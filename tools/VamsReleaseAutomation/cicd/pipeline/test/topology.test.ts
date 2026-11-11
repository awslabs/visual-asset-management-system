/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// The release pipeline follows the ledger's promotion graph: fan-out runs siblings in parallel (each
// promoting from its own promotesFrom, never from a sibling), waves run in order, ungated envs get no
// approval, a gated root gets one. Offline: in-memory synth, cdk-nag on every topology.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { App, Aspects } from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import { PipelineConfig, ReleaseEnv, loadConfig, planRelease, validateConfig } from "../lib/config";
import { VraPipelineStack } from "../lib/vra-pipeline-stack";

const cdkContext = JSON.parse(readFileSync(join(__dirname, "..", "cdk.json"), "utf8")).context;
const BASE = join(__dirname, "..", "config", "pipeline.example.commercial-github.json");

function configWith(envs: ReleaseEnv[], extra: Partial<PipelineConfig> = {}): PipelineConfig {
    const cfg = JSON.parse(readFileSync(BASE, "utf8")) as PipelineConfig;
    return { ...cfg, ...extra, release: { ...cfg.release, ...(extra.release ?? {}), envs } };
}

interface Action {
    Name: string;
    RunOrder?: number;
    ActionTypeId: { Category: string };
    Configuration: { EnvironmentVariables?: string; ProjectName?: unknown };
}
interface Stage {
    Name: string;
    Actions: Action[];
}

function synth(cfg: PipelineConfig) {
    validateConfig(cfg);
    const app = new App({ context: cdkContext });
    const stack = new VraPipelineStack(app, "vra-pipeline", {
        config: cfg,
        env: { account: cfg.toolsAccount, region: cfg.region },
    });
    Aspects.of(app).add(new AwsSolutionsChecks());
    app.synth();
    const template = Template.fromStack(stack);
    const release = Object.values(template.findResources("AWS::CodePipeline::Pipeline")).find(
        (p) => p.Properties.Name === "vra-release"
    );
    const stages = release?.Properties.Stages as Stage[];
    const nag = [
        ...Annotations.fromStack(stack).findError("*", Match.stringLikeRegexp("AwsSolutions-.*")),
        ...Annotations.fromStack(stack).findWarning("*", Match.stringLikeRegexp("AwsSolutions-.*")),
    ];
    return { template, stages, nag };
}

function vars(a: Action): Record<string, string> {
    const list = JSON.parse(a.Configuration.EnvironmentVariables ?? "[]") as Array<{
        name: string;
        value: string;
    }>;
    return Object.fromEntries(list.map((v) => [v.name, v.value]));
}

function action(stages: Stage[], name: string): { stage: Stage; action: Action } {
    for (const stage of stages) {
        const a = stage.Actions.find((x) => x.Name === name);
        if (a) return { stage, action: a };
    }
    throw new Error(`no action ${name} in ${stages.map((s) => s.Name).join(", ")}`);
}

const DEV = { name: "dev", account: "222222222222" };
const TEST = { name: "test", account: "333333333333", promotesFrom: "dev", requiresApproval: true };

describe("fan-out: test -> prod-us and test -> prod-eu", () => {
    const { stages, nag } = synth(
        configWith([
            DEV,
            TEST,
            {
                name: "prod-us",
                account: "444444444444",
                promotesFrom: "test",
                requiresApproval: true,
            },
            {
                name: "prod-eu",
                account: "555555555555",
                promotesFrom: "test",
                requiresApproval: true,
            },
        ])
    );

    test("siblings share one approval stage and one deploy stage, in parallel", () => {
        expect(stages.map((s) => s.Name)).toEqual([
            "Source",
            "Validate",
            "Build",
            "Deploy-dev",
            "Approve-test",
            "Deploy-test",
            "Approve-prod-eu_prod-us",
            "Deploy-prod-eu_prod-us",
        ]);
        const us = action(stages, "Promote-prod-us");
        const eu = action(stages, "Promote-prod-eu");
        expect(us.stage.Name).toBe(eu.stage.Name);
        expect(us.action.RunOrder).toBe(eu.action.RunOrder);
    });

    test("each sibling promotes from test, never from the other sibling", () => {
        expect(vars(action(stages, "Promote-prod-us").action).VRA_PROMOTE_FROM).toBe("test");
        expect(vars(action(stages, "Promote-prod-eu").action).VRA_PROMOTE_FROM).toBe("test");
    });

    test("each sibling's deploy reads its own approval action", () => {
        expect(vars(action(stages, "Promote-prod-us").action).VRA_APPROVAL_ACTION).toBe(
            "FinalApproval-prod-us"
        );
        expect(vars(action(stages, "Promote-prod-eu").action).VRA_APPROVAL_ACTION).toBe(
            "FinalApproval-prod-eu"
        );
    });

    test("cdk-nag is clean", () => {
        expect(nag.map((n) => `${n.id}: ${n.entry.data}`)).toEqual([]);
    });
});

