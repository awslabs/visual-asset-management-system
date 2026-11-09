/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// 0.5.0 pipeline features: CodeConnections sources (created PENDING, GitHub / GitHub Enterprise Server / GitLab /
// Bitbucket), S3 sources fed by vra, the hybrid scope, auto-rollback on failed tests, strict-IAM role modes
// (report / precreated / name prefix), the topology digest `vra pipeline status` compares, and the three-env
// live-test example. Offline: in-memory synth with cdk-nag.
import { mkdtempSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { App, Aspects } from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import {
    PipelineConfig,
    ReleaseEnv,
    SourceSpec,
    loadConfig,
    scopeEnvs,
    topologyDigest,
    validateConfig,
} from "../lib/config";
import { VraPipelineStack } from "../lib/vra-pipeline-stack";

const cdkContext = JSON.parse(readFileSync(join(__dirname, "..", "cdk.json"), "utf8")).context;
const BASE = join(__dirname, "..", "config", "pipeline.example.commercial-github.json");
const EXAMPLE = join(__dirname, "..", "examples", "three-env", "pipeline.json");
/** Same value engine/tests/test_pipeline.py expects from vra.pipeline.topology_digest for the example graph. */
const EXAMPLE_DIGEST = readFileSync(
    join(__dirname, "..", "examples", "three-env", "topology-digest.txt"),
    "utf8"
).trim();

const DEV: ReleaseEnv = { name: "dev", account: "222222222222" };
const TEST: ReleaseEnv = {
    name: "test",
    account: "333333333333",
    promotesFrom: "dev",
    requiresApproval: true,
};
const PROD: ReleaseEnv = {
    name: "prod",
    account: "444444444444",
    promotesFrom: "test",
    requiresApproval: true,
};

function base(
    extra: Partial<PipelineConfig> = {},
    envs: ReleaseEnv[] = [DEV, TEST, PROD]
): PipelineConfig {
    const cfg = JSON.parse(readFileSync(BASE, "utf8")) as PipelineConfig;
    return {
        ...cfg,
        codebuild: undefined,
        ...extra,
        sources: { ...cfg.sources, ...(extra.sources ?? {}) },
        release: { ...cfg.release, ...(extra.release ?? {}), envs },
    };
}

function synth(cfg: PipelineConfig, outdir?: string) {
    // report mode is what bin/app.ts sets for `vra pipeline plan` after validating the config file
    if (cfg.iamRoles?.mode !== "report") validateConfig(cfg);
    const app = new App({ context: cdkContext, ...(outdir ? { outdir } : {}) });
    const stack = new VraPipelineStack(app, "vra-pipeline", {
        config: cfg,
        env: { account: cfg.toolsAccount, region: cfg.region },
    });
    Aspects.of(app).add(new AwsSolutionsChecks());
    app.synth();
    const template = Template.fromStack(stack);
    const nag = [
        ...Annotations.fromStack(stack).findError("*", Match.stringLikeRegexp("AwsSolutions-.*")),
        ...Annotations.fromStack(stack).findWarning("*", Match.stringLikeRegexp("AwsSolutions-.*")),
    ].map((m) => `${m.id}: ${m.entry.data}`);
    return { stack, template, nag };
}

interface Stage {
    Name: string;
    Actions: Array<{
        Name: string;
        ActionTypeId: { Category: string; Provider: string };
        Configuration: Record<string, unknown>;
    }>;
}

function stages(template: Template, name = "vra-release"): Stage[] {
    const p = Object.values(template.findResources("AWS::CodePipeline::Pipeline")).find(
        (x) => x.Properties.Name === name
    );
    return (p?.Properties.Stages ?? []) as Stage[];
}

function projectEnv(template: Template, projectName: string): Record<string, string> {
    const p = Object.values(template.findResources("AWS::CodeBuild::Project")).find(
        (x) => x.Properties.Name === projectName
    );
    if (!p) throw new Error(`no project ${projectName}`);
    const vars = (p.Properties.Environment.EnvironmentVariables ?? []) as Array<{
        Name: string;
        Value: unknown;
    }>;
    return Object.fromEntries(vars.map((v) => [v.Name, JSON.stringify(v.Value)]));
}

function policyActions(template: Template): Map<string, string[]> {
    // role logical id -> actions of every inline policy attached to it
    const out = new Map<string, string[]>();
    for (const p of Object.values(template.findResources("AWS::IAM::Policy"))) {
        const acts = (
            p.Properties.PolicyDocument.Statement as Array<{ Action: string | string[] }>
        ).flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]));
        for (const r of p.Properties.Roles as Array<{ Ref: string }>) {
            out.set(r.Ref, [...(out.get(r.Ref) ?? []), ...acts]);
        }
    }
    return out;
}

