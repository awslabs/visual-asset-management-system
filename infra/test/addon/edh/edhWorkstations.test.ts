/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 assertions for the EDH Workstations add-on stack.
 *
 * The shipped templates all set `app.addons.useEdhWorkstations.enabled: false`, so nothing under
 * `AddonBuilder/EdhWorkstationsBuilderNestedStack` is emitted from a shipped config and CI's plain
 * `cdk synth` never sees it. The `enableEdh` hybrid is therefore the only synth this stack gets. The
 * add-on supports commercial AWS and GovCloud, so both templates are asserted; EU Sovereign is excluded
 * by the config partition rule and is not synthesized with the add-on on.
 *
 * What is pinned here:
 *   * every `handlers.addon.edh.*` Lambda names a module that exists on disk — the source-scan gate in
 *     `security/lambdaBuilderHandlerModules.test.ts` covers the builder file, this covers what the
 *     stack actually wires;
 *   * the Lambdas are in the VPC because the ADD-ON is enabled, not because
 *     `useGlobalVpc.useForAllLambdas` is — the templates leave that false, and the AddonBuilder
 *     placeholder Lambda staying out of the VPC is the control that proves it;
 *   * a Lambda is API-facing exactly when the REST API holds an invoke permission on it; every API-facing
 *     Lambda finishes inside the API Gateway integration timeout, so a request the gateway has already
 *     answered with 504 does not keep running against EDH, and every other Lambda (the launch worker; the
 *     scheduled and custom-resource handlers later phases add) is given the longer timeout its job needs;
 *   * the security group the EDH ALB rule will name permits HTTPS egress only: 443, plus the port the
 *     configured endpoint names when it is not 443;
 *   * the Secrets Manager read names the configured root-key ARN — plus the CA-bundle ARN when one is
 *     configured — and nothing wider;
 *   * the session table is granted to the handlers that open it and to no other add-on Lambda;
 *   * the session and AMI recipe table names are published by the add-on stack under its own
 *     `addon/dynamoTables/` keys, and every Lambda that opens a table is created after both;
 *   * the AMI builds feature carries its own resources — the recipe service and the registrar, the build
 *     instance profile and its egress-only security group, the Image Builder completion rule and the
 *     sweep schedule, the seeded recipe bundles on the registration resource, each recipe asset tree
 *     uploaded with the exclusions the bundle hash walker reads, and Image Builder grants bounded to
 *     the deployment prefix and the recipe tag — and emits none of them with the feature off;
 *   * the AMI recipe table is written by the build Lambdas and read, never written, by the
 *     workstation service for its launch catalog; no add-on Lambda holds a grant on it with the
 *     feature off;
 *   * the REST API paths under the regional web ACL's AMI recipe exemption prefix are exactly the
 *     recipe routes the add-on stack registers.
 */

import * as fs from "fs";
import * as path from "path";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import {
    API_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    API_GATEWAY_STAGE_NAME,
} from "../../../config/config";
import { EDH_SHIPPED_AMI_RECIPE_DIRS } from "../../../config/addon/edhWorkstationsConfig";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../../common/addonResourceParamKeys";
import commercialTemplate from "../../../config/config.template.commercial.json";
import { enableRouteContributingAddons } from "../support/addonTestHooks";
import {
    ASSET_EXCLUDE_GLOBS,
    CREDENTIAL_EXCLUDE_GLOBS,
} from "../../../lib/nestedStacks/addon/edhWorkstations/constructs/edhAmiBuild-construct";
import { buildEdhWafRuleExclusions } from "../../../lib/nestedStacks/addon/edhWorkstations/edhWafExclusions";
import {
    ACCOUNT,
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    buildTemplateConfig,
    expectAbsent,
    handlerOf,
    invokedFunction,
    lambdaByHandler,
    resolveReference,
    resourcesOf,
    restApiPairs,
    statementsOf,
    synthTemplate,
} from "../../support/templateSynth";
import {
    EDH_STACK,
    edhFunctions,
    enableEdh,
    enableEdhAmiBuilds,
    inEdhStack,
} from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs eleven of them (two templates, stock + hybrid, and the
// commercial hybrid on a non-default EDH port, on an explicit :443, with a CA bundle, with AMI
// builds on, with AMI builds on and a non-default session secret prefix, and with VPC endpoints on
// both with and without AMI builds).
jest.setTimeout(600_000);

const EDH_TEMPLATES: TemplateName[] = ["commercial", "govcloud"];
const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..");
const BACKEND_ROOT = path.join(REPO_ROOT, "backend", "backend");

const edhSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" });

/** The add-on hybrid with the EDH endpoint on a port other than 443. */
const EDH_PORT_ENDPOINT = "https://edh-alb.internal.example.com:8443";
const enableEdhOnPort = (c: Parameters<typeof enableEdh>[0]): void => {
    enableEdh(c);
    c.app.addons.useEdhWorkstations.edhApiEndpoint = EDH_PORT_ENDPOINT;
};
const edhPortSynth = (): SynthResult =>
    synthTemplate("commercial", { mutate: enableEdhOnPort, mutateKey: "edhWorkstationsPort8443" });

/** The add-on hybrid with the EDH endpoint naming the default HTTPS port explicitly. */
const EDH_DEFAULT_PORT_ENDPOINT = "https://edh-alb.internal.example.com:443";
const enableEdhOnDefaultPort = (c: Parameters<typeof enableEdh>[0]): void => {
    enableEdh(c);
    c.app.addons.useEdhWorkstations.edhApiEndpoint = EDH_DEFAULT_PORT_ENDPOINT;
};
const edhDefaultPortSynth = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: enableEdhOnDefaultPort,
        mutateKey: "edhWorkstationsPort443",
    });

/**
 * The add-on hybrid with a private-CA bundle secret configured beside the root key, and the name the
 * ALB certificate is verified against (the one the EDH installer's self-signed certificate carries).
 */
const caBundleArn = (s: SynthResult): string =>
    `arn:${s.partition}:secretsmanager:${s.region}:${ACCOUNT}:secret:edh-ca-bundle-GhIjKl`;
const EDH_TLS_SERVER_NAME = "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE";
const enableEdhWithCaBundle = (c: Parameters<typeof enableEdh>[0]): void => {
    enableEdh(c);
    c.app.addons.useEdhWorkstations.edhApiCaBundleSecretArn = `arn:${c.env.partition}:secretsmanager:${c.env.region}:${ACCOUNT}:secret:edh-ca-bundle-GhIjKl`;
    c.app.addons.useEdhWorkstations.edhApiTlsServerName = EDH_TLS_SERVER_NAME;
};
const edhCaBundleSynth = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: enableEdhWithCaBundle,
        mutateKey: "edhWorkstationsCaBundle",
    });

/** `handlers.addon.edh.<module>.lambda_handler` -> `<module>`. */
const moduleOf = (r: Resource): string => handlerOf(r).split(".")[3];

/** `handlers.X.Y.<entrypoint>` -> `backend/backend/handlers/X/Y.py`. */
function handlerModulePath(handler: string): string {
    const segments = handler.split(".");
    segments.pop();
    return path.join(BACKEND_ROOT, ...segments) + ".py";
}

/** The root-key secret ARN `enableEdh` configures, in the template's own partition and region. */
const rootKeyArn = (s: SynthResult): string =>
    `arn:${s.partition}:secretsmanager:${s.region}:${ACCOUNT}:secret:edh-root-AbCdEf`;

/** Every IAM statement in the add-on's nested template, from inline and managed policies alike. */
function edhPolicyStatements(s: SynthResult): any[] {
    const inStack = (r: Resource) => r.stack.includes(EDH_STACK);
    return [
        ...s.where("AWS::IAM::Policy", inStack),
        ...s.where("AWS::IAM::ManagedPolicy", inStack),
    ].flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
}

