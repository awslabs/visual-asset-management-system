/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH admin service Lambda, its eleven route registrations, and the deploy-time catalog
 * registration custom resource.
 *
 * The custom resource is asserted on its PROPERTIES rather than on its existence alone: the handler
 * parses the two JSON-string lists, re-runs on `contentHash`/`registrationRevision`, and re-runs on
 * every deploy through `Timestamp`. A resource that lost any one of those would still synthesize.
 *
 * The stock-template absence check carries its positive control in the same test: the stock
 * commercial template holds other custom resources, so "no registration resource" is distinguishable
 * from "no custom resources were emitted at all".
 */

import { API_GATEWAY_DEFAULT_TIMEOUT_SECONDS } from "../../../config/config";
import {
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    handlerOf,
    invokedFunction,
    lambdaByHandler,
    restApiPairs,
    synthTemplate,
} from "../../support/templateSynth";
import { EDH_STACK, enableEdh } from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs three of them.
jest.setTimeout(600_000);

const EDH_TEMPLATES: TemplateName[] = ["commercial", "govcloud"];

/** Strings identical to the admin members of `EDH_ROUTES` in apiRoutes.py. */
const ADMIN_PAIRS = [
    "GET /addon/edh/admin/images",
    "POST /addon/edh/admin/images/vdi",
    "POST /addon/edh/admin/images/simulation",
    "DELETE /addon/edh/admin/images/vdi/{imageId}",
    "DELETE /addon/edh/admin/images/simulation/{imageId}",
    "GET /addon/edh/admin/instances",
    "GET /addon/edh/admin/instances/stale",
    "POST /addon/edh/admin/instances/{sessionId}/stop",
    "POST /addon/edh/admin/instances/{sessionId}/deactivate",
    "GET /addon/edh/admin/capacity",
    "GET /addon/edh/admin/health",
    "GET /addon/edh/admin/settings",
    "PUT /addon/edh/admin/settings",
];

const ADMIN_HANDLER = "handlers.addon.edh.edhAdminService.lambda_handler";
const REGISTRATION_HANDLER = "handlers.addon.edh.edhCatalogRegistration.lambda_handler";

const VDI_IMAGE = {
    amiId: "ami-0123456789abcdef0",
    stackName: "t1-vdi-linux",
    baseOs: "amazonlinux2023",
    rootSize: 100,
    virtualDesktopProfileId: 1,
};

function withPredefinedImages(c: any) {
    enableEdh(c);
    const edh = c.app.addons.useEdhWorkstations;
    edh.predefinedImages.vdi = [VDI_IMAGE];
    edh.predefinedImages.simulationAmis = [];
    edh.predefinedImages.registrationRevision = 3;
}

function withLeasePolicy(c: any) {
    enableEdh(c);
    const edh = c.app.addons.useEdhWorkstations;
    edh.vdi.lease = { enabled: true, leaseHours: 12, forced: true, graceHours: 6 };
}

const adminSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: withPredefinedImages, mutateKey: "edhAdminRegistration" });

function registrationResources(s: SynthResult): Resource[] {
    return s
        .ofType("AWS::CloudFormation::CustomResource")
        .filter((r) => "predefinedVdiImages" in r.properties);
}

describe("EDH admin service", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the admin Lambda is in the VPC behind the add-on security group and fits the API Gateway integration timeout",
        (name) => {
            const fn = lambdaByHandler(adminSynth(name), ADMIN_HANDLER);
            expect(fn.stack.includes(EDH_STACK)).toBe(true);
            expect(fn.properties.Timeout).toBeLessThanOrEqual(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS);
            expect(fn.properties.VpcConfig).toBeDefined();
            expect(fn.properties.VpcConfig.SubnetIds.length).toBeGreaterThan(0);
            expect(SynthResult.flatten(fn.properties.VpcConfig.SecurityGroupIds)).toContain(
                "EdhAddonLambdaSecurityGroup"
            );
            // Set by globalLambdaEnvironmentsAndPermissions: proves the shared helpers ran on this Lambda.
            expect(fn.properties.Environment.Variables.VAMS_RESOURCE_PARAM_PREFIX).toBe(
                `/vams-t1-${name}/resourceNames`
            );
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: every admin method+path pair is registered on the REST API",
        (name) => {
            const pairs = restApiPairs(adminSynth(name));
            // Control: the Phase-1 surface is present too, so an empty body cannot pass this test.
            expect(pairs.has("GET /addon/edh/workstations")).toBe(true);
            const missing = ADMIN_PAIRS.filter((pair) => !pairs.has(pair));
            expect(missing).toEqual([]);
        }
    );
});