const gh = (over: Partial<Extract<SourceSpec, { type: "codeconnections" }>> = {}): SourceSpec => ({
    type: "codeconnections",
    provider: "GitHub",
    repo: "example-org/vams-fork",
    branch: "main",
    ...over,
});

describe("CodeConnections sources", () => {
    const { template, nag } = synth(
        base({
            sources: {
                fork: gh(),
                vra: gh({ repo: "example-org/vams-release-automation", branch: "v0.5" }),
            },
        })
    );

    test("cdk-nag clean", () => expect(nag).toEqual([]));

    test("one GitHub connection is created (PENDING until the console handshake) and output", () => {
        template.resourceCountIs("AWS::CodeConnections::Connection", 1);
        template.hasResourceProperties("AWS::CodeConnections::Connection", {
            ProviderType: "GitHub",
            ConnectionName: "vra-github",
        });
        expect(Object.keys(template.findOutputs("ConnectionArn"))).toEqual(["ConnectionArn"]);
    });

    test("the created connection is retained when its source goes away (keeps the handshake; B-050-4)", () => {
        template.hasResource("AWS::CodeConnections::Connection", {
            DeletionPolicy: "Retain",
            UpdateReplacePolicy: "Retain",
        });
    });

    test("source action roles and every tier role may use the connection under both IAM prefixes", () => {
        const acts = policyActions(template);
        const roles = Object.keys(template.findResources("AWS::IAM::Role"));
        const sourceRoles = roles.filter((r) =>
            /SourceForkCodePipelineActionRole|SourceVraCodePipelineActionRole/.test(r)
        );
        expect(sourceRoles.length).toBe(4); // release + preview pipelines
        const tiers = roles.filter((r) => /^(BuildRole|DevRole|DeployRole)/.test(r));
        for (const r of [...sourceRoles, ...tiers]) {
            expect(acts.get(r)).toEqual(expect.arrayContaining(["codeconnections:UseConnection"]));
        }
    });

    test("full clone output for the fork (vra needs git history)", () => {
        const fork = stages(template)[0].Actions.find((a) => a.Name === "Fork");
        expect(fork?.ActionTypeId.Provider).toBe("CodeStarSourceConnection");
        expect(fork?.Configuration.OutputArtifactFormat).toBe("CODEBUILD_CLONE_REF");
        expect(fork?.Configuration.FullRepositoryId).toBe("example-org/vams-fork");
        expect(fork?.Configuration.BranchName).toBe("main");
    });
});