describe("the stock templates emit no EDH resources", () => {
    test.each(EDH_TEMPLATES)("%s: no handlers.addon.edh.* Lambda", (name) => {
        const stock = synthTemplate(name);
        const enabled = edhSynth(name);
        expectAbsent(
            `EDH Lambdas in the stock ${name} template`,
            edhFunctions(stock).map((r) => `${r.stack}/${r.logicalId}`),
            {
                description: `the ${name} hybrid emits handlers.addon.edh.* Lambdas`,
                count: edhFunctions(enabled).length,
            }
        );
    });
});

describe("EDH add-on Lambdas", () => {
    test.each(EDH_TEMPLATES)("%s: every handler string resolves to a backend module", (name) => {
        const fns = edhFunctions(edhSynth(name));
        // Control for the resolution negative below, and the one module this stack must always wire.
        expect(fns.length).toBeGreaterThan(0);
        expect(fns.map(handlerOf)).toContain(
            "handlers.addon.edh.edhWorkstationService.lambda_handler"
        );
        const unresolvable = fns
            .map(handlerOf)
            .filter((handler) => !fs.existsSync(handlerModulePath(handler)));
        expect(unresolvable).toEqual([]);
    });

    test.each(EDH_TEMPLATES)(
        "%s: placed in the VPC behind the add-on security group while useForAllLambdas is false",
        (name) => {
            const s = edhSynth(name);
            const fns = edhFunctions(s);
            // Without this the loop below is satisfied by an empty set.
            expect(fns.length).toBeGreaterThan(0);
            for (const fn of fns) {
                const vpcConfig = fn.properties.VpcConfig;
                expect(vpcConfig).toBeDefined();
                expect(vpcConfig.SubnetIds.length).toBeGreaterThan(0);
                // Each subnet id is a parameter fed from the VPC template two nestings up; resolved
                // to its AWS::EC2::Subnet, the CDK type tag must say Isolated.
                for (const subnetId of vpcConfig.SubnetIds) {
                    const subnet = resolveReference(s, fn.stack, subnetId);
                    if (subnet === undefined) {
                        throw new Error(
                            `${fn.logicalId}: subnet id ${JSON.stringify(
                                subnetId
                            )} resolves to no resource`
                        );
                    }
                    expect(subnet.type).toBe("AWS::EC2::Subnet");
                    expect(subnet.properties.Tags).toEqual(
                        expect.arrayContaining([{ Key: "aws-cdk:subnet-type", Value: "Isolated" }])
                    );
                }
                expect(SynthResult.flatten(vpcConfig.SecurityGroupIds)).toContain(
                    "EdhAddonLambdaSecurityGroup"
                );
            }
            // Control: the AddonBuilder placeholder Lambda follows the useForAllLambdas rule alone. It
            // staying OUT of the VPC proves the flag is false in this synth, so the placement above is
            // the feature-driven one and not a side effect of a global setting.
            const placeholder = s.where("AWS::Lambda::Function", (r) =>
                r.logicalId.startsWith("AddonNestedStackEmptyLambda")
            );
            expect(placeholder).toHaveLength(1);
            expect(placeholder[0].properties.VpcConfig).toBeUndefined();
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the service runs inside the gateway timeout and the launch worker for six minutes",
        (name) => {
            const fns = edhFunctions(edhSynth(name));
            const timeoutOf = (module: string) =>
                fns.find((r) => handlerOf(r) === `handlers.addon.edh.${module}.lambda_handler`)
                    ?.properties.Timeout;
            expect(timeoutOf("edhWorkstationService")).toBe(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS);
            // The worker's start of a stopped desktop reads EDH's answer for up to 240 s, then the
            // desktop's state every 15 s for what is left of the start's window.
            expect(timeoutOf("edhLaunchWorker")).toBe(360);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the launch worker reads instances on every resource with a reasoned suppression, and may stop, start or terminate none",
        (name) => {
            const s = edhSynth(name);
            const fns = edhFunctions(s);
            const [worker] = fns.filter(
                (r) => handlerOf(r) === "handlers.addon.edh.edhLaunchWorker.lambda_handler"
            );
            expect(worker).toBeDefined();
            const allowedOn = (fn: Resource) =>
                statementsOf(s, fn).filter((st) => st.Effect === "Allow");
            // A refused start names the Availability Zone of the stopped desktop's instance.
            const describe = allowedOn(worker).filter((st) =>
                actionsOf(st).includes("ec2:DescribeInstances")
            );
            expect(describe.length).toBeGreaterThan(0);
            expect([...new Set(describe.flatMap(resourcesOf))]).toEqual(["*"]);
            const covers = (pattern: string, action: string) =>
                new RegExp(`^${pattern.replace(/\*/g, ".*").replace(/\?/g, ".")}$`, "i").test(
                    action
                );
            const lifecycle = allowedOn(worker)
                .flatMap(actionsOf)
                .filter((a) =>
                    ["ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances"].some(
                        (x) => covers(a, x)
                    )
                );
            expect(lifecycle).toEqual([]);
            // Neither API-facing workstation role reads instances: the start runs in the worker.
            for (const module of ["edhWorkstationService", "edhAdminService"]) {
                const [fn] = fns.filter(
                    (r) => handlerOf(r) === `handlers.addon.edh.${module}.lambda_handler`
                );
                expect(
                    allowedOn(fn).some((st) => actionsOf(st).includes("ec2:DescribeInstances"))
                ).toBe(false);
            }
            const reasons = (
                (worker.raw.Metadata?.cdk_nag?.rules_to_suppress ?? []) as Array<{ reason: string }>
            ).map((r) => r.reason);
            expect(
                reasons.some((r) => r.startsWith("ec2:DescribeInstances accepts no resource ARN"))
            ).toBe(true);
        }
    );

    test.each(EDH_TEMPLATES)("%s: the workstation service carries its configuration", (name) => {
        const s = edhSynth(name);
        const [fn] = edhFunctions(s).filter(
            (r) => handlerOf(r) === "handlers.addon.edh.edhWorkstationService.lambda_handler"
        );
        expect(fn).toBeDefined();
        const env = fn.properties.Environment.Variables;
        expect(env.EDH_API_ENDPOINT).toBe("https://internal-edh.example.internal");
        expect(SynthResult.flatten(env.EDH_ADMIN_KEY_SECRET_ARN)).toBe(rootKeyArn(s));
        expect(env.EDH_CA_BUNDLE_SECRET_ARN).toBe("");
        expect(env.EDH_API_TLS_SERVER_NAME).toBe("");
        expect(env.EDH_CLUSTER_ID).toBe("edh-t1");
        expect(env.EDH_AUTO_PROVISION_USERS).toBe("true");
        expect(JSON.parse(env.EDH_ALLOWED_INSTANCE_TYPES)).toEqual(["g5.xlarge", "g5.2xlarge"]);
        expect(env.EDH_VDI_DEFAULT_PROJECT).toBe("default");
        expect(env.EDH_VDI_PROFILE_ID).toBe("1");
        // Set by globalLambdaEnvironmentsAndPermissions: proves the shared helpers ran on this Lambda.
        expect(env.VAMS_RESOURCE_PARAM_PREFIX).toBe(`/vams-t1-${name}/resourceNames`);
    });

    test.each(EDH_TEMPLATES)(
        "%s: the launch worker is retried twice and its events expire after twenty-three minutes",
        (name) => {
            const s = edhSynth(name);
            const [worker] = edhFunctions(s).filter(
                (r) => handlerOf(r) === "handlers.addon.edh.edhLaunchWorker.lambda_handler"
            );
            expect(worker).toBeDefined();
            const configs = s.where(
                "AWS::Lambda::EventInvokeConfig",
                (r) =>
                    r.stack.includes(EDH_STACK) &&
                    SynthResult.flatten(r.properties.FunctionName).includes(worker.logicalId)
            );
            expect(configs).toHaveLength(1);
            expect(configs[0].properties.MaximumRetryAttempts).toBe(2);
            // Three attempts that each run to the six-minute worker timeout, plus Lambda's one- and
            // two-minute retry pauses, fit inside the age with margin.
            expect(configs[0].properties.MaximumEventAgeInSeconds).toBe(1380);
            expect(configs[0].properties.Qualifier).toBe("$LATEST");
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the workstation service names the launch worker and may invoke it",
        (name) => {
            const s = edhSynth(name);
            const fns = edhFunctions(s);
            const [service] = fns.filter(
                (r) => handlerOf(r) === "handlers.addon.edh.edhWorkstationService.lambda_handler"
            );
            const [worker] = fns.filter(
                (r) => handlerOf(r) === "handlers.addon.edh.edhLaunchWorker.lambda_handler"
            );
            expect(service).toBeDefined();
            expect(worker).toBeDefined();
            const env = service.properties.Environment.Variables;
            // The worker's name is a Ref to its own resource; the worker itself is not told about itself.
            expect(SynthResult.flatten(env.EDH_LAUNCH_WORKER_FUNCTION_NAME)).toBe(
                `\${${worker.logicalId}}`
            );
            expect(
                worker.properties.Environment.Variables.EDH_LAUNCH_WORKER_FUNCTION_NAME
            ).toBeUndefined();

            // `Role` is Fn::GetAtt [<roleLogicalId>, Arn]; flatten gives `${<roleLogicalId>.Arn}`.
            const roleOf = (fn: Resource) =>
                SynthResult.flatten(fn.properties.Role).replace(/^\$\{|\.Arn\}$/g, "");
            const invokeResources = (roleId: string) =>
                s
                    .where(
                        "AWS::IAM::Policy",
                        (r) =>
                            r.stack.includes(EDH_STACK) &&
                            SynthResult.flatten(r.properties.Roles).includes(roleId)
                    )
                    .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[])
                    .filter((st) => actionsOf(st).includes("lambda:InvokeFunction"))
                    .flatMap((st) =>
                        ([] as unknown[])
                            .concat(st.Resource ?? [])
                            .map((r) => SynthResult.flatten(r))
                    );
            // The session bootstrap's cross-call grant to the API key service sits beside the invoke
            // grant on the launch worker; edhSessionBootstrap.test.ts bounds the cross-call, so only
            // the worker invoke is pinned here.
            const workerInvoke = invokeResources(roleOf(service)).filter(
                (res) => !/apiKeyService/.test(res)
            );
            expect(workerInvoke.sort()).toEqual([
                `\${${worker.logicalId}.Arn}`,
                `\${${worker.logicalId}.Arn}:*`,
            ]);
            // Control: the worker invokes no Lambda of its own.
            expect(invokeResources(roleOf(worker))).toEqual([]);
        }
    );
});

describe("EDH add-on IAM grants", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the Secrets Manager read is scoped to the root-key ARN",
        (name) => {
            const s = edhSynth(name);
            const secretReads = edhPolicyStatements(s).filter((st) =>
                actionsOf(st).includes("secretsmanager:GetSecretValue")
            );
            expect(secretReads.length).toBeGreaterThan(0);
            for (const st of secretReads) {
                const resources = ([] as unknown[])
                    .concat(st.Resource ?? [])
                    .map((r) => SynthResult.flatten(r));
                expect(resources).toEqual([rootKeyArn(s)]);
            }
        }
    );

    test("a configured CA bundle is read beside the root key, by its exact ARN, and the configured certificate server name reaches every handler Lambda", () => {
        const s = edhCaBundleSynth();
        const fns = edhFunctions(s);
        expect(fns.length).toBeGreaterThan(0);
        for (const fn of fns) {
            expect(fn.properties.Environment.Variables.EDH_CA_BUNDLE_SECRET_ARN).toBe(
                caBundleArn(s)
            );
            expect(fn.properties.Environment.Variables.EDH_API_TLS_SERVER_NAME).toBe(
                EDH_TLS_SERVER_NAME
            );
            // minimizePolicies may fold the two reads into one statement or keep them apart; the
            // set of secrets read is what is pinned.
            const read = statementsOf(s, fn)
                .filter((st) => actionsOf(st).includes("secretsmanager:GetSecretValue"))
                .flatMap(resourcesOf)
                .sort();
            expect(read).toEqual([caBundleArn(s), rootKeyArn(s)].sort());
        }
    });

    test.each(EDH_TEMPLATES)(
        "%s: the session table is granted read/write to the handlers that need it",
        (name) => {
            const s = edhSynth(name);
            const fns = edhFunctions(s);
            const sessionTableStatements = (fn: Resource) =>
                statementsOf(s, fn).filter(
                    (st) =>
                        actionsOf(st).some((a) => a.startsWith("dynamodb:")) &&
                        /EdhSessionStorage/i.test(resourcesOf(st).join(" "))
                );
            const granted = fns.filter((fn) => sessionTableStatements(fn).length > 0);
            // The registrar writes the session table once to seed the reserved SETTINGS#lease row
            // (a conditional put); the four handlers read and write session rows.
            expect(granted.map(moduleOf).sort()).toEqual([
                "edhAdminService",
                "edhCatalogRegistration",
                "edhLaunchWorker",
                "edhSessionReconciler",
                "edhWorkstationService",
            ]);
            for (const fn of granted) {
                expect(sessionTableStatements(fn).flatMap(actionsOf)).toEqual(
                    expect.arrayContaining([
                        "dynamodb:Query",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                    ])
                );
            }
        }
    );
});