describe("waves: test -> prod-us -> prod-eu", () => {
    const { stages } = synth(
        configWith([
            DEV,
            TEST,
            {
                name: "prod-us",
                account: "444444444444",
                promotesFrom: "test",
                requiresApproval: true,
            },
            {
                name: "prod-eu",
                account: "555555555555",
                promotesFrom: "prod-us",
                requiresApproval: true,
            },
        ])
    );

    test("the second wave starts after the first and promotes from it", () => {
        const names = stages.map((s) => s.Name);
        expect(names.indexOf("Deploy-prod-us")).toBeLessThan(names.indexOf("Approve-prod-eu"));
        expect(vars(action(stages, "Promote-prod-eu").action).VRA_PROMOTE_FROM).toBe("prod-us");
        // a wave that feeds another env runs its live tests
        expect(action(stages, "Test-prod-us").action.RunOrder).toBe(2);
        expect(action(stages, "Approval-prod-us").action.ActionTypeId.Category).toBe("Approval");
    });
});

describe("ungated intermediate env: dev -> int (auto) -> test", () => {
    const { stages, template } = synth(
        configWith([
            DEV,
            { name: "int", account: "222222222222", promotesFrom: "dev" },
            { ...TEST, promotesFrom: "int" },
        ])
    );

    test("int gets no approval stage and no approval binding", () => {
        expect(stages.map((s) => s.Name)).toEqual([
            "Source",
            "Validate",
            "Build",
            "Deploy-dev",
            "Deploy-int",
            "Approve-test",
            "Deploy-test",
        ]);
        const int = vars(action(stages, "Promote-int").action);
        expect(int.VRA_PROMOTE_FROM).toBe("dev");
        expect(int.VRA_APPROVAL_ACTION).toBeUndefined();
        expect(vars(action(stages, "Promote-test").action).VRA_PROMOTE_FROM).toBe("int");
    });

    test("int has its own tier role, not the dev tier", () => {
        const roles = template.findResources("AWS::IAM::Role");
        const names = Object.values(roles).map((r) => JSON.stringify(r.Properties.RoleName));
        expect(names.some((n) => n.includes("vra-codebuild-deploy-int-"))).toBe(true);
    });
});

describe("gated root: a tenant prod deployed straight from the build", () => {
    const { stages } = synth(
        configWith([DEV, { name: "tenant-a", account: "444444444444", requiresApproval: true }])
    );

    test("the approval comes before the root deploy, which is a deploy, not a promotion", () => {
        expect(stages.map((s) => s.Name)).toEqual([
            "Source",
            "Validate",
            "Build",
            "Approve-tenant-a",
            "Deploy-dev_tenant-a",
        ]);
        const t = vars(action(stages, "Deploy-tenant-a").action);
        expect(t.VRA_PROMOTE_FROM).toBe("");
        expect(t.VRA_APPROVAL_ACTION).toBe("FinalApproval-tenant-a");
        expect(vars(action(stages, "Deploy-dev").action).VRA_APPROVAL_ACTION).toBeUndefined();
    });
});

