/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The interface endpoints the EDH Workstations add-on adds to the VAMS VPC.
 *
 * `addonInterfaceEndpoints()` lists them and the VPC builder creates each one under its id, so an id
 * is the endpoint's construct id in the VPC stack, and a changed id replaces the live endpoint. What
 * is pinned here:
 *   * the add-on adds the EC2 endpoint whenever it is enabled (the capacity reservation reads and the
 *     launch worker call Amazon EC2, and the root key sync when it runs in the VPC), and no AWS KMS endpoint: the root key
 *     sync decrypts with its own per-run key and calls no AWS KMS (review S-10); a KMS endpoint the
 *     customer managed key block creates is the core's;
 *   * the containers feature adds the Batch endpoint, and the AMI builds feature the Image Builder,
 *     Systems Manager messages and EC2 messages endpoints, each under the id it deploys with;
 *   * the VPC stack emits each one once, with a logical id built from that id and the service it
 *     names;
 *   * with a pipeline on that already creates the Batch endpoint, the VPC still has one;
 *   * with the add-on off, the VPC has none of them;
 *   * the commercial and GovCloud templates each get one EC2 endpoint, and KMS endpoints only where the
 *     customer managed key block creates them (the EU Sovereign Cloud partition refuses the add-on in
 *     getConfig()).
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../../config/config";
import * as Service from "../../../lib/helper/service-helper";
import { addonInterfaceEndpoints } from "../../../lib/nestedStacks/addon/addonVpcEndpoints";
import { VPCBuilderNestedStack } from "../../../lib/nestedStacks/vpc/vpcBuilder-nestedStack";
import {
    ACCOUNT,
    SynthResult,
    TemplateName,
    buildTemplateConfig,
    expectAbsent,
} from "../../support/templateSynth";
import { newTestApp } from "../../support/testApp";
import { enableEdh, enableEdhAmiBuilds, enableEdhContainers } from "./support/edhSynth";

/** Endpoint id -> the service name suffix of the endpoint it creates. */
/** Whenever the add-on is enabled. */
const ADDON_ENDPOINTS: Record<string, string> = { EC2Endpoint: "ec2" };
const CONTAINER_ENDPOINTS: Record<string, string> = { BatchEndpoint: "batch" };
const AMI_BUILD_ENDPOINTS: Record<string, string> = {
    ImageBuilderEndpoint: "imagebuilder",
    SSMMessagesEndpoint: "ssmmessages",
    EC2MessagesEndpoint: "ec2messages",
};
const EDH_ENDPOINTS: Record<string, string> = {
    ...ADDON_ENDPOINTS,
    ...CONTAINER_ENDPOINTS,
    ...AMI_BUILD_ENDPOINTS,
};

/** The switches `addonInterfaceEndpoints` reads; nothing else in the config is consulted. */
const edhConfig = (
    enabled: boolean,
    containers: boolean,
    amiBuilds: boolean,
    rootKeySync: boolean | undefined = undefined,
    useFips = false
): Config.Config =>
    ({
        app: {
            useFips,
            addons: {
                useEdhWorkstations: {
                    enabled,
                    edhRootKeyAutoSync: rootKeySync,
                    containers: { enabled: containers },
                    amiBuilds: { enabled: amiBuilds },
                },
            },
        },
    } as unknown as Config.Config);

const idsOf = (config: Config.Config): string[] => addonInterfaceEndpoints(config).map((e) => e.id);

/** Both optional features on; `enableEdhAmiBuilds` resets containers, so it runs first. */
const withBothFeatures = (c: any): void => {
    enableEdhAmiBuilds(c);
    enableEdhContainers(c);
};

/** Every interface endpoint of the VPC stack built from a shipped template. */
const vpcEndpoints = (
    mutate: (c: any) => void,
    template: TemplateName = "commercial"
): Array<{ logicalId: string; service: string }> => {
    const config = buildTemplateConfig(template, (c: any) => {
        mutate(c);
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
    });
    Service.SetConfig(config);
    const app = newTestApp();
    const root = new cdk.Stack(app, "EdhVpcEndpointsTest", {
        env: { account: ACCOUNT, region: config.env.region },
    });
    const vpcStack = new VPCBuilderNestedStack(root, "VPCBuilder", { config });
    return Object.entries<any>(Template.fromStack(vpcStack).findResources("AWS::EC2::VPCEndpoint"))
        .filter(([, r]) => r.Properties.VpcEndpointType === "Interface")
        .map(([logicalId, r]) => ({
            logicalId,
            service: SynthResult.flatten(r.Properties.ServiceName),
        }));
};

const endsWithService = (service: string, suffix: string): boolean =>
    service.endsWith(`.${suffix}`);