describe("EdhAddonLambdaSecurityGroup", () => {
    test.each(EDH_TEMPLATES)(
        "%s: is the add-on's only security group and egresses on 443 only",
        (name) => {
            const sgs = edhSynth(name).where("AWS::EC2::SecurityGroup", (r) =>
                r.stack.includes(EDH_STACK)
            );
            expect(sgs).toHaveLength(1);
            const [sg] = sgs;
            expect(sg.logicalId).toMatch(/^EdhAddonLambdaSecurityGroup/);
            expect(sg.properties.SecurityGroupIngress).toBeUndefined();
            expect(sg.properties.SecurityGroupEgress).toEqual([
                expect.objectContaining({
                    CidrIp: "0.0.0.0/0",
                    IpProtocol: "tcp",
                    FromPort: 443,
                    ToPort: 443,
                    Description: "Allow HTTPS to AWS service endpoints and to the EDH ALB on 443",
                }),
            ]);
        }
    );

    test("an endpoint on another port adds one egress rule for that port beside 443", () => {
        const s = edhPortSynth();
        const sgs = s.where("AWS::EC2::SecurityGroup", (r) => r.stack.includes(EDH_STACK));
        expect(sgs).toHaveLength(1);
        type EgressRule = { CidrIp: string; IpProtocol: string; FromPort: number; ToPort: number };
        const egress = (sgs[0].properties.SecurityGroupEgress as EgressRule[])
            .map(({ CidrIp, IpProtocol, FromPort, ToPort }) => ({
                CidrIp,
                IpProtocol,
                FromPort,
                ToPort,
            }))
            .sort((a, b) => a.FromPort - b.FromPort);
        expect(egress).toEqual([
            { CidrIp: "0.0.0.0/0", IpProtocol: "tcp", FromPort: 443, ToPort: 443 },
            { CidrIp: "0.0.0.0/0", IpProtocol: "tcp", FromPort: 8443, ToPort: 8443 },
        ]);
        // The handlers are told the same endpoint the rule was derived from.
        const [service] = edhFunctions(s).filter(
            (r) => handlerOf(r) === "handlers.addon.edh.edhWorkstationService.lambda_handler"
        );
        expect(service.properties.Environment.Variables.EDH_API_ENDPOINT).toBe(EDH_PORT_ENDPOINT);
    });

    test("an endpoint naming :443 explicitly keeps the single 443 egress rule", () => {
        // URL.port normalises the default port to "", so the endpoint's port resolves to 443 and the
        // inline 443 rule covers it; a second entry for the same port and peer would fail CREATE with
        // InvalidPermission.Duplicate.
        const sgs = edhDefaultPortSynth().where("AWS::EC2::SecurityGroup", (r) =>
            r.stack.includes(EDH_STACK)
        );
        expect(sgs).toHaveLength(1);
        expect(sgs[0].properties.SecurityGroupEgress).toHaveLength(1);
        expect(sgs[0].properties.SecurityGroupEgress[0]).toEqual(
            expect.objectContaining({ FromPort: 443, ToPort: 443 })
        );
    });
});