describe("no gated env at all", () => {
    test("is a valid topology (dev -> int, both ungated)", () => {
        const { stages } = synth(
            configWith([DEV, { name: "int", account: "222222222222", promotesFrom: "dev" }])
        );
        expect(stages.filter((s) => s.Name.startsWith("Approve-"))).toEqual([]);
    });
});

describe("validation", () => {
    test("an unknown promotesFrom is an error", () => {
        expect(() =>
            validateConfig(configWith([DEV, { ...TEST, promotesFrom: "staging" }]))
        ).toThrow(/promotesFrom 'staging'/);
    });
    test("a promotion cycle is an error", () => {
        expect(() =>
            validateConfig(
                configWith([
                    { ...DEV, promotesFrom: "test" },
                    { ...TEST, promotesFrom: "dev" },
                ])
            )
        ).toThrow(/cycle/);
    });
    test("an empty release lane is an error", () => {
        expect(() => validateConfig(configWith([]))).toThrow(/at least one env/);
    });
});

describe("planRelease", () => {
    test("long sibling lists fall back to a wave stage name", () => {
        const many: ReleaseEnv[] = [DEV];
        for (let i = 0; i < 12; i++) {
            many.push({
                name: `prod-region-${i}`,
                account: "444444444444",
                promotesFrom: "dev",
                requiresApproval: true,
            });
        }
        const names = planRelease(many).map((s) => s.name);
        expect(names).toContain("Approve-wave-1");
        expect(names.every((n) => n.length <= 100)).toBe(true);
    });
});