describe("GitHub Enterprise Server, GitLab self-managed with nested groups, Bitbucket", () => {
    const host = "arn:aws:codeconnections:us-east-1:111111111111:host/ghes-0123abcd";
    const { template, nag } = synth(
        base({
            sources: {
                fork: gh({
                    provider: "GitHubEnterpriseServer",
                    hostArn: host,
                    repo: "platform/vams-fork",
                }),
                forkPreview: gh({
                    provider: "GitLabSelfManaged",
                    hostArn: "arn:aws:codeconnections:us-east-1:111111111111:host/gitlab-0123abcd",
                    repo: "group/subgroup/vams-fork",
                    branch: "preview/main",
                }),
                vra: gh({
                    provider: "Bitbucket",
                    repo: "workspace/vams-release-automation",
                    branch: "v0.5",
                }),
            },
        })
    );

    test("cdk-nag clean", () => expect(nag).toEqual([]));

    test("one connection per provider and host", () => {
        template.resourceCountIs("AWS::CodeConnections::Connection", 3);
        template.hasResourceProperties("AWS::CodeConnections::Connection", {
            ProviderType: "GitHubEnterpriseServer",
            HostArn: host,
        });
        template.hasResourceProperties("AWS::CodeConnections::Connection", {
            ProviderType: "Bitbucket",
        });
    });

    test("a nested GitLab project keeps its full repository id", () => {
        const fork = stages(template, "vra-preview")[0].Actions.find((a) => a.Name === "Fork");
        expect(fork?.Configuration.FullRepositoryId).toBe("group/subgroup/vams-fork");
    });

    test("validation: a server provider needs a host or an existing connection", () => {
        expect(() =>
            validateConfig(
                base({ sources: { fork: gh({ provider: "GitHubEnterpriseServer" }), vra: gh() } })
            )
        ).toThrow(/needs hostArn/);
        expect(() =>
            validateConfig(base({ sources: { fork: gh({ hostArn: host }), vra: gh() } }))
        ).toThrow(/hostArn is only for/);
        expect(() =>
            validateConfig(
                base({ sources: { fork: gh({ provider: "Gitea" as never }), vra: gh() } })
            )
        ).toThrow(/provider must be one of/);
        expect(() =>
            validateConfig(base({ sources: { fork: gh({ repo: "nogroup" }), vra: gh() } }))
        ).toThrow(/owner\/repo/);
        expect(() =>
            validateConfig(
                base({ sources: { fork: gh({ connectionArn: "arn:aws:s3:::x" }), vra: gh() } })
            )
        ).toThrow(/not a CodeConnections connection ARN/);
    });
});

describe("S3 sources fed by vra (partitions without CodeConnections or CodeCommit)", () => {
    const { template, nag } = synth(
        base({
            sources: {
                fork: { type: "s3", objectKey: "fork/main.zip" },
                vra: { type: "s3", objectKey: "vra/vams-release-automation.zip" },
            },
            watch: {
                enabled: true,
                schedule: "cron(0 6 * * ? *)",
                command: "sync-merge",
                gitTokenSecretName: "vra/github-token",
            },
        })
    );

    test("cdk-nag clean", () => expect(nag).toEqual([]));

    test("the stack creates one encrypted, versioned source bucket; the pipeline does not poll it", () => {
        const fork = stages(template)[0].Actions.find((a) => a.Name === "Fork");
        expect(fork?.ActionTypeId.Provider).toBe("S3");
        expect(fork?.Configuration.PollForSourceChanges).toBe(false);
        expect(Object.keys(template.findOutputs("SourceBucketName"))).toEqual(["SourceBucketName"]);
        template.hasResourceProperties("AWS::S3::Bucket", {
            VersioningConfiguration: { Status: "Enabled" },
            LoggingConfiguration: Match.objectLike({ LogFilePrefix: "source/" }),
        });
    });

    test("the watch job uploads the fork zip, starts the pipeline and reads the package zip", () => {
        const env = projectEnv(template, "vra-watch");
        expect(env.VRA_SOURCE_RELEASE_KEY).toBe('"fork/main.zip"');
        expect(env.VRA_RELEASE_PIPELINE).toBe('"vra-release"');
        expect(env.VRA_SOURCE_BUCKET).toBeDefined();
        expect(env.VRA_PACKAGE_S3_URI).toContain("vra/vams-release-automation.zip");
        expect(env.VRA_PACKAGE_REPO).toBeUndefined();
        const roleActions = (prefix: string) =>
            policyActions(template).get(
                Object.keys(template.findResources("AWS::IAM::Role")).find((r) =>
                    r.startsWith(prefix)
                ) as string
            ) ?? [];
        // R50-12: only the watch tier feeds the sources and starts the pipelines
        expect(roleActions("WatchRole")).toEqual(
            expect.arrayContaining(["s3:PutObject", "codepipeline:StartPipelineExecution"])
        );
        for (const tier of ["DevRole", "BuildRole"]) {
            expect(roleActions(tier)).not.toContain("codepipeline:StartPipelineExecution");
        }
        const policies = Object.values(template.findResources("AWS::S3::BucketPolicy"));
        expect(JSON.stringify(policies)).toContain("OnlyTheWatchTierWritesSources");
        const watch = Object.values(template.findResources("AWS::CodeBuild::Project")).find(
            (p) => p.Properties.Name === "vra-watch"
        );
        expect(String(watch?.Properties.Source.BuildSpec)).toContain("pipeline-watch.sh");
    });

    test("B-050-5: sourceWriterArns lets the operator bootstrap the package zip; nothing else changes", () => {
        const operator = "arn:aws:iam::111111111111:role/ReleaseOperator";
        const cfg = base({
            sources: {
                fork: { type: "s3", objectKey: "fork/main.zip" },
                vra: { type: "s3", objectKey: "vra/vams-release-automation.zip" },
            },
            watch: { enabled: true, schedule: "rate(1 day)", command: "sync-merge" },
            sourceWriterArns: [operator],
        });
        const { template: t, nag: n } = synth(cfg);
        expect(n).toEqual([]);
        const deny = (tm: Template) =>
            Object.values(tm.findResources("AWS::S3::BucketPolicy"))
                .flatMap((p) => p.Properties.PolicyDocument.Statement)
                .find((s: { Sid?: string }) => s.Sid === "OnlyTheWatchTierWritesSources");
        const allowed = deny(t).Condition.ArnNotEquals["aws:PrincipalArn"];
        expect(allowed).toHaveLength(2);
        expect(allowed[1]).toBe(operator);
        expect(JSON.stringify(allowed[0])).toContain("WatchRole");
        // default: the watch tier alone
        expect(deny(template).Condition.ArnNotEquals["aws:PrincipalArn"]).toHaveLength(1);
        for (const bad of [
            "arn:aws:iam::111111111111:role/*",
            "arn:aws:iam::111111111111:user/me",
            "*",
        ])
            expect(() => validateConfig({ ...cfg, sourceWriterArns: [bad] })).toThrow(
                /not an exact IAM role ARN/
            );
    });

    test("validation: a git package source still needs a pinned ref for the watch", () => {
        expect(() =>
            validateConfig(
                base({
                    watch: {
                        enabled: true,
                        schedule: "rate(1 day)",
                        packageRepoUrl: "https://x.example/p.git",
                    },
                })
            )
        ).toThrow(/packageRef must pin/);
    });
});