/** The routes the workstation service backs; strings identical to the `EDH_ROUTES` in apiRoutes.py. */
const WORKSTATION_PAIRS = [
    "GET /addon/edh/workstations",
    "POST /addon/edh/workstations",
    "GET /addon/edh/workstations/{sessionId}",
    "DELETE /addon/edh/workstations/{sessionId}",
    "POST /addon/edh/workstations/{sessionId}/stop",
    "POST /addon/edh/workstations/{sessionId}/start",
    "POST /addon/edh/workstations/{sessionId}/renew",
    "GET /addon/edh/workstations/{sessionId}/connect",
    "GET /addon/edh/workstations/{sessionId}/windows-sign-in",
    "GET /addon/edh/catalog/images",
    "GET /addon/edh/catalog/instance-types",
    "GET /addon/edh/catalog/lease",
    "GET /addon/edh/catalog/capacity-reservations",
];

describe("EDH add-on routes", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the hybrid registers every workstation pair and the stock template none",
        (name) => {
            const enabled = restApiPairs(edhSynth(name));
            const missing = WORKSTATION_PAIRS.filter((pair) => !enabled.has(pair));
            expect(missing).toEqual([]);

            const isEdh = (pair: string) => pair.includes(" /addon/edh/");
            expectAbsent(
                `/addon/edh routes in the stock ${name} template`,
                [...restApiPairs(synthTemplate(name))].filter(isEdh),
                {
                    description: `the ${name} hybrid registers /addon/edh routes`,
                    count: [...enabled].filter(isEdh).length,
                }
            );
        }
    );

    test("the route parity hook turns on the add-on and both features that add routes", () => {
        // apiRouteBackendCdkParity.test.ts synthesizes through enableRouteContributingAddons and
        // requires every /addon/edh/* route the backend declares; this pins that the hook still
        // enables the container and AMI recipe routes, not only the workstation ones.
        const c = JSON.parse(JSON.stringify(commercialTemplate));
        const edh = c.app.addons.useEdhWorkstations;
        expect([edh.enabled, edh.containers.enabled, edh.amiBuilds.enabled]).toEqual([
            false,
            false,
            false,
        ]);
        enableRouteContributingAddons(c);
        expect([edh.enabled, edh.containers.enabled, edh.amiBuilds.enabled]).toEqual([
            true,
            true,
            true,
        ]);
    });
});

describe("API-facing EDH Lambdas", () => {
    test.each(EDH_TEMPLATES)(
        "%s: every Lambda API Gateway may invoke finishes inside the integration timeout, and no other is bounded by it",
        (name) => {
            const s = edhSynth(name);
            const fns = edhFunctions(s);
            expect(fns.length).toBeGreaterThan(0);
            // The REST API grants itself invoke on each routed Lambda with a Permission in the API
            // template; resolving each one to its function is what classifies a Lambda as API-facing.
            const invoked = s
                .where("AWS::Lambda::Permission", (r) =>
                    SynthResult.flatten(r.properties.Principal).includes("apigateway")
                )
                .map((r) => invokedFunction(s, r));
            expect(invoked.length).toBeGreaterThan(0);
            // A permission the walk cannot resolve would leave its Lambda unclassified and unbounded.
            expect(invoked.filter((fn) => fn === undefined)).toEqual([]);
            const isInvoked = (r: Resource) =>
                invoked.some((fn) => fn?.stack === r.stack && fn.logicalId === r.logicalId);
            const apiFacing = fns.filter(isInvoked);
            const others = fns.filter((r) => !isInvoked(r));
            // Controls: the classification separates an API-facing Lambda from an asynchronous one.
            expect(apiFacing.map(moduleOf)).toContain("edhWorkstationService");
            expect(others.map(moduleOf)).toContain("edhLaunchWorker");
            for (const fn of apiFacing) {
                expect(fn.properties.Timeout).toBeLessThanOrEqual(
                    API_GATEWAY_DEFAULT_TIMEOUT_SECONDS
                );
            }
            for (const fn of others) {
                expect(fn.properties.Timeout).toBeGreaterThan(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS);
            }
        }
    );
});

/**
 * The nested template emitted for one construct id: the file name is the construct path with
 * separators removed followed by an 8-hex hash, so stripping the hash and requiring the remainder to
 * END with the id is exact.
 */
function nestedTemplateKeyFor(s: SynthResult, construct: string): string {
    const keys = Object.keys(s.templates)
        .map((key) => ({ key, id: key.replace(/\.nested$/, "") }))
        .filter(({ id }) => /[0-9A-F]{8}$/.test(id) && id.slice(0, -8).endsWith(construct))
        .map(({ key }) => key);
    if (keys.length !== 1) {
        throw new Error(
            `expected exactly one nested template for construct ${construct}, found ${keys.length} ` +
                `(${keys.join(", ")})`
        );
    }
    return keys[0];
}

describe("EdhAddonLambdaSecurityGroup export", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the group id is a stack output and an SSM parameter under the resource-name prefix",
        (name) => {
            const s = edhSynth(name);
            const outputs = s.templates[nestedTemplateKeyFor(s, EDH_STACK)].Outputs ?? {};
            expect(outputs.EdhAddonLambdaSecurityGroupId).toBeDefined();
            expect(SynthResult.flatten(outputs.EdhAddonLambdaSecurityGroupId.Value)).toContain(
                "EdhAddonLambdaSecurityGroup"
            );

            const params = s
                .where("AWS::SSM::Parameter", (r) => r.stack.includes(EDH_STACK))
                .filter((p) =>
                    SynthResult.flatten(p.properties.Name).endsWith(
                        "/securityGroups/edhAddonLambda"
                    )
                );
            expect(params).toHaveLength(1);
            expect(SynthResult.flatten(params[0].properties.Name)).toBe(
                `/vams-t1-${name}/resourceNames/securityGroups/edhAddonLambda`
            );
            expect(SynthResult.flatten(params[0].properties.Value)).toContain(
                "EdhAddonLambdaSecurityGroup"
            );
        }
    );
});