describe("ledgerGraph: the topology comes from `vra --json ledger graph`", () => {
    const dir = mkdtempSync(join(tmpdir(), "vra-ledger-"));
    const genv = (o: Record<string, unknown>) => ({
        lane: "release",
        promotesFrom: null,
        requiresApproval: false,
        autoDeploy: false,
        tier: "nonprod",
        isProdClass: false,
        requiredSuites: ["live-cli"],
        region: "us-east-1",
        children: [],
        ...o,
    });
    const graph = {
        exitCode: 0,
        message: null,
        result: {
            envs: {
                dev: genv({ account: "222222222222", autoDeploy: true, children: ["test"] }),
                test: genv({
                    account: "333333333333",
                    promotesFrom: "dev",
                    requiresApproval: true,
                    requiredSuites: ["live-cli", "smoke"],
                    children: ["prod-a", "prod-b"],
                }),
                "prod-a": genv({
                    account: "444444444444",
                    promotesFrom: "test",
                    requiresApproval: true,
                    tier: "prod",
                }),
                "prod-b": genv({
                    account: "555555555555",
                    promotesFrom: "test",
                    requiresApproval: true,
                    tier: "prod",
                }),
                "dev-preview": genv({
                    account: "222222222222",
                    lane: "preview",
                    tier: "preview",
                    requiredSuites: [],
                }),
            },
            roots: ["dev", "dev-preview"],
        },
    };
    writeFileSync(join(dir, "ledger-graph.json"), JSON.stringify(graph));
    // env.yaml only for what the graph does not carry: role names and the approval signing key
    const KEY = "arn:aws:kms:us-east-1:333333333333:key/approvals";
    mkdirSync(join(dir, "ledger", "envs", "test"), { recursive: true });
    writeFileSync(
        join(dir, "ledger", "envs", "test", "env.yaml"),
        "name: test\n" +
            "deployRoleArn: arn:aws:iam::333333333333:role/custom-deploy-test\n" +
            `approvalSigningKeyArn: ${KEY}\n` +
            // never read for the topology: the graph says test is gated
            "requiresApproval: false\n"
    );
    const cfgFile = join(dir, "pipeline.json");
    const base = JSON.parse(readFileSync(BASE, "utf8"));
    delete base.release.envs;
    writeFileSync(
        cfgFile,
        JSON.stringify({ ...base, ledgerGraph: "ledger-graph.json", ledgerDir: "ledger" })
    );
    const cfg = loadConfig(cfgFile);
    const { stages, template } = synth(cfg);

    test("release envs, gates and tiers come from the graph; role names from env.yaml", () => {
        const envs = cfg.release.envs ?? [];
        expect(envs.map((e) => e.name)).toEqual(["dev", "prod-a", "prod-b", "test"]);
        expect(envs.find((e) => e.name === "test")?.requiresApproval).toBe(true);
        expect(envs.find((e) => e.name === "prod-a")?.tier).toBe("prod");
        expect(envs.find((e) => e.name === "test")?.deployRoleName).toBe("custom-deploy-test");
        expect(vars(action(stages, "Promote-prod-a").action).VRA_PROMOTE_FROM).toBe("test");
        expect(vars(action(stages, "Promote-test").action).VRA_APPROVAL_ACTION).toBe(
            "Approval-test"
        );
        expect(vars(action(stages, "Deploy-dev").action).VRA_APPROVAL_ACTION).toBeUndefined();
        expect(JSON.stringify(template.findResources("AWS::IAM::Policy"))).toContain(
            "role/custom-deploy-test"
        );
    });

    test("an env's test action runs every suite the ledger requires before it promotes", () => {
        expect(vars(action(stages, "Test-test").action).VRA_TEST_SUITES).toBe(
            "live-cli,live-web,customer,smoke"
        );
    });

    test("Validate checks the ledger against the graph the pipeline was built from", () => {
        const projects = template.findResources("AWS::CodeBuild::Project");
        const v = Object.values(projects).find((p) => p.Properties.Name === "vra-validate");
        const topo = JSON.parse(
            v?.Properties.Environment.EnvironmentVariables.find(
                (x: { Name: string }) => x.Name === "VRA_PIPELINE_TOPOLOGY"
            ).Value
        );
        const test = topo.release.envs.find((e: { name: string }) => e.name === "test");
        expect(test).toEqual({
            name: "test",
            account: "333333333333",
            promotesFrom: "dev",
            requiresApproval: true,
            requiredSuites: ["live-cli", "smoke"],
        });
    });

    test("the env's tier verifies its approval signatures and never signs", () => {
        const policies = Object.values(template.findResources("AWS::IAM::Policy"));
        const verify = policies
            .flatMap((p) => p.Properties.PolicyDocument.Statement)
            .filter((s: { Sid?: string }) => s.Sid === "VerifyApprovalSignatures");
        expect(verify).toHaveLength(1);
        expect([...verify[0].Action].sort()).toEqual(["kms:DescribeKey", "kms:Verify"]);
        expect(verify[0].Resource).toBe(KEY);
        const owner = policies.find((p) =>
            JSON.stringify(p.Properties.PolicyDocument.Statement).includes(
                "VerifyApprovalSignatures"
            )
        );
        expect(JSON.stringify(owner?.Properties.Roles)).toContain("DeployRoletest");
    });

    test("ledgerDir alone is refused (the topology never comes from re-reading env.yaml)", () => {
        const f = join(dir, "only-dir.json");
        writeFileSync(f, JSON.stringify({ ...base, ledgerDir: "ledger" }));
        expect(() => loadConfig(f)).toThrow(/ledgerGraph/);
    });
});