describe("hybrid tier: scope dev", () => {
    test("only the ungated chain gets stages, never an approval", () => {
        const QA: ReleaseEnv = { name: "qa", account: "222222222222", promotesFrom: "dev" };
        const cfg = base({ scope: "dev", automationMode: "hybrid" }, [DEV, QA, TEST, PROD]);
        expect(scopeEnvs(cfg.release.envs ?? [], "dev").map((e) => e.name)).toEqual(["dev", "qa"]);
        const { template, nag } = synth(cfg);
        expect(nag).toEqual([]);
        const names = stages(template).map((s) => s.Name);
        expect(names).toEqual(["Source", "Validate", "Build", "Deploy-dev", "Deploy-qa"]);
        template.hasResourceProperties("AWS::CodeBuild::Project", { Name: "vra-test-dev" });
        expect(
            Object.values(template.findResources("AWS::CodeBuild::Project")).some(
                (p) => p.Properties.Name === "vra-deploy-prod"
            )
        ).toBe(false);
    });

    test("hybrid needs scope dev; scope dev needs an ungated root", () => {
        expect(() => validateConfig(base({ automationMode: "hybrid" }))).toThrow(
            /hybrid needs scope "dev"/
        );
        expect(() =>
            validateConfig(base({ scope: "dev" }, [{ ...DEV, requiresApproval: true }]))
        ).toThrow(/at least one release root env without requiresApproval/);
    });
});