describe("EDH table names", () => {
    const TABLES: Array<[string, string]> = [
        [ADDON_RESOURCE_PARAM_KEYS.dynamoTables.edhSessionStorage, "EdhSessionStorageTable"],
        [ADDON_RESOURCE_PARAM_KEYS.dynamoTables.edhAmiRecipeStorage, "EdhAmiRecipeStorageTable"],
    ];

    test("the add-on keys sit under addon/, apart from every core key", () => {
        expect(TABLES.map(([key]) => key)).toEqual([
            "addon/dynamoTables/edhSessionStorage",
            "addon/dynamoTables/edhAmiRecipeStorage",
        ]);
    });

    test.each(EDH_TEMPLATES)(
        "%s: each table name is an add-on SSM parameter whose value is that add-on table",
        (name) => {
            const s = edhSynth(name);
            const ssm = s.ofType("AWS::SSM::Parameter");
            for (const [key, tableId] of TABLES) {
                const params = ssm.filter(
                    (p) =>
                        SynthResult.flatten(p.properties.Name) ===
                        `/vams-t1-${name}/resourceNames/${key}`
                );
                expect(params).toHaveLength(1);
                expect(inEdhStack(params[0])).toBe(true);
                const table = resolveReference(s, params[0].stack, params[0].properties.Value);
                expect(table?.type).toBe("AWS::DynamoDB::Table");
                expect(table?.logicalId.startsWith(tableId)).toBe(true);
                expect(table && inEdhStack(table)).toBe(true);
            }
            // No parameter publishes either table under a core dynamoTables/ key.
            const coreNamed = ssm.filter((p) =>
                /\/resourceNames\/dynamoTables\/edh/.test(SynthResult.flatten(p.properties.Name))
            );
            expectAbsent("core-keyed EDH table parameters", coreNamed, {
                description: "the deployment publishes core table-name parameters",
                count: ssm.filter((p) =>
                    SynthResult.flatten(p.properties.Name).includes("/resourceNames/dynamoTables/")
                ).length,
            });
        }
    );

    test("every add-on Lambda that opens a table is created after both parameters", () => {
        const s = synthTemplate("commercial", {
            mutate: enableEdhAmiBuilds,
            mutateKey: "edhAmiBuilds",
        });
        const params = s
            .ofType("AWS::SSM::Parameter")
            .filter(
                (p) =>
                    inEdhStack(p) &&
                    /\/resourceNames\/addon\/dynamoTables\//.test(
                        SynthResult.flatten(p.properties.Name)
                    )
            )
            .map((p) => p.logicalId);
        expect(params).toHaveLength(2);
        const readers = [
            "edhWorkstationService",
            "edhLaunchWorker",
            "edhSessionReconciler",
            "edhAdminService",
            "edhCatalogRegistration",
            "edhAmiRecipeService",
            "edhAmiBuildRegistrar",
        ];
        for (const module of readers) {
            const fn = lambdaByHandler(s, `handlers.addon.edh.${module}.lambda_handler`);
            const dependsOn: string[] = [].concat(fn.raw.DependsOn ?? []);
            expect({ module, missing: params.filter((p) => !dependsOn.includes(p)) }).toEqual({
                module,
                missing: [],
            });
        }
        // Control: the custom-resource provider's framework Lambda in the same stack opens no
        // table and carries DependsOn without either parameter, so the assertion above reads
        // DependsOn where CDK writes it rather than passing on every Lambda.
        const framework = s.where(
            "AWS::Lambda::Function",
            (r) => inEdhStack(r) && handlerOf(r) === "framework.onEvent"
        );
        expect(framework).toHaveLength(1);
        const frameworkDependsOn: string[] = [].concat(framework[0].raw.DependsOn ?? []);
        expect(frameworkDependsOn.length).toBeGreaterThan(0);
        expect(frameworkDependsOn.filter((d) => params.includes(d))).toEqual([]);
    });
});

