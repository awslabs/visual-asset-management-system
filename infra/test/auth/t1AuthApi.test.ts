/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: assertions against the CloudFormation each shipped config template actually emits, for the
 * auth-defaults / API Gateway shard of the pre-release review.
 *
 * Fixes covered:
 *
 * - **FIX-071** (`S1-INFRA-059`) — the seeded bootstrap `admin` role is written with
 *   `mfaRequired: false`. The owner's decision is to KEEP that default and instead emit an explicit
 *   warning that the bootstrap administrator is not MFA-protected. The constraint guards here pin the
 *   two things the owner said must not change (the seeded `false`, and no pool-level MFA enforcement);
 *   the ratchet test asserts the warning exists.
 * - **FIX-059** (`S1-INFRA-046`) — the account-singleton `AWS::ApiGateway::Account` and the API
 *   Gateway CloudWatch role are created from a per-deployment nested stack, so deleting them on
 *   teardown would reset the account-level CloudWatch role for every other REST API in that
 *   account+Region. Owner decision: RETAIN and document the orphan, which is what is asserted.
 * - **FIX-092** (`S1-INFRA-107`) — believed already fixed: the shared workflow log group declared
 *   `TEN_YEARS` while `LogRetentionAspect` overwrote it. Written WITHOUT `.failing`, because the
 *   verification that matters is the EMITTED value, not the construct declaration.
 *
 * Every negative assertion below carries a positive control. The specific way a check in this shard
 * goes vacuous is asserting a property of a resource that was never emitted at all — `expect(undefined)
 * .not.toBe("ON")` passes for a template with no user pool just as happily as for a correct one.
 */

import * as logs from "aws-cdk-lib/aws-logs";
import * as fs from "fs";
import * as Config from "../../config/config";
import cdkJson from "../../cdk.json";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

// Three full-app synths, ~20-30 s each.
jest.setTimeout(600_000);

const synthed: Partial<Record<TemplateName, SynthResult>> = {};
const synth = (name: TemplateName): SynthResult => (synthed[name] ??= synthTemplate(name));

// Synthesize up front so the getConfig() probes below cannot interleave with a synth. The probe spies
// on the real `fs` module object, and doing that while the harness is mid-synth would be a needless
// coupling even though the harness holds its own (esModuleInterop-copied) reference to readFileSync.
beforeAll(() => {
    ALL_TEMPLATES.forEach(synth);
});

/* -------------------------------------------------------------------------------------------------
 * FIX-071 — seeded bootstrap admin role and the missing MFA warning
 * ---------------------------------------------------------------------------------------------- */

/** The `AwsCustomResource` putItem that seeds the bootstrap `admin` role into the roles table. */
function seededAdminRoleCall(s: SynthResult): Resource[] {
    return s.where("Custom::AWS", (r) =>
        SynthResult.flatten(r.properties.Create).includes("initial_admin_role_creation")
    );
}

const TEMPLATE_JSON: Record<TemplateName, unknown> = {
    commercial: commercialTemplate,
    govcloud: govcloudTemplate,
    eusovereign: eusovereignTemplate,
};

const PROBE_STACK_NAME = "t1probe";

/**
 * Run `getConfig()` against a shipped template and capture what it printed.
 *
 * `getConfig()` reads `config/config.json` from disk, so the config has to be served through `fs`.
 * `config.ts` uses `import { readFileSync } from "fs"`, which compiles to a property read on the real
 * module object — so spying on `jest.requireActual("fs")` intercepts it. The harness's own `fs` binding
 * is an `__importStar` copy holding the original function, so a synth is unaffected either way.
 *
 * Returns the warnings, the resolved config (when it validated) and the error (when it did not);
 * warnings printed before a throw still count, since an operator sees them.
 */