describe("EDH catalog registration custom resource", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the registration Lambda has the batch-profile timeout and VPC placement",
        (name) => {
            const fn = lambdaByHandler(adminSynth(name), REGISTRATION_HANDLER);
            expect(fn.properties.Timeout).toBe(900);
            expect(fn.properties.VpcConfig).toBeDefined();
            expect(SynthResult.flatten(fn.properties.VpcConfig.SecurityGroupIds)).toContain(
                "EdhAddonLambdaSecurityGroup"
            );
        }
    );

    test("exactly one registration resource carries the lists, the hash, the revision and a nonce", () => {
        const resources = registrationResources(adminSynth("commercial"));
        expect(resources).toHaveLength(1);
        const props = resources[0].properties;
        expect(JSON.parse(props.predefinedVdiImages)).toEqual([VDI_IMAGE]);
        expect(JSON.parse(props.predefinedSimulationAmis)).toEqual([]);
        // The job-definition list is always carried; it is empty while the containers feature is off.
        expect(JSON.parse(props.jobDefinitions)).toEqual([]);
        expect(props.contentHash).toMatch(/^[0-9a-f]{64}$/);
        expect(props.registrationRevision).toBe("3");
        expect(props.Timestamp).toMatch(/^\d{13}$/);
    });

    test("the content hash follows the lists and the revision", () => {
        const base = registrationResources(adminSynth("commercial"))[0].properties.contentHash;
        const bumped = synthTemplate("commercial", {
            mutate: (c: any) => {
                withPredefinedImages(c);
                c.app.addons.useEdhWorkstations.predefinedImages.registrationRevision = 4;
            },
            mutateKey: "edhAdminRegistrationRevision4",
        });
        expect(registrationResources(bumped)[0].properties.contentHash).not.toBe(base);
        expect(registrationResources(bumped)[0].properties.registrationRevision).toBe("4");
    });

    test("the resource is served by a provider whose handler is the registration Lambda, outside the VPC", () => {
        const s = adminSynth("commercial");
        const resource = registrationResources(s)[0];
        expect(JSON.stringify(resource.properties.ServiceToken)).toContain(
            "EdhCatalogRegistrationProvider"
        );
        const frameworkFns = s.where("AWS::Lambda::Function", (r) =>
            r.logicalId.includes("EdhCatalogRegistrationProviderframeworkonEvent")
        );
        expect(frameworkFns).toHaveLength(1);
        // The framework Lambda stays outside the VPC; only the handler it invokes is placed inside.
        expect(frameworkFns[0].properties.VpcConfig).toBeUndefined();
        const registrationFn = lambdaByHandler(s, REGISTRATION_HANDLER);
        expect(JSON.stringify(frameworkFns[0].properties.Environment.Variables)).toContain(
            registrationFn.logicalId
        );
    });

    test("the registration Lambda is not routed and the admin Lambda is", () => {
        const s = adminSynth("commercial");
        const routed = s
            .ofType("AWS::Lambda::Permission")
            .filter((p) => SynthResult.flatten(p.properties.Principal).includes("apigateway"))
            .map((p) => invokedFunction(s, p));
        expect(routed.length).toBeGreaterThan(0);
        // A permission the walk cannot resolve would leave its Lambda unclassified.
        expect(routed.filter((fn) => fn === undefined)).toEqual([]);
        const routedHandlers = routed.map((fn) => handlerOf(fn as Resource));
        expect(routedHandlers).toContain(ADMIN_HANDLER);
        expect(routedHandlers).not.toContain(REGISTRATION_HANDLER);
    });

    test("the warnings attribute is exported as a stack output", () => {
        const s = adminSynth("commercial");
        const outputs = Object.values(s.templates)
            .map((t: any) => t.Outputs?.EdhCatalogRegistrationWarnings)
            .filter(Boolean);
        expect(outputs).toHaveLength(1);
        expect(JSON.stringify(outputs[0].Value)).toContain("warnings");
    });

    test("the lease policy defaults are passed from the configured values", () => {
        const s = synthTemplate("commercial", {
            mutate: withLeasePolicy,
            mutateKey: "edhLeasePolicy",
        });
        const resources = registrationResources(s);
        const props = resources[0].properties;
        const lease = JSON.parse(props.leasePolicyDefaults);
        expect(lease).toEqual({
            enabled: true,
            leaseHours: 12,
            forced: true,
            graceHours: 6,
        });
    });
});

describe("the stock templates emit no admin service and no registration resource", () => {
    test.each(EDH_TEMPLATES)("%s", (name) => {
        const stock = synthTemplate(name);
        expectAbsent(
            `the EDH catalog registration resource in the stock ${name} template`,
            registrationResources(stock),
            {
                description: `custom resources in the stock ${name} template`,
                count: stock.countOfType("AWS::CloudFormation::CustomResource"),
            }
        );
        expectAbsent(
            `the EDH admin Lambda in the stock ${name} template`,
            stock.where("AWS::Lambda::Function", (r) => handlerOf(r) === ADMIN_HANDLER),
            {
                description: `the ${name} hybrid emits the admin Lambda`,
                count: adminSynth(name).where(
                    "AWS::Lambda::Function",
                    (r) => handlerOf(r) === ADMIN_HANDLER
                ).length,
            }
        );
    });
});