describe("EDH AMI builds", () => {
    // Every `Source.asset` call the AMI builds synth makes, recorded while it runs.
    const sourceAsset = jest.spyOn(s3deployment.Source, "asset");
    const withAmiBuilds = synthTemplate("commercial", {
        mutate: enableEdhAmiBuilds,
        mutateKey: "edhAmiBuilds",
    });
    const sourceAssetCalls = [...sourceAsset.mock.calls];
    sourceAsset.mockRestore();
    const withoutAmiBuilds = synthTemplate("commercial", {
        mutate: enableEdh,
        mutateKey: "edhWorkstationsEnabled",
    });
    const edhStackResources = (s: SynthResult) => s.resources.filter(inEdhStack);

    // Session bootstrap on, with a prefix other than the default; `withAmiBuilds` leaves it off.
    const SESSION_SECRET_PREFIX = "team-a/edh-session/";
    const withAmiBuildsSecretPrefix = synthTemplate("commercial", {
        mutate: (c: any) => {
            enableEdhAmiBuilds(c);
            const bootstrap = c.app.addons.useEdhWorkstations.sessionBootstrap;
            bootstrap.enabled = true;
            bootstrap.secretNamePrefix = SESSION_SECRET_PREFIX;
            bootstrap.workstationInstanceRoleArns = [
                `arn:${c.env.partition}:iam::${ACCOUNT}:role/EdhDesktopInstanceRole`,
            ];
        },
        mutateKey: "edhAmiBuildsSessionSecretPrefix",
    });

    // `addVpcEndpoints` is set here rather than taken from the shipped template, so a change to
    // that default cannot quietly void the endpoint assertions below.
    const withAmiBuildEndpoints = synthTemplate("commercial", {
        mutate: (c: any) => {
            enableEdhAmiBuilds(c);
            c.app.useGlobalVpc.addVpcEndpoints = true;
        },
        mutateKey: "edhAmiBuildsVpcEndpoints",
    });
    const withoutAmiBuildEndpoints = synthTemplate("commercial", {
        mutate: (c: any) => {
            enableEdh(c);
            c.app.useGlobalVpc.addVpcEndpoints = true;
        },
        mutateKey: "edhWorkstationsVpcEndpoints",
    });

    /** Flattened `ServiceName` of every interface endpoint in the deployment. */
    const interfaceEndpointServices = (s: SynthResult): string[] =>
        s
            .ofType("AWS::EC2::VPCEndpoint")
            .filter((r) => r.properties.VpcEndpointType === "Interface")
            .map((r) => SynthResult.flatten(r.properties.ServiceName));

    const tagValue = (r: Resource, key: string): string =>
        (r.properties.Tags ?? []).find((t: any) => t.Key === key)?.Value ?? "";

    const subnetsOfTier = (s: SynthResult, tier: string): Resource[] =>
        s.ofType("AWS::EC2::Subnet").filter((r) => tagValue(r, "aws-cdk:subnet-type") === tier);

    /** The actions each add-on Lambda is allowed on the AMI recipe table, by module. */
    const recipeTableActions = (s: SynthResult): Record<string, string[]> =>
        Object.fromEntries(
            edhFunctions(s)
                .map((fn): [string, string[]] => [
                    moduleOf(fn),
                    statementsOf(s, fn)
                        .filter(
                            (st) =>
                                st.Effect === "Allow" &&
                                /EdhAmiRecipeStorage/i.test(resourcesOf(st).join(" "))
                        )
                        .flatMap(actionsOf),
                ])
                .filter(([, actions]) => actions.length > 0)
        );

    // The actions `grantReadData` allows; IAM matches an action case-insensitively.
    const DYNAMODB_READ_ACTIONS = [
        "dynamodb:BatchGetItem",
        "dynamodb:ConditionCheckItem",
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:GetRecords",
        "dynamodb:GetShardIterator",
        "dynamodb:Query",
        "dynamodb:Scan",
    ].map((a) => a.toLowerCase());
    /** Every action outside that set, a wildcard or a non-DynamoDB action included. */
    const beyondReads = (actions: string[]): string[] =>
        actions.filter((a) => !DYNAMODB_READ_ACTIONS.includes(a.toLowerCase()));

    test("the recipe service and the registrar deploy only with amiBuilds.enabled", () => {
        const modules = (s: SynthResult) => edhFunctions(s).map(moduleOf);
        expect(modules(withAmiBuilds)).toEqual(
            expect.arrayContaining(["edhAmiRecipeService", "edhAmiBuildRegistrar"])
        );
        expect(modules(withoutAmiBuilds)).not.toEqual(
            expect.arrayContaining(["edhAmiRecipeService"])
        );
        expect(modules(withoutAmiBuilds)).not.toEqual(
            expect.arrayContaining(["edhAmiBuildRegistrar"])
        );
    });

    test("the workstation service reads the recipe table only with amiBuilds.enabled and writes none of it", () => {
        const granted = recipeTableActions(withAmiBuilds);
        // The build Lambdas write recipe and build rows; the launch catalog only scans them.
        expect(Object.keys(granted).sort()).toEqual([
            "edhAmiBuildRegistrar",
            "edhAmiRecipeService",
            "edhCatalogRegistration",
            "edhWorkstationService",
        ]);
        // Control: the filter keeps a write grant on this table, so the empty list below is not
        // satisfied by a filter that keeps nothing.
        expect(beyondReads(granted.edhAmiRecipeService)).toEqual(
            expect.arrayContaining(["dynamodb:PutItem", "dynamodb:UpdateItem"])
        );
        expect(granted.edhWorkstationService).toEqual(
            expect.arrayContaining(["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem"])
        );
        expect(beyondReads(granted.edhWorkstationService)).toEqual([]);
        expectAbsent(
            "AMI recipe table grants with amiBuilds off",
            Object.keys(recipeTableActions(withoutAmiBuilds)),
            {
                description: "the amiBuilds hybrid grants the recipe table to add-on Lambdas",
                count: Object.keys(granted).length,
            }
        );
    });

    test("the Image Builder state rule and the 30-minute sweep target the registrar", () => {
        const rules = edhStackResources(withAmiBuilds).filter(
            (r) => r.type === "AWS::Events::Rule"
        );
        const state = rules.find((r) =>
            JSON.stringify(r.properties.EventPattern ?? {}).includes("aws.imagebuilder")
        );
        expect(state?.properties.EventPattern["detail-type"]).toEqual([
            "EC2 Image Builder Image State Change",
        ]);
        // Only this deployment's images invoke the registrar: other teams' builds in the account do not.
        const prefix = edhFunctions(withAmiBuilds).find(
            (f) => moduleOf(f) === "edhAmiRecipeService"
        )!.properties.Environment.Variables.EDH_AMI_BUILD_PREFIX;
        const [match] = state?.properties.EventPattern.resources ?? [];
        expect(SynthResult.flatten(match.prefix)).toMatch(
            new RegExp(`:imagebuilder:.*:image/${prefix}-$`)
        );
        const sweep = rules.find((r) => r.properties.ScheduleExpression === "rate(30 minutes)");
        expect(JSON.stringify(sweep?.properties.Targets[0].Input)).toContain("sweep");
        // Both rules invoke the registrar and nothing else.
        const registrar = edhFunctions(withAmiBuilds).find(
            (f) => moduleOf(f) === "edhAmiBuildRegistrar"
        )!;
        for (const rule of [state, sweep]) {
            expect(rule?.properties.Targets).toHaveLength(1);
            expect(SynthResult.flatten(rule!.properties.Targets[0].Arn)).toContain(
                registrar.logicalId
            );
        }
    });

    test("the build instance profile carries the two Image Builder managed policies and the security group has egress only", () => {
        const roles = edhStackResources(withAmiBuilds).filter((r) => r.type === "AWS::IAM::Role");
        const buildRole = roles.find((r) =>
            JSON.stringify(r.properties.ManagedPolicyArns ?? []).includes(
                "EC2InstanceProfileForImageBuilder"
            )
        );
        expect(JSON.stringify(buildRole?.properties.ManagedPolicyArns)).toContain(
            "AmazonSSMManagedInstanceCore"
        );
        expect(
            edhStackResources(withAmiBuilds).some((r) => r.type === "AWS::IAM::InstanceProfile")
        ).toBe(true);
        const sg = edhStackResources(withAmiBuilds).find(
            (r) =>
                r.type === "AWS::EC2::SecurityGroup" &&
                String(r.properties.GroupDescription).includes("AMI recipe build")
        );
        expect(sg?.properties.SecurityGroupIngress ?? []).toEqual([]);
        expect(
            (sg?.properties.SecurityGroupEgress ?? [])
                .map((e: any) => e.FromPort)
                .sort((a: number, b: number) => a - b)
        ).toEqual([80, 443]);
    });

    test("every add-on Lambda knows whether builds are enabled and the build Lambdas carry the build environment", () => {
        for (const fn of edhFunctions(withAmiBuilds)) {
            expect(fn.properties.Environment.Variables.EDH_AMI_BUILDS_ENABLED).toBe("true");
        }
        for (const fn of edhFunctions(withoutAmiBuilds)) {
            expect(fn.properties.Environment.Variables.EDH_AMI_BUILDS_ENABLED).toBe("false");
        }
        const service = edhFunctions(withAmiBuilds).find(
            (f) => moduleOf(f) === "edhAmiRecipeService"
        )!;
        const env = service.properties.Environment.Variables;
        // `<config.name>-<baseStackName>-edh`; the harness synthesizes as vams / t1-commercial.
        expect(env.EDH_AMI_BUILD_PREFIX).toMatch(/^vams-.*-edh$/);
        expect(env.EDH_AMI_BUILD_TIMEOUT_MINUTES).toBe("180");
        expect(env.EDH_AMI_KEEP_VERSIONS_DEFAULT).toBe("3");
        expect(env.EDH_AMI_BUILD_ARTEFACTS_BUCKET).toBeDefined();
        expect(env.EDH_AMI_BUILD_INSTANCE_PROFILE_NAME).toBeDefined();
        expect(env.EDH_AMI_BUILD_SUBNET_ID).toBeDefined();
        expect(env.EDH_AMI_BUILD_SECURITY_GROUP_ID).toBeDefined();
    });

    test("the Lambdas that resolve component documents carry the configured session secret prefix, bootstrap on or off", () => {
        // The recipe service saves and starts builds, the registrar starts queued builds and the
        // registration resource seeds recipes and starts buildOnDeploy builds.
        const resolving = ["edhAmiBuildRegistrar", "edhAmiRecipeService", "edhCatalogRegistration"];
        for (const [synth, prefix] of [
            [withAmiBuilds, "vams/edh-session/"],
            [withAmiBuildsSecretPrefix, SESSION_SECRET_PREFIX],
        ] as const) {
            const fns = edhFunctions(synth).filter((f) => resolving.includes(moduleOf(f)));
            expect(fns.map(moduleOf).sort()).toEqual(resolving);
            for (const fn of fns) {
                expect(fn.properties.Environment.Variables.EDH_SESSION_SECRET_PREFIX).toBe(prefix);
            }
        }
    });

    test("Image Builder grants are scoped to the deployment prefix", () => {
        const policies = edhStackResources(withAmiBuilds).filter(
            (r) => r.type === "AWS::IAM::Policy"
        );
        const text = JSON.stringify(policies.map((p) => p.properties.PolicyDocument));
        expect(text).toContain("imagebuilder:StartImagePipelineExecution");
        // CreateImagePipeline and StartImagePipelineExecution read the resources they reference.
        for (const read of [
            "imagebuilder:GetInfrastructureConfiguration",
            "imagebuilder:GetDistributionConfiguration",
            "imagebuilder:GetImagePipeline",
            "imagebuilder:GetImageRecipe",
        ]) {
            expect(text).toContain(read);
        }
        expect(text).toContain(":imagebuilder:");
        expect(text).toContain("-edh-*");
        expect(text).toContain("iam:CreateServiceLinkedRole");
        expect(text).not.toMatch(/"imagebuilder:\*"/);
    });

    test("AMI and snapshot tagging and deletion are bound to this deployment's tag value", () => {
        // The tag key alone scopes nothing: a role holding CreateTags on the key could tag any AMI and
        // then delete it, another deployment's included. Every statement names the prefix as the value.
        const fns = edhFunctions(withAmiBuilds);
        const prefix = fns.find((f) => moduleOf(f) === "edhAmiRecipeService")!.properties
            .Environment.Variables.EDH_AMI_BUILD_PREFIX;
        const holders = fns.filter((fn) =>
            statementsOf(withAmiBuilds, fn).some((st) =>
                actionsOf(st).some((a: string) =>
                    ["ec2:CreateTags", "ec2:DeregisterImage"].includes(a)
                )
            )
        );
        expect(holders.length).toBeGreaterThan(0);
        for (const fn of holders) {
            const sts = statementsOf(withAmiBuilds, fn).filter((st) =>
                actionsOf(st).some((a: string) =>
                    ["ec2:CreateTags", "ec2:DeregisterImage", "ec2:DeleteSnapshot"].includes(a)
                )
            );
            expect(sts.length).toBeGreaterThanOrEqual(2);
            for (const st of sts) {
                const equals = st.Condition?.StringEquals ?? {};
                const named = Object.entries(equals).filter(([key]) =>
                    /^aws:(Request|Resource)Tag\/vams:deployment$/.test(key)
                );
                expect(named.length).toBeGreaterThan(0);
                for (const [, value] of named) expect(value).toBe(prefix);
                if (actionsOf(st).includes("ec2:CreateTags")) {
                    const keys = st.Condition["ForAllValues:StringEquals"]["aws:TagKeys"];
                    expect([...keys].sort()).toEqual(
                        [
                            "edh:visibility:edh-t1",
                            "vams:deployment",
                            "vams:edhAmiRecipe",
                            "vams:edhAmiRecipeHash",
                        ].sort()
                    );
                }
            }
        }
    });

    test("the catalog registration holds no Image Builder delete, AMI deregister or snapshot delete", () => {
        // It seeds recipes and starts builds at deploy time; retiring, pruning and removing a recipe
        // run in the recipe service and the registrar.
        const fns = edhFunctions(withAmiBuilds);
        const deleteActions = (fn: Resource) =>
            statementsOf(withAmiBuilds, fn)
                .flatMap((st) => actionsOf(st))
                .filter(
                    (a: string) =>
                        a.startsWith("imagebuilder:Delete") ||
                        a === "ec2:DeregisterImage" ||
                        a === "ec2:DeleteSnapshot"
                );
        const registration = fns.find((f) => moduleOf(f) === "edhCatalogRegistration")!;
        expect(registration).toBeDefined();
        expect(deleteActions(registration)).toEqual([]);
        for (const module of ["edhAmiRecipeService", "edhAmiBuildRegistrar"]) {
            const fn = fns.find((f) => moduleOf(f) === module)!;
            expect(deleteActions(fn)).toEqual(
                expect.arrayContaining(["imagebuilder:DeleteImage", "ec2:DeregisterImage"])
            );
        }
    });

    test("only the registrar cancels an image creation, and only on this deployment's images", () => {
        const fns = edhFunctions(withAmiBuilds);
        const cancels = (fn: Resource) =>
            statementsOf(withAmiBuilds, fn).filter((st) =>
                actionsOf(st).includes("imagebuilder:CancelImageCreation")
            );
        // The recipe service and the catalog registration carry the build grants too, but the sweep
        // that cancels a build running past the timeout runs in the registrar alone.
        expect(fns.filter((fn) => cancels(fn).length > 0).map(moduleOf)).toEqual([
            "edhAmiBuildRegistrar",
        ]);
        const registrar = fns.find((f) => moduleOf(f) === "edhAmiBuildRegistrar")!;
        const resources = cancels(registrar).flatMap(resourcesOf);
        expect(resources.length).toBeGreaterThan(0);
        for (const arn of resources) {
            expect(arn).toMatch(
                /^arn:\$\{AWS::Partition\}:imagebuilder:[^:]+:[^:]+:image\/vams-[^*:]*-edh-\*$/
            );
        }
    });

    test("the build Lambdas look up the instances that use an AMI and can stop or terminate none", () => {
        // An AMI is deregistered only once DescribeInstances finds no instance using it; the lookup
        // is read-only.
        const building = ["edhAmiBuildRegistrar", "edhAmiRecipeService", "edhCatalogRegistration"];
        const fns = edhFunctions(withAmiBuilds).filter((f) => building.includes(moduleOf(f)));
        expect(fns.map(moduleOf).sort()).toEqual(building);
        const allowed = (fn: Resource) =>
            statementsOf(withAmiBuilds, fn).filter((st) => st.Effect === "Allow");

        const describeResources = Object.fromEntries(
            fns.map((fn) => [
                moduleOf(fn),
                [
                    ...new Set(
                        allowed(fn)
                            .filter((st) => actionsOf(st).includes("ec2:DescribeInstances"))
                            .flatMap(resourcesOf)
                    ),
                ],
            ])
        );
        expect(describeResources).toEqual(
            Object.fromEntries(building.map((module) => [module, ["*"]]))
        );

        // IAM matches an action case-insensitively and expands `*` and `?`.
        const covers = (pattern: string, action: string) =>
            new RegExp(`^${pattern.replace(/\*/g, ".*").replace(/\?/g, ".")}$`, "i").test(action);
        const lifecycle = fns.flatMap((fn) =>
            allowed(fn)
                .flatMap(actionsOf)
                .filter((a) =>
                    ["ec2:TerminateInstances", "ec2:StopInstances"].some((x) => covers(a, x))
                )
                .map((a) => `${moduleOf(fn)}: ${a}`)
        );
        expect(lifecycle).toEqual([]);
    });

    test("only the build Lambdas look up instance type architectures, on every resource", () => {
        // A recipe save and a build start check the build instance types against the recipe's
        // architecture; DescribeInstanceTypes takes no resource ARN.
        const building = ["edhAmiBuildRegistrar", "edhAmiRecipeService", "edhCatalogRegistration"];
        const describeResources = Object.fromEntries(
            edhFunctions(withAmiBuilds)
                .map((fn) => [
                    moduleOf(fn),
                    [
                        ...new Set(
                            statementsOf(withAmiBuilds, fn)
                                .filter(
                                    (st) =>
                                        st.Effect === "Allow" &&
                                        actionsOf(st).includes("ec2:DescribeInstanceTypes")
                                )
                                .flatMap(resourcesOf)
                        ),
                    ],
                ])
                .filter(([, resources]) => resources.length > 0)
        );
        expect(describeResources).toEqual(
            Object.fromEntries(building.map((module) => [module, ["*"]]))
        );
    });

    test("the seeding custom resource carries the shipped recipe bundles", () => {
        const cr = edhStackResources(withAmiBuilds).find(
            (r) =>
                r.type === "AWS::CloudFormation::CustomResource" &&
                r.properties.amiRecipeBundles !== undefined
        );
        expect(cr?.properties.amiBuildsEnabled).toBe("true");
        const bundles = JSON.parse(cr!.properties.amiRecipeBundles);
        expect(bundles.map((b: any) => b.recipeId).sort()).toEqual([
            "al2023-desktop",
            "isaacsim",
            "windows2022-desktop",
        ]);
        expect(bundles[0].files).toEqual(expect.arrayContaining(["recipe.json"]));
        expect(bundles.every((b: any) => b.buildOnDeploy === false)).toBe(true);
        const isaacsim = bundles.find((b: any) => b.recipeId === "isaacsim");
        expect(isaacsim.assets).toEqual([
            { id: "vamscli", prefix: `${isaacsim.prefix}/assets/vamscli` },
            { id: "connector", prefix: `${isaacsim.prefix}/assets/connector` },
        ]);
        const al2023 = bundles.find((b: any) => b.recipeId === "al2023-desktop");
        expect(al2023.assets).toEqual([
            { id: "vamscli", prefix: `${al2023.prefix}/assets/vamscli` },
        ]);
        expect(bundles.find((b: any) => b.recipeId === "windows2022-desktop").assets).toEqual([]);
        const withoutCr = edhStackResources(withoutAmiBuilds).find(
            (r) =>
                r.type === "AWS::CloudFormation::CustomResource" &&
                r.properties.amiBuildsEnabled !== undefined
        );
        expect(withoutCr?.properties.amiBuildsEnabled).toBe("false");
        expect(JSON.parse(withoutCr!.properties.amiRecipeBundles)).toEqual([]);
    });

    test("each recipe asset tree gets its own bucket deployment", () => {
        const ids = edhStackResources(withAmiBuilds)
            .filter((r) => r.type === "Custom::CDKBucketDeployment")
            .map((r) => r.logicalId ?? "");
        expect(ids.some((id) => id.includes("RecipeAssetisaacsimvamscli"))).toBe(true);
        expect(ids.some((id) => id.includes("RecipeAssetisaacsimconnector"))).toBe(true);
        expect(ids.some((id) => id.includes("RecipeAssetal2023desktopvamscli"))).toBe(true);
    });

    test("each recipe asset tree is uploaded with the exclusions the bundle hash walker reads", () => {
        // One entry per shipped recipe and declared asset, as an absolute repository path.
        const declared = EDH_SHIPPED_AMI_RECIPE_DIRS.flatMap((dir) =>
            (
                JSON.parse(fs.readFileSync(path.join(REPO_ROOT, dir, "recipe.json"), "utf8"))
                    .assets ?? []
            ).map((asset: { repoPath: string }) => path.resolve(REPO_ROOT, asset.repoPath))
        );
        const treeCalls = sourceAssetCalls.filter(([source]) =>
            declared.includes(path.resolve(source))
        );
        expect(declared).toHaveLength(3);
        expect(treeCalls).toHaveLength(declared.length);
        for (const [, options] of treeCalls) {
            expect(options?.exclude).toEqual(ASSET_EXCLUDE_GLOBS);
        }
    });

    test("each recipe directory is uploaded without credential files", () => {
        const recipeDirs = EDH_SHIPPED_AMI_RECIPE_DIRS.map((dir) => path.resolve(REPO_ROOT, dir));
        const bundleCalls = sourceAssetCalls.filter(([source]) =>
            recipeDirs.includes(path.resolve(source))
        );
        expect(recipeDirs).toHaveLength(3);
        expect(bundleCalls).toHaveLength(recipeDirs.length);
        for (const [, options] of bundleCalls) {
            expect(options?.exclude).toEqual(CREDENTIAL_EXCLUDE_GLOBS);
        }
    });

    test("the build instance role reads only the recipe prefix of the artefacts bucket", () => {
        // Locate the build instance role by its managed policy.
        const buildInstanceRoles = edhStackResources(withAmiBuilds).filter(
            (r) =>
                r.type === "AWS::IAM::Role" &&
                SynthResult.flatten(r.properties.ManagedPolicyArns).includes(
                    "iam::aws:policy/EC2InstanceProfileForImageBuilder"
                )
        );
        expect(buildInstanceRoles).toHaveLength(1);
        const roleLogicalId = buildInstanceRoles[0].logicalId;

        // Select policies attached to this role.
        const buildRolePolicies = edhStackResources(withAmiBuilds).filter(
            (r) =>
                r.type === "AWS::IAM::Policy" &&
                (r.properties.Roles ?? []).some((ref: any) => ref.Ref === roleLogicalId)
        );
        expect(buildRolePolicies.length).toBeGreaterThan(0);

        // Assert one statement grants prefix-scoped read.
        const statements = buildRolePolicies.flatMap(
            (p) => p.properties.PolicyDocument?.Statement ?? []
        );
        const prefixGrant = statements.find((st: any) => {
            const actions = [st.Action].flat();
            const resources = [st.Resource].flat();
            return (
                actions.includes("s3:GetObject*") &&
                resources.some((r: string) =>
                    SynthResult.flatten(r).endsWith("/addon/edh/amiRecipes/*")
                )
            );
        });
        expect(prefixGrant).toBeDefined();

        // Negative: no bare bucket-ARN wildcard on any of this role's policies.
        const unscoped = statements
            .flatMap((st) => [st.Resource].flat())
            .map((r) => SynthResult.flatten(r))
            .filter((flat) => flat.endsWith("/*") && !flat.includes("/addon/edh/amiRecipes/"));
        expect(unscoped).toEqual([]);
        // No s3:* either.
        for (const st of statements) {
            const actions = [st.Action].flat();
            expect(actions).not.toContain("s3:*");
        }
    });

    test("the five AMI recipe routes are attached only with amiBuilds.enabled", () => {
        const pairs = restApiPairs(withAmiBuilds);
        for (const key of [
            "GET /addon/edh/admin/ami-recipes",
            "POST /addon/edh/admin/ami-recipes",
            "GET /addon/edh/admin/ami-recipes/{recipeId}",
            "PUT /addon/edh/admin/ami-recipes/{recipeId}",
            "DELETE /addon/edh/admin/ami-recipes/{recipeId}",
            "GET /addon/edh/admin/ami-recipes/{recipeId}/builds",
            "POST /addon/edh/admin/ami-recipes/{recipeId}/builds",
            "DELETE /addon/edh/admin/ami-recipes/{recipeId}/builds/{version}",
            "POST /addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register",
        ]) {
            expect(pairs.has(key)).toBe(true);
        }
        expect(restApiPairs(withoutAmiBuilds).has("GET /addon/edh/admin/ami-recipes")).toBe(false);
    });

    test("the feature flag follows amiBuilds.enabled", () => {
        expect(JSON.stringify(withAmiBuilds.resources.map((r) => r.properties))).toContain(
            "EDH_AMI_RECIPES"
        );
        expect(JSON.stringify(withoutAmiBuilds.resources.map((r) => r.properties))).not.toContain(
            "EDH_AMI_RECIPES"
        );
    });

    test("the AMI build endpoints reach Image Builder, the EC2 API and Systems Manager messaging", () => {
        const services = interfaceEndpointServices(withAmiBuildEndpoints);
        for (const service of ["imagebuilder", "ssmmessages", "ec2messages"]) {
            expect(services.filter((n) => n.endsWith("." + service))).toHaveLength(1);
        }
        // The EC2 API endpoint, not ec2messages: both service names contain "ec2".
        expect(services.filter((n) => n.endsWith(".ec2"))).toHaveLength(1);
    });

    test("none of the four AMI build endpoints exists with the add-on alone", () => {
        const services = interfaceEndpointServices(withoutAmiBuildEndpoints);
        const found = services.filter((n) =>
            [".imagebuilder", ".ec2", ".ssmmessages", ".ec2messages"].some((suffix) =>
                n.endsWith(suffix)
            )
        );
        expectAbsent("AMI build interface endpoints", found, {
            description: "the add-on hybrid creates interface endpoints at all",
            count: services.length,
        });
    });

    test("the REST API paths under the WAF exemption prefix are exactly the AMI recipe routes", () => {
        // The regional web ACL relaxes two body rules under this prefix (edhWafExclusions.test.ts),
        // so a route of any other surface under it would borrow the relaxation.
        const exclusions = buildEdhWafRuleExclusions(
            buildTemplateConfig("commercial", enableEdhAmiBuilds)
        );
        expect(exclusions).toHaveLength(1);
        expect(exclusions[0].uriPathPrefixes).toHaveLength(1);
        const [prefix] = exclusions[0].uriPathPrefixes;
        const source = fs.readFileSync(
            path.join(
                __dirname,
                "..",
                "..",
                "..",
                "lib",
                "nestedStacks",
                "addon",
                "edhWorkstations",
                "edhWorkstationsBuilder-nestedStack.ts"
            ),
            "utf8"
        );
        const recipePaths = Array.from(
            new Set(
                Array.from(
                    source.matchAll(/routePath:\s*"([^"]*\/ami-recipes[^"]*)"/g),
                    (m) => m[1]
                )
            )
        ).sort();
        expect(recipePaths).toHaveLength(5);
        const apis = withAmiBuilds.ofType("AWS::ApiGateway::RestApi");
        expect(apis).toHaveLength(1);
        const specPaths = Object.keys(apis[0].properties.Body.paths);
        expect(
            specPaths.filter((p) => `/${API_GATEWAY_STAGE_NAME}${p}`.startsWith(prefix)).sort()
        ).toEqual(recipePaths);
    });

    test("the build subnet defaults to the first isolated subnet when the VPC has none with egress", () => {
        // The VPC builder adds public and private-with-egress subnets only for a pipeline or a
        // public ALB, so this configuration has no NAT-backed subnet and the default falls back.
        expect(subnetsOfTier(withAmiBuildEndpoints, "Private")).toHaveLength(0);
        expect(withAmiBuildEndpoints.countOfType("AWS::EC2::NatGateway")).toBe(0);

        const isolated = subnetsOfTier(withAmiBuildEndpoints, "Isolated");
        expect(isolated.length).toBeGreaterThan(1);
        // The environment value is a nested-stack parameter whose name carries the subnet's logical
        // id with its own resource hash, so the assertion drops each trailing hash.
        const withoutHash = (logicalId: string) => logicalId.replace(/[0-9A-F]{8}$/, "");
        const service = edhFunctions(withAmiBuildEndpoints).find(
            (f) => moduleOf(f) === "edhAmiRecipeService"
        )!;
        const subnetRef = SynthResult.flatten(
            service.properties.Environment.Variables.EDH_AMI_BUILD_SUBNET_ID
        );
        expect(subnetRef).toContain(withoutHash(isolated[0].logicalId));
        expect(subnetRef).not.toContain(withoutHash(isolated[1].logicalId));
    });
});