describe("auto-rollback on failed tests", () => {
    test("ungated envs: the Test project rolls back (privileged, one build at a time); gated envs never", () => {
        const { template, nag } = synth(base());
        expect(nag).toEqual([]);
        expect(projectEnv(template, "vra-test-dev").VRA_ROLLBACK_ON_TEST_FAILURE).toBe('"true"');
        expect(projectEnv(template, "vra-test-test").VRA_ROLLBACK_ON_TEST_FAILURE).toBe('"false"');
        template.hasResourceProperties("AWS::CodeBuild::Project", {
            Name: "vra-test-dev",
            Environment: Match.objectLike({ PrivilegedMode: true }),
            ConcurrentBuildLimit: 1,
        });
    });

    test("off with autoRollback false or isolateTests; explicit true with isolateTests is refused", () => {
        const off = synth(base({ release: { autoRollback: false } })).template;
        expect(projectEnv(off, "vra-test-dev").VRA_ROLLBACK_ON_TEST_FAILURE).toBe('"false"');
        const iso = synth(base({ release: { isolateTests: true } })).template;
        expect(projectEnv(iso, "vra-test-dev").VRA_ROLLBACK_ON_TEST_FAILURE).toBe('"false"');
        expect(() =>
            validateConfig(base({ release: { isolateTests: true, autoRollback: true } }))
        ).toThrow(/autoRollback/);
    });

    test("build, deploy and test jobs share the bundle store in the artifact bucket (B-050-21)", () => {
        const { template } = synth(base());
        const bucket = Object.keys(template.findResources("AWS::S3::Bucket")).find((k) =>
            k.startsWith("Artifacts")
        );
        expect(bucket).toBeDefined();
        for (const name of ["vra-build", "vra-deploy-dev", "vra-test-dev", "vra-deploy-prod"]) {
            const v = projectEnv(template, name).VRA_BUNDLE_STORE;
            expect(v).toContain(`"Ref":"${bucket}"`);
            expect(v).toContain("/vra-bundles");
        }
        // the build tier writes (BuildOutput grant), the dev tier that rolls back reads the bucket
        const acts = policyActions(template);
        const roles = Object.keys(template.findResources("AWS::IAM::Role"));
        const role = (p: string) => roles.find((r) => r.startsWith(p)) as string;
        expect(acts.get(role("BuildRole"))).toEqual(expect.arrayContaining(["s3:PutObject"]));
        expect(acts.get(role("DevRole"))).toEqual(expect.arrayContaining(["s3:GetObject*"]));
    });

    test("the test buildspec runs vra rollback only for rollbackPolicy auto and still fails the action", () => {
        const spec = readFileSync(
            join(__dirname, "..", "..", "codebuild", "buildspec-test.yml"),
            "utf8"
        );
        expect(spec).toContain("VRA_ROLLBACK_ON_TEST_FAILURE");
        expect(spec).toContain("pipeline_ci.py rollback-policy");
        expect(spec).toMatch(/rollback --env "\$\{VRA_ENV\}"/);
        expect(spec).toContain('exit "${code}"');
    });
});