function probeGetConfig(
    name: TemplateName,
    mutate?: (c: any) => void
): { warnings: string[]; config?: Config.Config; error?: Error } {
    const region = {
        commercial: "us-east-1",
        govcloud: "us-gov-west-1",
        eusovereign: "eusc-de-east-1",
    }[name];
    const partition = { commercial: "aws", govcloud: "aws-us-gov", eusovereign: "aws-eusc" }[name];
    const cfg = JSON.parse(JSON.stringify(TEMPLATE_JSON[name])) as any;
    cfg.env.region = region;
    cfg.env.account = "123456789012";
    cfg.app.baseStackName = PROBE_STACK_NAME;
    if (cfg.app.useAlb?.enabled) {
        cfg.app.useAlb.domainHost = "vams-t1.example.com";
        cfg.app.useAlb.certificateArn = `arn:${partition}:acm:${region}:123456789012:certificate/11111111-2222-3333-4444-555555555555`;
        cfg.app.useAlb.optionalHostedZoneId = "";
    }
    mutate?.(cfg);

    const nodeFs = jest.requireActual<typeof import("fs")>("fs");
    const realRead = nodeFs.readFileSync;
    const readSpy = jest
        .spyOn(nodeFs, "readFileSync")
        .mockImplementation((p: any, ...rest: any[]) =>
            typeof p === "string" && p.endsWith("config.json")
                ? JSON.stringify(cfg)
                : (realRead as any)(p, ...rest)
        );
    const warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    let config: Config.Config | undefined;
    let error: Error | undefined;
    try {
        config = Config.getConfig(newTestApp());
    } catch (e) {
        error = e as Error;
    }
    const warnings = warnSpy.mock.calls.map((c) => c.map((a) => String(a)).join(" "));
    warnSpy.mockRestore();
    readSpy.mockRestore();
    return { warnings, config, error };
}

/** A candidate warning has to mention MFA and the admin in the same breath to count. */
const mentionsAdminMfa = (text: string) => /mfa/i.test(text) && /admin/i.test(text);

/**
 * Configurations in which a bootstrap-admin MFA warning would be WRONG: Amazon Cognito holds no
 * password for the administrator, so the second factor is the external provider's to enforce — and the
 * MFA claim the authorizer resolves is false for an external IDP regardless, which would make the
 * `mfaRequired` role the warning recommends inactive rather than safer.
 *
 * These are listed here to establish that the emitted warning's condition is reachable: each must be a
 * configuration `getConfig()` ACCEPTS (asserted by its own control test below), or the gate in
 * `dynamodb-authdefaults-admin-construct.ts` would be guarding a state no deployment can be in. The
 * warning's absence in them is asserted in `bootstrapAdminMfaWarning.test.ts`, which builds the
 * construct directly instead of paying for two more full-app synths. The il6 case starts from the
 * govcloud template because `il6Compliant` on the commercial template is rejected for not enabling
 * `useGlobalVpc`.
 *
 * Cognito federation is deliberately NOT one of them, though it looks like it should be: the bootstrap
 * administrator is a native user pool user (`CfnUserPoolUser` "AdminUser" in
 * cognito-web-native-construct.ts, created whenever Cognito is enabled, not sourced from the IDP), and
 * a federated pool still accepts username and password for its native users — `cli/commands/
 * setup-and-auth.md` states that explicitly. Enabling SAML or OIDC therefore leaves the administrator's
 * password, and this warning, exactly where they were.
 */
const MFA_WARNING_NEGATIVES: Array<[TemplateName, string, (c: any) => void]> = [
    [
        "commercial",
        "useCognito.enabled = false",
        (c) => (c.app.authProvider.useCognito.enabled = false),
    ],
    [
        "govcloud",
        "govCloud.il6Compliant = true (forces Cognito off)",
        (c) => {
            c.app.govCloud.il6Compliant = true;
            c.app.authProvider.useCognito.enabled = false;
            c.app.useWaf = false;
            c.app.useKmsCmkEncryption.enabled = true;
        },
    ],
];