describe("state store: tier-scoped locks, create-only history and bundles, no deletes", () => {
    const stateStore = {
        bucketName: "vra-state-111111111111-us-east-1",
        lockTableName: "vra-locks",
        keyArn: "arn:aws:kms:us-east-1:111111111111:key/state",
    };
    const { template } = synth(configWith([DEV, TEST], { stateStore }));
    const projects = template.findResources("AWS::CodeBuild::Project");
    const policies = {
        ...template.findResources("AWS::IAM::Policy"),
        ...template.findResources("AWS::IAM::ManagedPolicy"),
    };
    type Stmt = {
        Sid?: string;
        Effect: string;
        Action: unknown;
        Resource: unknown;
        Condition?: Record<string, Record<string, unknown>>;
    };
    const statementsOf = (projectName: string): Stmt[] => {
        const proj = Object.values(projects).find((p) => p.Properties.Name === projectName);
        const roleId = proj?.Properties.ServiceRole["Fn::GetAtt"][0];
        return Object.values(policies)
            .filter((p) => JSON.stringify(p.Properties.Roles).includes(`"${roleId}"`))
            .flatMap((p) => p.Properties.PolicyDocument.Statement);
    };
    const lockKeys = (name: string) =>
        statementsOf(name).find((s) => s.Sid === "StateLocks")?.Condition?.[
            "ForAllValues:StringLike"
        ]["dynamodb:LeadingKeys"];

    test("each tier takes run leases and only its own env locks", () => {
        expect(lockKeys("vra-build")).toEqual(["run#*"]);
        // dev, preview and the scheduled watch are separate tiers, each with its own lock
        expect(lockKeys("vra-deploy-dev")).toEqual(["run#*", "env#dev"]);
        expect(lockKeys("vra-deploy-dev-preview")).toEqual(["run#*", "env#dev-preview"]);
        expect(lockKeys("vra-watch")).toEqual(["run#*", "env#watch"]);
        expect(lockKeys("vra-deploy-test")).toEqual(["run#*", "env#test"]);
    });

    test("history and bundles are create-only; nothing in the store is deleted or updated in place", () => {
        for (const name of ["vra-build", "vra-deploy-dev", "vra-deploy-test", "vra-watch"]) {
            const stmts = statementsOf(name);
            const create = stmts.find((s) => s.Sid === "StateCreateOnly");
            expect(create?.Condition).toEqual({ Null: { "s3:if-none-match": "false" } });
            expect(JSON.stringify(create?.Resource)).toContain("/vra/history/*");
            expect(JSON.stringify(create?.Resource)).toContain("/vra/artifacts/*");
            const unconditional = stmts.filter(
                (s) =>
                    s.Effect === "Allow" &&
                    JSON.stringify(s.Action).includes("s3:PutObject") &&
                    !s.Condition &&
                    /\/(history|artifacts)\//.test(JSON.stringify(s.Resource))
            );
            expect(unconditional).toEqual([]);
            const text = JSON.stringify(stmts.filter((s) => s.Effect === "Allow"));
            expect(text).not.toContain("dynamodb:UpdateItem");
            expect(text).not.toMatch(/s3:DeleteObject[^"]*"[^\]]*vra-state/);
            const deny = stmts.find((s) => s.Sid === "HistoryIsAppendOnly");
            expect(JSON.stringify(deny?.Resource)).toContain("/*/approvals-consumed/*");
        }
    });
});

describe("validation of CI environment variables", () => {
    test("a VAMS config variable in codebuild.environment is refused", () => {
        const cfg = configWith([DEV, TEST], {
            codebuild: { environment: { REGION: "us-east-1" } },
        });
        expect(() => validateConfig(cfg)).toThrow(
            /codebuild.environment.REGION is a VAMS config variable/
        );
        expect(() =>
            validateConfig(
                configWith([DEV, TEST], {
                    codebuild: { environment: { NPM_CONFIG_REGISTRY: "x" } },
                })
            )
        ).not.toThrow();
    });
    test("approvalSigningKeyArn must be a KMS key", () => {
        expect(() =>
            validateConfig(configWith([DEV, { ...TEST, approvalSigningKeyArn: "alias/x" }]))
        ).toThrow(/approvalSigningKeyArn/);
    });
});

describe("isolateTests", () => {
    const { template } = synth(configWith([DEV, TEST], { release: { isolateTests: true } }));

    test("test projects assume only the env's live-test role, never a deploy role", () => {
        const projects = template.findResources("AWS::CodeBuild::Project");
        const policies = template.findResources("AWS::IAM::Policy");
        const proj = Object.values(projects).find((p) => p.Properties.Name === "vra-test-test");
        const roleId = proj?.Properties.ServiceRole["Fn::GetAtt"][0];
        const text = JSON.stringify(
            Object.values(policies)
                .filter((p) => JSON.stringify(p.Properties.Roles).includes(`"${roleId}"`))
                .flatMap((p) => p.Properties.PolicyDocument.Statement)
                .filter((s) => JSON.stringify(s.Action).includes("sts:AssumeRole"))
        );
        expect(text).toContain("role/vra-test-test");
        expect(text).not.toContain("vra-deploy-");
    });
});