describe("strict IAM: report, precreated roles, role name prefix", () => {
    test("report mode writes iam-policy-report.json listing every role (vra pipeline plan reads it)", () => {
        const out = mkdtempSync(join(tmpdir(), "vra-iam-report-"));
        const cfg = base();
        validateConfig(cfg);
        synth({ ...cfg, iamRoles: { mode: "report" } }, out);
        const report = JSON.parse(readFileSync(join(out, "iam-policy-report.json"), "utf8")) as {
            roles: Array<{ roleConstructPath: string; identityPolicyStatements: unknown[] }>;
        };
        const paths = report.roles.map((r) => r.roleConstructPath);
        expect(paths).toEqual(
            expect.arrayContaining(["vra-pipeline/BuildRole", "vra-pipeline/DevRole"])
        );
        expect(paths.some((p) => p.includes("CodePipelineActionRole"))).toBe(true);
    });

    test("a config file cannot ask for report mode (not deployable)", () => {
        expect(() => validateConfig(base({ iamRoles: { mode: "report" } }))).toThrow(
            /not deployable/
        );
        expect(() => validateConfig(base({ iamRoles: { mode: "precreated" } }))).toThrow(
            /precreatedRoles/
        );
    });

    test("precreated: every role comes from IT, the template creates none", () => {
        const out = mkdtempSync(join(tmpdir(), "vra-iam-report-"));
        const cfg = base();
        validateConfig(cfg);
        synth({ ...cfg, iamRoles: { mode: "report" } }, out);
        const report = JSON.parse(readFileSync(join(out, "iam-policy-report.json"), "utf8")) as {
            roles: Array<{ roleConstructPath: string }>;
        };
        const map = Object.fromEntries(
            report.roles.map((r, i) => [r.roleConstructPath, `it-vra-role-${i}`])
        );
        const { template, nag } = synth(
            base({ iamRoles: { mode: "precreated", precreatedRoles: map } })
        );
        template.resourceCountIs("AWS::IAM::Role", 0);
        expect(nag).toEqual([]);
        const missing = { ...map };
        delete missing["vra-pipeline/BuildRole"];
        // a role IT has not created is a synth error (`cdk synth` exits non-zero; vra pipeline plan reports it)
        const { stack } = synth(
            base({ iamRoles: { mode: "precreated", precreatedRoles: missing } })
        );
        const errors = Annotations.fromStack(stack)
            .findError("*", Match.anyValue())
            .map((e) => String(e.entry.data));
        expect(errors.some((e) => e.includes("vra-pipeline/BuildRole"))).toBe(true);
    });

    test("roleNamePrefix: every role name starts with the prefix and fits IAM's 64 characters", () => {
        const { template, nag } = synth(base({ iamRoles: { roleNamePrefix: "app-" } }));
        expect(nag).toEqual([]);
        const roles = Object.values(template.findResources("AWS::IAM::Role"));
        expect(roles.length).toBeGreaterThan(5);
        for (const r of roles) {
            const name = r.Properties.RoleName;
            const text = typeof name === "string" ? name : JSON.stringify(name);
            expect(text.startsWith("app-") || text.includes('"app-vra-codebuild-')).toBe(true);
            if (typeof name === "string") expect(name.length).toBeLessThanOrEqual(64);
        }
    });
});

describe("pipeline+actions: hand-off from GitHub Actions", () => {
    const provider = "arn:aws:iam::111111111111:oidc-provider/token.actions.githubusercontent.com";
    const { template, nag } = synth(
        base({
            automationMode: "pipeline+actions",
            sources: {
                fork: gh({ triggerOnPush: false }),
                vra: gh({ repo: "example-org/vra", branch: "v0.5" }),
            },
            actionsHandoff: {
                oidcProviderArn: provider,
                subjects: ["repo:example-org/vams-fork:environment:vra-handoff"],
            },
        })
    );

    test("cdk-nag clean", () => expect(nag).toEqual([]));

    test("the fork source does not start the pipeline on a push (the hand-off starts it)", () => {
        const fork = stages(template)[0].Actions.find((a) => a.Name === "Fork");
        expect(fork?.Configuration.DetectChanges).toBe(false);
        const preview = stages(template, "vra-preview")[0].Actions.find((a) => a.Name === "Fork");
        expect(preview?.Configuration.DetectChanges).toBe(true);
    });

    test("the hand-off role trusts only the environment subject and may only start the release pipeline", () => {
        template.hasResourceProperties("AWS::IAM::Role", {
            AssumeRolePolicyDocument: Match.objectLike({
                Statement: [
                    Match.objectLike({
                        Action: "sts:AssumeRoleWithWebIdentity",
                        Principal: { Federated: provider },
                        Condition: {
                            StringEquals: {
                                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                            },
                            StringLike: {
                                "token.actions.githubusercontent.com:sub": [
                                    "repo:example-org/vams-fork:environment:vra-handoff",
                                ],
                            },
                        },
                    }),
                ],
            }),
        });
        const role = Object.keys(template.findResources("AWS::IAM::Role")).find((r) =>
            r.startsWith("ActionsHandoffRole")
        ) as string;
        expect(policyActions(template).get(role)?.sort()).toEqual([
            "codepipeline:GetPipelineExecution",
            "codepipeline:StartPipelineExecution",
        ]);
        expect(Object.keys(template.findOutputs("HandoffRoleArn"))).toEqual(["HandoffRoleArn"]);
    });

    test("validation: subjects are required and never a bare *", () => {
        expect(() =>
            validateConfig(base({ actionsHandoff: { oidcProviderArn: provider, subjects: ["*"] } }))
        ).toThrow(/subjects/);
    });
});