describe("addonInterfaceEndpoints", () => {
    test("adds the EC2 endpoint with the add-on on", () => {
        const endpoints = addonInterfaceEndpoints(edhConfig(true, false, false));
        expect(endpoints.map((e) => e.id)).toEqual(Object.keys(ADDON_ENDPOINTS));
        expect(endpoints.map((e) => e.service)).toEqual([ec2.InterfaceVpcEndpointAwsService.EC2]);
    });

    test.each([
        ["the sync unset", undefined, false],
        ["the sync on", true, false],
        ["the sync on under useFips", true, true],
        ["the sync off", false, true],
    ])("S-10: adds no KMS endpoint with %s", (_case, rootKeySync, useFips) => {
        const ids = idsOf(edhConfig(true, false, false, rootKeySync, useFips));
        expect(ids).toEqual(Object.keys(ADDON_ENDPOINTS));
        expect(ids.filter((id) => /^KMS/.test(id))).toEqual([]);
    });

    test("adds the Batch endpoint for the containers feature", () => {
        const endpoints = addonInterfaceEndpoints(edhConfig(true, true, false, false));
        expect(endpoints.map((e) => e.id)).toEqual([
            ...Object.keys(ADDON_ENDPOINTS),
            ...Object.keys(CONTAINER_ENDPOINTS),
        ]);
        expect(endpoints[1].service).toBe(ec2.InterfaceVpcEndpointAwsService.BATCH);
    });

    test("adds the three build endpoints for the AMI builds feature, and the EC2 endpoint once", () => {
        const endpoints = addonInterfaceEndpoints(edhConfig(true, false, true, false));
        expect(endpoints.map((e) => e.id)).toEqual([
            ...Object.keys(ADDON_ENDPOINTS),
            ...Object.keys(AMI_BUILD_ENDPOINTS),
        ]);
        expect(endpoints.slice(1).map((e) => e.service)).toEqual([
            ec2.InterfaceVpcEndpointAwsService.IMAGE_BUILDER,
            ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
            ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
        ]);
    });

    test("adds all five with both features and the sync on, each id once", () => {
        const ids = idsOf(edhConfig(true, true, true));
        expect(ids).toEqual(Object.keys(EDH_ENDPOINTS));
        expect(new Set(ids).size).toBe(ids.length);
    });

    test.each([
        ["the add-on and both features are off", false, false, false],
        ["AMI builds are on without the add-on", false, false, true],
        ["containers are on without the add-on", false, true, false],
    ])("adds none when %s", (_case, enabled, containers, amiBuilds) => {
        expect(idsOf(edhConfig(enabled, containers, amiBuilds))).toEqual([]);
    });
});

describe("the VPC stack with the EDH endpoints", () => {
    const both = vpcEndpoints(withBothFeatures);

    test.each(Object.entries(EDH_ENDPOINTS))(
        "emits %s once, with a logical id built from that id, for the %s service",
        (id, suffix) => {
            const matching = both.filter((e) => new RegExp(`^${id}[0-9A-F]{8}$`).test(e.logicalId));
            expect(matching).toHaveLength(1);
            expect(endsWithService(matching[0].service, suffix)).toBe(true);
            expect(both.filter((e) => endsWithService(e.service, suffix))).toHaveLength(1);
        }
    );

    test("with a pipeline that creates the Batch endpoint, the VPC still has one", () => {
        const enablePipeline = (c: any) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
        };
        // Control: the pipeline block creates the Batch endpoint by itself.
        const pipelineOnly = vpcEndpoints(enablePipeline);
        expect(pipelineOnly.filter((e) => endsWithService(e.service, "batch"))).toHaveLength(1);
        const bothWithPipeline = (c: any) => {
            withBothFeatures(c);
            enablePipeline(c);
        };
        // Control: the add-on asks for the Batch endpoint in this configuration as well.
        expect(idsOf(buildTemplateConfig("commercial", bothWithPipeline))).toEqual(
            Object.keys(EDH_ENDPOINTS)
        );
        const withPipeline = vpcEndpoints(bothWithPipeline);
        const batch = withPipeline.filter((e) => endsWithService(e.service, "batch"));
        expect(batch).toHaveLength(1);
        expect(batch[0].logicalId).toMatch(/^BatchEndpoint[0-9A-F]{8}$/);
    });

    test.each([
        // Commercial: no customer managed key, so no KMS endpoint at all; the add-on adds EC2.
        ["commercial", { ec2: 1, kms: 0, "kms-fips": 0 }],
        // GovCloud: the customer managed key block creates KMS and KMS FIPS (useFips is on); the add-on
        // adds EC2 and nothing for KMS.
        ["govcloud", { ec2: 1, kms: 1, "kms-fips": 1 }],
    ] as Array<[TemplateName, Record<string, number>]>)(
        "%s: the add-on with the root key sync on gets one EC2 endpoint, and KMS only from the core block",
        (template, counts) => {
            const endpoints = vpcEndpoints((c: any) => enableEdh(c), template);
            for (const [suffix, count] of Object.entries(counts)) {
                expect([
                    suffix,
                    endpoints.filter((e) => endsWithService(e.service, suffix)).length,
                ]).toEqual([suffix, count]);
            }
            for (const e of endpoints.filter((x) => /\.kms(-fips)?$/.test(x.service))) {
                expect(e.logicalId).toMatch(/^KMSEndpoint(FIPS)?[0-9A-F]{8}$/);
            }
        }
    );

    test("S-10: commercial with useFips and the sync on: no KMS endpoint", () => {
        const endpoints = vpcEndpoints((c: any) => {
            enableEdh(c);
            c.app.useFips = true;
        });
        expect(endpoints.filter((e) => /\.kms(-fips)?$/.test(e.service))).toEqual([]);
        expect(endpoints.filter((e) => endsWithService(e.service, "ec2"))).toHaveLength(1);
    });

    test("the root key sync on or off adds or removes no interface endpoint", () => {
        const on = vpcEndpoints((c: any) => enableEdh(c));
        const off = vpcEndpoints((c: any) => {
            enableEdh(c);
            c.app.addons.useEdhWorkstations.edhRootKeyAutoSync = false;
        });
        expect(off.map((e) => e.service).sort()).toEqual(on.map((e) => e.service).sort());
    });

    test("with the add-on off, the VPC has none of the EDH endpoints", () => {
        const stock = vpcEndpoints(() => undefined);
        const found = stock.filter((e) =>
            Object.values(EDH_ENDPOINTS).some((suffix) => endsWithService(e.service, suffix))
        );
        expectAbsent("EDH interface endpoints with the add-on off", found, {
            description: "the stock VPC stack emits interface endpoints at all",
            count: stock.length,
        });
    });
});