describe("FIX-071: the bootstrap admin is seeded without MFA and the operator is told so", () => {
    test("the getConfig() probe really serves the template it was handed (control)", () => {
        // Without this, the negative-configuration control below could pass because the probe reads
        // the real on-disk config.json (or nothing at all) rather than the mutated template.
        for (const name of ALL_TEMPLATES) {
            const clean = probeGetConfig(name);
            // A throw would truncate the warning capture, so a later warning would be missed and the
            // ratchet test would fail for the wrong reason.
            expect(clean.error).toBeUndefined();
            // getConfig() appends the region to baseStackName, hence the prefix match. If the fs spy
            // had not intercepted, this would be whatever config/config.json holds on disk.
            expect(clean.config?.app.baseStackName).toMatch(new RegExp(`^${PROBE_STACK_NAME}`));
        }

        // Control for the console.warn capture itself: none of the 15 existing getConfig() warnings
        // fire for any of the three shipped templates as written, so the capture has to be proven with
        // a mutation known to warn. Without this, "no MFA warning" and "no capture at all" look alike.
        const noisy = probeGetConfig("commercial", (c) => {
            c.app.authProvider.useCognito.useUserPasswordAuthFlow = true;
        });
        expect(noisy.warnings.some((w) => /UserPasswordAuth/.test(w))).toBe(true);

        // Control for the served config being the one validated: an invalid value must be rejected.
        const broken = probeGetConfig("commercial", (c) => {
            c.app.adminEmailAddress = "UNDEFINED";
        });
        expect(broken.error?.message).toMatch(/initial admin email address/i);
    });

    test("each configuration the warning must stay silent in is one getConfig() accepts (control)", () => {
        // Reachability control for the gate the fix added. If `useCognito.enabled = false` were not a
        // configuration `getConfig()` accepts, the gate would be guarding an unreachable state and the
        // silence asserted in bootstrapAdminMfaWarning.test.ts would prove nothing about a deployment.
        const rejected = MFA_WARNING_NEGATIVES.map(([base, label, mutate]) => ({
            label,
            error: probeGetConfig(base, mutate).error?.message,
        })).filter((r) => r.error !== undefined);
        expect(rejected).toEqual([]);
    });

    test.each(ALL_TEMPLATES)(
        "%s: synth warns that the bootstrap administrator is not MFA protected",
        (name) => {
            // The emission mechanism chosen is `Annotations.of(scope).addWarningV2` in
            // dynamodb-authdefaults-admin-construct.ts, matching wafv2-basic-construct.ts:154 — the AWS
            // CDK CLI prints these while synthesizing and deploying, which is where an operator is
            // looking, whereas a CfnOutput is only visible once the stack has finished.
            //
            // They are NOT in any *.template.json: an annotation is assembly metadata, reported on the
            // enclosing top-level stack artifact even when the construct sits in a nested stack. The
            // harness exposes them as `SynthResult.warnings`, so this reads the same channel the CLI
            // prints rather than a proxy for it.
            //
            // All three shipped templates set useCognito.enabled = true, so the bootstrap admin really
            // is a Cognito password user in all three and the warning is expected in all three.
            const s = synth(name);
            const hits = s.warnings.filter((w) => mentionsAdminMfa(w.message));
            // eslint-disable-next-line no-console
            console.log(
                `[T1 annotations] ${name}: ${s.warnings.length} warning(s), ` +
                    `${hits.length} about admin MFA: ${hits.map((w) => w.path).join(", ")}`
            );
            // Exactly one, and attributable to the construct that seeds the role — not merely "some
            // warning somewhere mentions MFA". Three unrelated things could otherwise satisfy this: a
            // CDK deprecation notice, a warning from another construct, or two copies of this one.
            expect(hits.length).toBe(1);
            expect(hits[0].path).toContain("DynamoDBAuthDefaultsAdmin");
            expect(hits[0].message).toContain("mfaRequired");
            expect(hits[0].message).toContain("not MFA protected");
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s keeps the shipped default mfaRequired BOOL false on the seeded admin role",
        (name) => {
            // Owner constraint: do NOT change the shipped default. This guards the constraint rather
            // than the defect — if someone "fixes" FIX-071 by flipping the seeded value, this fails.
            const s = synth(name);
            const calls = seededAdminRoleCall(s);
            // Positive control: the seeding custom resource exists at all, and exactly once. The
            // basicReadOnly role is seeded by a sibling construct with the same field, which is why
            // the match is anchored on `initial_admin_role_creation` rather than on `mfaRequired`.
            expect(calls.length).toBe(1);
            for (const key of ["Create", "Update"]) {
                const body = SynthResult.flatten(calls[0].properties[key]);
                expect(body).toContain('"roleName":{"S":"admin"}');
                expect(body).toContain('"mfaRequired":{"BOOL":false}');
            }
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s re-puts the seeded admin role on every deploy (no ConditionExpression)",
        (name) => {
            // The ConditionExpression is commented out in the construct and `createdOn` is evaluated
            // at synth, so the putItem parameters change every deploy and the row is rewritten. An
            // operator who flips mfaRequired to true in the UI has it reset. The warning text has to
            // say so, which is only true while this holds.
            const calls = seededAdminRoleCall(synth(name));
            expect(calls.length).toBe(1);
            const body = SynthResult.flatten(calls[0].properties.Create);
            expect(body).toContain('"action":"putItem"');
            expect(body).not.toContain("ConditionExpression");
        }
    );

    test.each(ALL_TEMPLATES)("%s does not enforce MFA at the Cognito user pool level", (name) => {
        // Owner constraint: enrollment is out of scope, so pool-level MFA must stay non-mandatory.
        const pools = synth(name).ofType("AWS::Cognito::UserPool");
        // Positive control: a pool is emitted AND it declares a MfaConfiguration, so `not "ON"` is a
        // statement about a real value rather than about `undefined`.
        expect(pools.length).toBe(1);
        expect(typeof pools[0].properties.MfaConfiguration).toBe("string");
        expect(pools[0].properties.MfaConfiguration).not.toBe("ON");
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-059 — account-singleton API Gateway CloudWatch role removal policy
 * ---------------------------------------------------------------------------------------------- */

/** The `AWS::ApiGateway::Account` singleton and the CloudWatch role it points at. */
function apiGatewayAccountAndRole(s: SynthResult): { account: Resource; role: Resource } {
    const accounts = s.ofType("AWS::ApiGateway::Account");
    expect(accounts.length).toBe(1);
    const account = accounts[0];
    const ref = account.properties.CloudWatchRoleArn?.["Fn::GetAtt"];
    // Resolve the role through the Account's own reference rather than by logical-id pattern, so the
    // assertion cannot silently target some other role that happens to be named similarly.
    expect(Array.isArray(ref)).toBe(true);
    const role = s.resources.find((r) => r.stack === account.stack && r.logicalId === ref[0]);
    expect(role).toBeDefined();
    expect(role!.type).toBe("AWS::IAM::Role");
    return { account, role: role! };
}

describe("FIX-059: the API Gateway account-level CloudWatch role and Account survive teardown", () => {
    it("FIX-059: the API Gateway CloudWatch role is retained, not deleted, in every template", () => {
        // `cloudWatchRoleRemovalPolicy: cdk.RemovalPolicy.RETAIN` in
        // rest-api-gateway-construct.ts is applied by CDK to both the role and the
        // AWS::ApiGateway::Account, so both halves are asserted rather than only the role.
        const policies: Record<string, unknown> = {};
        for (const name of ALL_TEMPLATES) {
            const { role } = apiGatewayAccountAndRole(synth(name));
            policies[name] = {
                DeletionPolicy: role.raw.DeletionPolicy,
                UpdateReplacePolicy: role.raw.UpdateReplacePolicy,
            };
        }
        expect(policies).toEqual({
            commercial: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
            govcloud: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
            eusovereign: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
        });
    });

    it("FIX-059: the account-singleton AWS::ApiGateway::Account is retained in every template", () => {
        // This is the half that actually matters: the Account resource is an account+Region
        // singleton, so deleting it on teardown of one VAMS deployment silently disables stage
        // execution/access logging for every co-resident REST API.
        const policies: Record<string, unknown> = {};
        for (const name of ALL_TEMPLATES) {
            const { account } = apiGatewayAccountAndRole(synth(name));
            policies[name] = {
                DeletionPolicy: account.raw.DeletionPolicy,
                UpdateReplacePolicy: account.raw.UpdateReplacePolicy,
            };
        }
        expect(policies).toEqual({
            commercial: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
            govcloud: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
            eusovereign: { DeletionPolicy: "Retain", UpdateReplacePolicy: "Retain" },
        });
    });

    test.each(ALL_TEMPLATES)(
        "%s: sibling roles in the same nested stack carry no DeletionPolicy (negative control)",
        (name) => {
            // Proves the Retain assertions above are targeted at two resources rather than reading a
            // stack-wide default. If a future change stamped Retain on every role, the two tests would
            // pass for the wrong reason and this one would fail.
            const s = synth(name);
            const { account, role } = apiGatewayAccountAndRole(s);
            const siblings = s.where(
                "AWS::IAM::Role",
                (r) => r.stack === account.stack && r.logicalId !== role.logicalId
            );
            expect(siblings.length).toBeGreaterThan(0);
            expect(siblings.some((r) => /RestAuthorizerInvokeRole/.test(r.logicalId))).toBe(true);
            expect(
                siblings
                    .filter((r) => r.raw.DeletionPolicy !== undefined)
                    .map((r) => `${r.logicalId}=${r.raw.DeletionPolicy}`)
            ).toEqual([]);
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s: the CloudWatch role has a literal RoleName, so a retained orphan collides on redeploy",
        (name) => {
            // This is the fact that makes the uninstall.md warning load-bearing: `IamRoleTransform`
            // stamps a fixed RoleName onto every role including CDK-generated ones, so RETAIN leaves
            // a named orphan that blocks a redeploy of the same stack into the same Region. If the
            // aspect is ever removed, the name becomes CFN-generated and the warning becomes wrong.
            const { role } = apiGatewayAccountAndRole(synth(name));
            const roleName = role.properties.RoleName;
            expect(typeof roleName).toBe("string");
            expect(roleName).not.toContain("${"); // no unresolved CFN token
            expect(roleName).toMatch(/CloudWatchRole/);
            expect(roleName).toContain(synth(name).region);
        }
    );

    test("cdk.json still defines context.environments.aws, which activates IamRoleTransform", () => {
        // Without this block `awsEnv` is undefined in core-stack.ts, the aspect is never added, and the
        // RoleName assertion above would become vacuous (CFN would generate the name instead).
        const awsEnv = (cdkJson as any).context?.environments?.aws;
        expect(awsEnv).toBeDefined();
        expect(Object.keys(awsEnv)).toEqual(
            expect.arrayContaining(["IamRoleNamePrefix", "PermissionBoundaryArn"])
        );
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-092 — shared workflow log group retention (believed already fixed; verify only)
 * ---------------------------------------------------------------------------------------------- */

const WORKFLOW_LOG_GROUP_PREFIX = "/aws/vendedlogs/vamsPipelineWorkflows";

const CORE_STACK_PATH = require.resolve("../../lib/core-stack.ts");
const LIB_DIR = CORE_STACK_PATH.replace(/core-stack\.ts$/, "");

/** The `RetentionDays` member name the `LogRetentionAspect` is actually constructed with. */
function aspectRetentionName(): string {
    const source = fs.readFileSync(CORE_STACK_PATH, "utf8");
    const matches = [
        ...source.matchAll(/new LogRetentionAspect\(\s*logs\.RetentionDays\.([A-Z_0-9]+)\s*\)/g),
    ];
    // Read the value from the call site instead of hardcoding 365: S1-INFRA-109 may move it, and a
    // verification pinned to a literal would then fail for the wrong reason. Exactly one call site is
    // expected — two would mean the "single authority" premise no longer holds.
    expect(matches.length).toBe(1);
    return matches[0][1];
}

/** The retention, in days, the `LogRetentionAspect` is actually constructed with. */
function aspectRetentionDays(): number {
    const days = (logs.RetentionDays as any)[aspectRetentionName()];
    expect(typeof days).toBe("number");
    return days as number;
}

/** Source with comments removed, so a `RetentionDays.X` mentioned in prose is not read as code. */
function stripComments(text: string): string {
    return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("FIX-092: the shared workflow log group's EMITTED retention matches the aspect", () => {
    test.each(ALL_TEMPLATES)(
        "%s emits the aspect's retention on the workflow log group",
        (name) => {
            const s = synth(name);
            const groups = s.where("AWS::Logs::LogGroup", (g) =>
                SynthResult.flatten(g.properties.LogGroupName).startsWith(WORKFLOW_LOG_GROUP_PREFIX)
            );
            // Positive control: the group was found, exactly once. Asserting RetentionInDays on an
            // undefined resource is the specific way a "verify only" check goes vacuous.
            expect(groups.length).toBe(1);
            expect(groups[0].properties.RetentionInDays).toBe(aspectRetentionDays());
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s keeps the exact 'vamsPipelineWorkflows' casing in the log group name",
        (name) => {
            // The lowercase 'vams' substring is load-bearing: the log-read IAM grants in
            // workflowFunctions.ts match /aws/vendedlogs/* names case-sensitively.
            const s = synth(name);
            const names = s
                .ofType("AWS::Logs::LogGroup")
                .map((g) => SynthResult.flatten(g.properties.LogGroupName))
                .filter((n) => /vamspipelineworkflows/i.test(n));
            expect(names.length).toBe(1);
            expect(names[0].startsWith(WORKFLOW_LOG_GROUP_PREFIX)).toBe(true);
        }
    );

    test("no construct in infra/lib declares a retention that differs from the aspect's", () => {
        // Source-level guard for the class of defect FIX-092 belongs to: a construct-level `retention`
        // is dead code the aspect overwrites, so a declaration that disagrees with the aspect makes
        // reading the construct actively misleading.
        const allowLowRetention = new Map<string, string[]>([
            // Deliberately-short pipeline log groups, not stale declarations.
            ["modelOps-construct.ts", ["ONE_MONTH"]],
            ["rapidPipeline-construct.ts", ["ONE_MONTH"]],
            ["rapidPipelineEKS-construct.ts", ["TWO_WEEKS"]],
        ]);
        // Asserted in-band so the allow-list cannot be widened silently to absorb a new drift.
        expect(allowLowRetention.size).toBe(3);

        const aspectName = aspectRetentionName();

        const walk = (dir: string): string[] =>
            fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
                const p = `${dir}/${e.name}`;
                return e.isDirectory() ? walk(p) : e.isFile() && p.endsWith(".ts") ? [p] : [];
            });

        const drift: string[] = [];
        let declarations = 0;
        for (const file of walk(LIB_DIR)) {
            // The aspect file is the authority; its own prose names other values by design.
            if (file.endsWith("log-retention.aspect.ts")) continue;
            const text = stripComments(fs.readFileSync(file, "utf8"));
            for (const m of text.matchAll(/RetentionDays\.([A-Z_0-9]+)/g)) {
                declarations++;
                if (m[1] === aspectName) continue;
                const base = file.split(/[\\/]/).pop()!;
                if (allowLowRetention.get(base)?.includes(m[1])) continue;
                drift.push(`${base}: RetentionDays.${m[1]}`);
            }
        }
        // Control: the scan found declarations at all. Zero would make `drift == []` meaningless.
        expect(declarations).toBeGreaterThan(20);
        expect(drift).toEqual([]);
    });
});