describe("three-env live-test example (examples/three-env)", () => {
    const cfg = loadConfig(EXAMPLE);
    const { template, nag } = synth(cfg);

    test("cdk-nag clean", () => expect(nag).toEqual([]));

    test("stages follow the ledger graph: dev, approval, test, final approval, prod", () => {
        expect(stages(template).map((s) => s.Name)).toEqual([
            "Source",
            "Validate",
            "Build",
            "Deploy-dev",
            "Approve-test",
            "Deploy-test",
            "Approve-prod",
            "Deploy-prod",
        ]);
    });

    test("the fork comes from a created GitHub connection on test/fork-main; the watch runs sync-merge", () => {
        const fork = stages(template)[0].Actions.find((a) => a.Name === "Fork");
        expect(fork?.Configuration.BranchName).toBe("test/fork-main");
        template.resourceCountIs("AWS::CodeConnections::Connection", 1);
        const watch = Object.values(template.findResources("AWS::CodeBuild::Project")).find(
            (p) => p.Properties.Name === "vra-watch"
        );
        expect(String(watch?.Properties.Source.BuildSpec)).toContain("pipeline-watch.sh");
        template.hasResourceProperties("AWS::Scheduler::Schedule", {
            ScheduleExpression: "cron(0 6 * * ? *)",
        });
    });

    test("TopologyDigest output matches what vra pipeline status computes from the ledger graph", () => {
        expect(topologyDigest(cfg.release.envs ?? [])).toBe(EXAMPLE_DIGEST);
        expect(template.findOutputs("TopologyDigest").TopologyDigest.Value).toBe(EXAMPLE_DIGEST);
    });
});

describe("REVIEW-050 fix wave", () => {
    test("R50-18: gated envs isolate their tests by default; non-isolated tests on a prod-class env are refused", () => {
        const { template, nag } = synth(base());
        expect(nag).toEqual([]);
        const ids = Object.keys(template.findResources("AWS::IAM::Role"));
        expect(ids.some((n) => n.startsWith("TestRoleprod"))).toBe(true);
        expect(ids.some((n) => n.startsWith("TestRoledev"))).toBe(false); // ungated dev keeps autoRollback
        const prod = { ...PROD, tier: "prod" };
        expect(() =>
            validateConfig(base({ release: { isolateTests: false } }, [DEV, TEST, prod]))
        ).toThrow(/prod-class/);
    });

    test("R50-16: the hand-off role trusts a GHES issuer by its own keys and the immutable subject", () => {
        const provider = "arn:aws:iam::111111111111:oidc-provider/ghes.example.com/_services/token";
        const { template } = synth(
            base({
                automationMode: "pipeline+actions",
                sources: {
                    fork: gh({ triggerOnPush: false }),
                    vra: gh({ repo: "example-org/vra" }),
                },
                actionsHandoff: {
                    oidcProviderArn: provider,
                    repositoryIds: {
                        repository: "example-org/vams-fork",
                        ownerId: "123",
                        repositoryId: "456",
                    },
                },
            })
        );
        const text = JSON.stringify(template.findResources("AWS::IAM::Role"));
        expect(text).toContain("ghes.example.com/_services/token:sub");
        expect(text).toContain("repo:example-org@123/vams-fork@456:environment:vra-handoff");
    });

    test("R50-3: every tier reads IT packages and pipeline plans, writes them create-only", () => {
        const stateStore = {
            bucketName: "vra-state-111111111111-us-east-1",
            lockTableName: "vra-locks",
            keyArn: "arn:aws:kms:us-east-1:111111111111:key/11111111-2222-3333-4444-555555555555",
        };
        const { template } = synth(base({ stateStore }));
        const text = JSON.stringify(template.findResources("AWS::IAM::ManagedPolicy"));
        expect(text).toContain("/vra/iam/*");
        expect(text).toContain("/vra/pipeline/*");
        expect(text).toContain("/vra/iam/*/applied.json");
    });
});
