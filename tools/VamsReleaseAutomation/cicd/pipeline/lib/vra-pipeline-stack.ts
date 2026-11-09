/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from "fs";
import { join } from "path";
import { createHash } from "crypto";
import {
    Aspects,
    Aws,
    CfnOutput,
    Duration,
    IAspect,
    RemovalPolicy,
    Stack,
    StackProps,
    Tags,
    TimeZone,
    aws_codebuild as codebuild,
    aws_codecommit as codecommit,
    aws_codeconnections as codeconnections,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as cpa,
    aws_ec2 as ec2,
    aws_events as events,
    aws_events_targets as eventsTargets,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
    aws_scheduler_targets as schedulerTargets,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
} from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import { Construct, IConstruct } from "constructs";
import { parse as parseYaml } from "yaml";
import {
    EnvTarget,
    PipelineConfig,
    PlannedAction,
    ReleaseEnv,
    SourceSpec,
    planRelease,
    handoffSubjects,
    scopeEnvs,
    testsIsolated,
    topologyDigest,
    watchCommand,
} from "./config";

/** Whether a source starts the pipeline on a push (git sources: triggerOnPush, default true). */
function sourceTrigger(s: SourceSpec): boolean {
    return "triggerOnPush" in s && s.triggerOnPush === false ? false : true;
}

/**
 * Prefixes the name of every IAM role of the stack (IT naming rule, like VAMS IamRoleNamePrefix). Roles without a
 * name get `<prefix><stack prefix>-<construct id>-<hash>` (64 characters at most).
 */
class RoleNamePrefix implements IAspect {
    constructor(private readonly rolePrefix: string, private readonly stackPrefix: string) {}

    visit(node: IConstruct): void {
        if (!(node instanceof iam.CfnRole)) return;
        const current = node.roleName;
        if (current === undefined) {
            const parent = node.node.scope?.node.id ?? "role";
            const hash = createHash("sha256").update(node.node.path).digest("hex").slice(0, 8);
            const base = `${this.rolePrefix}${this.stackPrefix}-`;
            const room = 64 - base.length - hash.length - 1;
            node.roleName = `${base}${parent
                .replace(/[^A-Za-z0-9+=,.@_-]/g, "")
                .slice(0, Math.max(room, 1))}-${hash}`;
        } else if (!current.startsWith(this.rolePrefix)) {
            node.roleName = `${this.rolePrefix}${current}`;
        }
    }
}

export interface VraPipelineStackProps extends StackProps {
    readonly config: PipelineConfig;
    /** Directory holding buildspec-*.yml (default: ../../codebuild relative to this file). */
    readonly buildspecDir?: string;
}

const DEFAULT_IMAGE = "aws/codebuild/amazonlinux-x86_64-standard:5.0";

interface EnvProjects {
    deploy: codebuild.IProject;
    test: codebuild.IProject;
}

/**
 * Optional CI/CD lane for vams-release-automation. Every build step is a thin wrapper over the
 * `vra` CLI (cicd/codebuild/buildspec-*.yml); every gate (approver list, separation of duties,
 * promotion order, change window) is enforced by vra itself. This stack only adds:
 *  - a release pipeline whose stages follow the ledger's promotion graph: source -> validate -> build ->
 *    one stage per depth of the graph (siblings in parallel, each promoting from its own promotesFrom), with a
 *    manual approval only before envs that require one
 *  - a preview pipeline for dev-preview (VAMS `development` lane, never promoted)
 *  - a daily EventBridge Scheduler schedule starting the `vra watch` CodeBuild project
 *  - failure notifications (EventBridge -> SNS), KMS-encrypted artifacts and logs, optional VPC
 */
export class VraPipelineStack extends Stack {
    private readonly cfg: PipelineConfig;
    private readonly prefix: string;
    private readonly buildspecDir: string;
    private readonly key: kms.Key;
    private readonly releaseEnvs: ReleaseEnv[];
    /** One role per trust tier: build (no env deploy role), dev (root ungated envs + dev-preview + watch), one per other env. */
    private readonly buildRole: iam.Role;
    private readonly devRole: iam.Role;
    /** Scheduled watch / push-source tier (watch.enabled): the only writer of the S3 sources. */
    private watchRole?: iam.Role;
    private readonly envRoles = new Map<string, iam.Role>();
    private readonly testRoles = new Map<string, iam.Role>();
    private readonly allRoles: iam.Role[] = [];
    /** Envs whose Test action rolls the env back when the tests fail. */
    private rollbackEnvs = new Set<string>();
    /** One managed policy per tier role with its state-store statements (stateStore set). */
    private readonly statePolicies: iam.ManagedPolicy[] = [];
    private readonly logGroup: logs.LogGroup;
    private accessLogs: s3.Bucket;
    private readonly vpcProps: Pick<
        codebuild.ProjectProps,
        "vpc" | "subnetSelection" | "securityGroups"
    >;
    private homeToken?: secretsmanager.ISecret;

    constructor(scope: Construct, id: string, props: VraPipelineStackProps) {
        super(scope, id, props);
        this.cfg = props.config;
        this.prefix = this.cfg.prefix ?? "vra";
        this.buildspecDir = props.buildspecDir ?? join(__dirname, "..", "..", "codebuild");
        // hybrid tier (scope "dev"): only the envs reachable without an approval get stages
        this.releaseEnvs = scopeEnvs(this.cfg.release.envs ?? [], this.cfg.scope);

        // Strict-IAM customers (same mechanism as VAMS useCustomVamsStackRoles): "report" writes
        // cdk.out/iam-policy-report.json for the IT package; "precreated" uses IT's roles and creates none.
        const iamMode = this.cfg.iamRoles?.mode ?? "create";
        if (iamMode === "report") {
            iam.Role.customizeRoles(this, { preventSynthesis: false });
        } else if (iamMode === "precreated") {
            iam.Role.customizeRoles(this, {
                usePrecreatedRoles: this.cfg.iamRoles?.precreatedRoles ?? {},
            });
        }
        if (this.cfg.iamRoles?.roleNamePrefix) {
            Aspects.of(this).add(new RoleNamePrefix(this.cfg.iamRoles.roleNamePrefix, this.prefix));
        }
        if (this.cfg.automationMode)
            Tags.of(this).add("vra:automation-mode", this.cfg.automationMode);

        if (this.cfg.permissionsBoundaryArn) {
            iam.PermissionsBoundary.of(this).apply(
                iam.ManagedPolicy.fromManagedPolicyArn(
                    this,
                    "Boundary",
                    this.cfg.permissionsBoundaryArn
                )
            );
        }

        // ------------------------------------------------------------------ encryption + storage
        this.key = new kms.Key(this, "PipelineKey", {
            description: `${this.prefix} pipeline artifacts and build logs`,
            enableKeyRotation: true,
            alias: `alias/${this.prefix}-pipeline`,
            removalPolicy: RemovalPolicy.RETAIN,
        });
        this.key.addToResourcePolicy(
            new iam.PolicyStatement({
                sid: "CloudWatchLogs",
                principals: [new iam.ServicePrincipal(`logs.${Aws.REGION}.${Aws.URL_SUFFIX}`)],
                actions: [
                    "kms:Encrypt*",
                    "kms:Decrypt*",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:Describe*",
                ],
                resources: ["*"],
                conditions: {
                    ArnLike: {
                        "kms:EncryptionContext:aws:logs:arn": `arn:${Aws.PARTITION}:logs:${Aws.REGION}:${Aws.ACCOUNT_ID}:log-group:/${this.prefix}/*`,
                    },
                },
            })
        );

        const accessLogs = (this.accessLogs = new s3.Bucket(this, "AccessLogs", {
            // S3 server access logs only support SSE-S3 on the destination bucket.
            encryption: s3.BucketEncryption.S3_MANAGED,
            enforceSSL: true,
            minimumTLSVersion: 1.2,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned: true,
            lifecycleRules: [
                { expiration: Duration.days(365), noncurrentVersionExpiration: Duration.days(30) },
            ],
            removalPolicy: RemovalPolicy.RETAIN,
        }));

        const artifactBucket = new s3.Bucket(this, "Artifacts", {
            encryption: s3.BucketEncryption.KMS,
            encryptionKey: this.key,
            bucketKeyEnabled: true,
            enforceSSL: true,
            minimumTLSVersion: 1.2,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned: true,
            serverAccessLogsBucket: accessLogs,
            serverAccessLogsPrefix: "artifacts/",
            lifecycleRules: [
                {
                    noncurrentVersionExpiration: Duration.days(30),
                    abortIncompleteMultipartUploadAfter: Duration.days(7),
                },
            ],
            removalPolicy: RemovalPolicy.RETAIN,
        });

        this.logGroup = new logs.LogGroup(this, "BuildLogs", {
            logGroupName: `/${this.prefix}/codebuild`,
            retention: logs.RetentionDays.ONE_YEAR,
            encryptionKey: this.key,
            removalPolicy: RemovalPolicy.RETAIN,
        });

        const topic = sns.Topic.fromTopicArn(
            this,
            "Notifications",
            this.cfg.notifications.topicArn
        );
        if (this.cfg.home.tokenSecretName) {
            this.homeToken = secretsmanager.Secret.fromSecretNameV2(
                this,
                "HomeToken",
                this.cfg.home.tokenSecretName
            );
        }

        // ------------------------------------------------------------------ CodeBuild roles, one per trust tier
        // The build tier runs npm ci, unit suites and customer code: it can never assume an env deploy role.
        // The dev tier (Validate, root envs without approval, dev-preview, the scheduled watch) reaches only those
        // envs. Every other env (gated, or promoted from another env) has its own role, used only by its projects.
        // dev-preview runs upstream `development` code (npm ci of web/e2e): it gets a preview tier of its own, which
        // can neither write the release pipeline's source nor start it (R50-12)
        const previewEnv: EnvTarget | undefined = this.cfg.preview?.env;
        const devTierEnvs: EnvTarget[] = this.releaseEnvs.filter(
            (e) => !e.promotesFrom && !e.requiresApproval
        );
        const ownTierEnvs = this.releaseEnvs.filter(
            (e) => !devTierEnvs.some((d) => d.name === e.name)
        );
        const allEnvs: EnvTarget[] = [
            ...devTierEnvs,
            ...(previewEnv ? [previewEnv] : []),
            ...ownTierEnvs,
        ];
        this.buildRole = this.tierRole(
            "BuildRole",
            "build",
            "vra build: synth with build-only vra-synth-* roles, never an env deploy role"
        );
        // every run holds a run lease (run#<runId>); the build takes no env lock
        this.grantCommon(this.buildRole, topic, []);
        this.grantAssume(
            this.buildRole,
            "AssumeSynthRoles",
            allEnvs.map((e) => this.roleArn(e, e.synthRoleName, "synth"))
        );
        this.devRole = this.tierRole(
            "DevRole",
            "dev",
            "vra validate / ungated root envs deploy and test"
        );
        this.grantCommon(
            this.devRole,
            topic,
            devTierEnvs.map((e) => e.name)
        );
        if (devTierEnvs.length > 0) {
            this.grantAssume(
                this.devRole,
                "AssumeDevEnvRoles",
                devTierEnvs.map((e) => this.roleArn(e, e.deployRoleName, "deploy"))
            );
        }
        if (previewEnv) {
            const role = this.tierRole(
                "PreviewRole",
                "preview",
                `vra deploy + test of ${previewEnv.name} only (upstream development code; no release source)`
            );
            this.grantCommon(role, topic, [previewEnv.name]);
            this.grantAssume(role, "AssumePreviewEnvRole", [
                this.roleArn(previewEnv, previewEnv.deployRoleName, "deploy"),
            ]);
            this.envRoles.set(previewEnv.name, role);
        }
        if (this.cfg.watch.enabled) {
            // the scheduled watch / `vra pipeline push-source`: the only tier that feeds the S3 sources and starts
            // the pipelines; it runs vra sync / merge, never an env's code (watch.command sync-merge or sync)
            this.watchRole = this.tierRole(
                "WatchRole",
                "watch",
                "vra scheduled sync / merge and push-source (feeds the S3 sources, starts the pipelines)"
            );
            this.grantCommon(this.watchRole, topic, ["watch"]);
        }
        for (const env of ownTierEnvs) {
            const role = this.tierRole(
                `DeployRole-${env.name}`,
                `deploy-${env.name}`,
                `vra deploy / promote + test of ${env.name} only`
            );
            this.grantCommon(role, topic, [env.name]);
            this.grantAssume(role, "AssumeOwnEnvRole", [
                this.roleArn(env, env.deployRoleName, "deploy"),
            ]);
            this.envRoles.set(env.name, role);
        }
        // live tests under a test-only tier: every approval-gated env by default (release.isolateTests unset),
        // every env with isolateTests true, none with false (refused for prod-class envs, R50-18)
        {
            for (const env of allEnvs.filter((e) => testsIsolated(this.cfg, e))) {
                const role = this.tierRole(
                    `TestRole-${env.name}`,
                    `test-${env.name}`,
                    `vra live tests of ${env.name} only (vra-test-${env.name}, no deploy role)`
                );
                this.grantCommon(role, topic, []);
                this.grantAssume(role, "AssumeOwnTestRole", [
                    this.roleArn(env, env.testRoleName, "test"),
                ]);
                this.testRoles.set(env.name, role);
            }
        }
        this.grantCodeCommit();
        if (this.cfg.signingKeyArn) {
            // only the build tier signs; every deploying tier verifies (deploy, promote, rollback refuse
            // unsigned or invalid manifests and release tags when the ledger enables build.signing)
            this.buildRole.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "SignBuilds",
                    actions: ["kms:Sign", "kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"],
                    resources: [this.cfg.signingKeyArn],
                })
            );
            for (const role of this.allRoles.filter((r) => r !== this.buildRole)) {
                role.addToPrincipalPolicy(
                    new iam.PolicyStatement({
                        sid: "VerifyBuildSignatures",
                        actions: ["kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"],
                        resources: [this.cfg.signingKeyArn],
                    })
                );
            }
        }

        // env.yaml approvalSigningKeyArn: the tier that runs an env's gated actions verifies its approval records
        const verifyKeys = new Map<iam.Role, string[]>();
        for (const env of allEnvs.filter((e) => e.approvalSigningKeyArn)) {
            const role = this.envRoles.get(env.name) ?? this.devRole;
            verifyKeys.set(role, [
                ...(verifyKeys.get(role) ?? []),
                env.approvalSigningKeyArn as string,
            ]);
        }
        for (const [role, keys] of verifyKeys) {
            role.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "VerifyApprovalSignatures",
                    actions: ["kms:Verify", "kms:DescribeKey"],
                    resources: Array.from(new Set(keys)).sort(),
                })
            );
        }

        // ------------------------------------------------------------------ optional VPC
        this.vpcProps = this.buildVpcProps();

        // ------------------------------------------------------------------ projects
        const validate = this.project("Validate", "buildspec-validate.yml", this.devRole, {
            privileged: false,
            compute: codebuild.ComputeType.SMALL,
            timeout: Duration.minutes(30),
            extraEnv: {
                // the topology this pipeline was built from; Validate fails once the ledger's differs
                VRA_PIPELINE_TOPOLOGY: JSON.stringify({
                    release: {
                        envs: this.releaseEnvs.map((e) => ({
                            name: e.name,
                            account: e.account,
                            promotesFrom: e.promotesFrom ?? null,
                            requiresApproval: Boolean(e.requiresApproval),
                            // from ledgerGraph only: release.envs configs do not state it
                            ...(e.requiredSuites ? { requiredSuites: e.requiredSuites } : {}),
                        })),
                    },
                }),
            },
        });
        // Bundle store (B-050-21): with a git home that does not push artifacts, a CI job holds only its own
        // execution's bundle. The build mirrors every bundle (create-only) below this prefix of the artifact
        // bucket and a rollback fetches the previous build's bundle from it. The build tier writes (it has
        // read/write on the bucket for BuildOutput); the deploy and test tiers read (their input artifacts).
        const bundleStore = { VRA_BUNDLE_STORE: `s3://${artifactBucket.bucketName}/vra-bundles` };
        const build = this.project("Build", "buildspec-build.yml", this.buildRole, {
            privileged: true,
            compute: codebuild.ComputeType.LARGE,
            timeout: Duration.hours(2),
            extraEnv: bundleStore,
        });
        const envProjects = new Map<string, EnvProjects>();
        // auto-rollback on a failed Test action: envs without approval (the dev tier), under their deploy tier
        const autoRollback =
            (this.cfg.release.autoRollback ?? true) && this.cfg.release.isolateTests !== true;
        this.rollbackEnvs = new Set(
            autoRollback
                ? [...devTierEnvs, ...(previewEnv ? [previewEnv] : [])]
                      .filter((e) => !testsIsolated(this.cfg, e))
                      .map((e) => e.name)
                : []
        );
        for (const env of allEnvs) {
            const role = this.envRoles.get(env.name) ?? this.devRole;
            const rollback = this.rollbackEnvs.has(env.name);
            envProjects.set(env.name, {
                deploy: this.project(`Deploy-${env.name}`, "buildspec-deploy.yml", role, {
                    privileged: true,
                    compute: codebuild.ComputeType.MEDIUM,
                    timeout: Duration.hours(8),
                    // one deploy of an env at a time; the vra env lock is the second guard
                    concurrentBuildLimit: 1,
                    extraEnv: bundleStore,
                }),
                test: this.project(
                    `Test-${env.name}`,
                    "buildspec-test.yml",
                    this.testRoles.get(env.name) ?? role,
                    {
                        // a rollback redeploys the previous build (CDK docker assets)
                        privileged: rollback,
                        compute: codebuild.ComputeType.MEDIUM,
                        timeout: rollback ? Duration.hours(8) : Duration.hours(2),
                        concurrentBuildLimit: rollback ? 1 : undefined,
                        extraEnv: {
                            VRA_ROLLBACK_ON_TEST_FAILURE: rollback ? "true" : "false",
                            ...bundleStore,
                        },
                    }
                ),
            });
        }

        // ------------------------------------------------------------------ release pipeline
        const releaseName = `${this.prefix}-release`;
        const release = this.releasePipeline(
            releaseName,
            artifactBucket,
            topic,
            { validate, build },
            envProjects,
            devTierEnvs.filter((e) => this.releaseEnvs.some((r) => r.name === e.name))
        );

        // ------------------------------------------------------------------ preview pipeline
        let preview: codepipeline.Pipeline | undefined;
        if (this.cfg.preview && this.cfg.sources.forkPreview) {
            preview = this.previewPipeline(
                `${this.prefix}-preview`,
                artifactBucket,
                { build },
                envProjects
            );
        }

        // Approval binding: a gated deploy reads the approval of ITS pipeline execution (ListActionExecutions
        // filtered by execution id); every tier reads its execution's trigger (the run initiator, vra.identity).
        const pipelineArns = [release.pipelineArn, ...(preview ? [preview.pipelineArn] : [])];
        for (const role of this.allRoles) {
            role.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "ReadOwnPipelineExecution",
                    actions: ["codepipeline:GetPipelineExecution"],
                    resources: pipelineArns,
                })
            );
        }
        for (const env of this.releaseEnvs.filter((e) => e.requiresApproval)) {
            (this.envRoles.get(env.name) ?? this.devRole).addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "ReadApprovalOfThisExecution",
                    actions: ["codepipeline:ListActionExecutions", "codepipeline:GetPipelineState"],
                    resources: [release.pipelineArn],
                })
            );
        }

        // ------------------------------------------------------------------ CodeConnections
        // CDK grants codestar-connections:UseConnection; IAM matches the action prefix to the ARN prefix, so a
        // codeconnections ARN (every connection created since July 2024, and the ones this stack creates) also
        // needs codeconnections:UseConnection: on the source action roles and on the tiers that clone (full clone).
        if (this.connectionArns.size > 0) {
            const useConnection = () =>
                new iam.PolicyStatement({
                    sid: "UseConnection",
                    actions: [
                        "codeconnections:UseConnection",
                        "codestar-connections:UseConnection",
                    ],
                    resources: Array.from(this.connectionArns),
                });
            for (const p of [release, ...(preview ? [preview] : [])]) {
                const source = p.node.tryFindChild("Source");
                for (const a of source?.node.children ?? []) {
                    const r = a.node.tryFindChild("CodePipelineActionRole");
                    if (r instanceof iam.Role) r.addToPrincipalPolicy(useConnection());
                }
            }
            for (const role of this.allRoles) role.addToPrincipalPolicy(useConnection());
        }

        // ------------------------------------------------------------------ S3 sources fed by vra
        // `vra pipeline push-source` (scheduled job, watch tier) uploads the branch zip and starts the pipeline.
        // Only the watch tier may write the created source objects (bucket policy); no tier that runs fork or
        // upstream code (build, dev, preview, env tiers) can replace the release source or start a release.
        if (this.createdSourceObjects.length > 0 && this.watchRole) {
            const writer = this.watchRole;
            writer.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "PushSourceObjects",
                    actions: ["s3:PutObject", "s3:GetObject"],
                    resources: this.createdSourceObjects.map((o) => o.bucket.arnForObjects(o.key)),
                })
            );
            this.key.grantEncryptDecrypt(writer);
            writer.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "StartPipelinesAfterPush",
                    actions: ["codepipeline:StartPipelineExecution"],
                    resources: pipelineArns,
                })
            );
            for (const bucket of new Set(this.createdSourceObjects.map((o) => o.bucket))) {
                (bucket as s3.Bucket).addToResourcePolicy(
                    new iam.PolicyStatement({
                        sid: "OnlyTheWatchTierWritesSources",
                        effect: iam.Effect.DENY,
                        principals: [new iam.AnyPrincipal()],
                        actions: ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"],
                        resources: [(bucket as s3.Bucket).arnForObjects("*")],
                        // a negated operator with several values: denied unless the caller is one of them
                        conditions: {
                            ArnNotEquals: {
                                "aws:PrincipalArn": [
                                    writer.roleArn,
                                    ...(this.cfg.sourceWriterArns ?? []),
                                ],
                            },
                        },
                    })
                );
            }
        }

        // ------------------------------------------------------------------ outputs (vra pipeline status)
        new CfnOutput(this, "TopologyDigest", {
            value: topologyDigest(this.releaseEnvs),
            description:
                "Digest of the ledger topology this pipeline was built from (vra pipeline status)",
        });
        new CfnOutput(this, "ReleasePipelineName", { value: releaseName });

        // ------------------------------------------------------------------ GitHub Actions hand-off (pipeline+actions)
        if (this.cfg.actionsHandoff) {
            const h = this.cfg.actionsHandoff;
            const issuer = h.oidcProviderArn.split(":oidc-provider/")[1];
            const handoff = new iam.Role(this, "ActionsHandoffRole", {
                roleName: `${this.prefix}-actions-handoff-${Aws.REGION}`,
                description:
                    "GitHub Actions hand-off: start the release pipeline at a checked commit (no deploy rights)",
                assumedBy: new iam.WebIdentityPrincipal(h.oidcProviderArn, {
                    StringEquals: { [`${issuer}:aud`]: h.audience ?? "sts.amazonaws.com" },
                    StringLike: { [`${issuer}:sub`]: handoffSubjects(h) },
                }),
                maxSessionDuration: Duration.hours(1),
            });
            handoff.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "StartReleasePipeline",
                    actions: [
                        "codepipeline:StartPipelineExecution",
                        "codepipeline:GetPipelineExecution",
                    ],
                    resources: [release.pipelineArn],
                })
            );
            new CfnOutput(this, "HandoffRoleArn", { value: handoff.roleArn });
        }
        if (preview) new CfnOutput(this, "PreviewPipelineName", { value: preview.pipelineName });

        // ------------------------------------------------------------------ failure notifications
        new events.Rule(this, "PipelineFailures", {
            description: "vra pipeline execution failed or was stopped",
            eventPattern: {
                source: ["aws.codepipeline"],
                detailType: ["CodePipeline Pipeline Execution State Change"],
                detail: {
                    pipeline: [releaseName, ...(preview ? [preview.pipelineName] : [])],
                    state: ["FAILED", "STOPPED"],
                },
            },
            targets: [new eventsTargets.SnsTopic(topic)],
        });

        // ------------------------------------------------------------------ scheduled watch
        if (this.cfg.watch.enabled) {
            this.watch(topic, artifactBucket);
        }

        this.addNagSuppressions([release, ...(preview ? [preview] : [])]);
    }

    // ====================================================================== helpers

    private buildspec(file: string): codebuild.BuildSpec {
        const doc = parseYaml(readFileSync(join(this.buildspecDir, file), { encoding: "utf8" }));
        return codebuild.BuildSpec.fromObjectToYaml(doc);
    }

    /** Variables every job needs: the home repository and registry mirrors. */
    private commonVariables(): Record<string, codebuild.BuildEnvironmentVariable> {
        const vars: Record<string, codebuild.BuildEnvironmentVariable> = {};
        for (const [k, v] of Object.entries(this.cfg.codebuild?.environment ?? {})) {
            vars[k] = { value: v, type: codebuild.BuildEnvironmentVariableType.PLAINTEXT };
        }
        vars.VRA_HOME_REPO = { value: this.cfg.home.repoUrl };
        vars.VRA_HOME_REF = { value: this.cfg.home.ref ?? "main" };
        if (this.homeToken) {
            vars.VRA_HOME_TOKEN = {
                value: this.homeToken.secretName,
                type: codebuild.BuildEnvironmentVariableType.SECRETS_MANAGER,
            };
        }
        return vars;
    }

    private baseEnvironment(
        privileged: boolean,
        compute: codebuild.ComputeType,
        extraEnv: Record<string, string> = {}
    ): codebuild.BuildEnvironment {
        const vars = this.commonVariables();
        for (const [k, v] of Object.entries(extraEnv)) {
            vars[k] = { value: v, type: codebuild.BuildEnvironmentVariableType.PLAINTEXT };
        }
        return {
            buildImage: codebuild.LinuxBuildImage.fromCodeBuildImageId(
                this.cfg.codebuild?.image ?? DEFAULT_IMAGE
            ),
            computeType: compute,
            privileged,
            environmentVariables: vars,
        };
    }

    private project(
        id: string,
        buildspecFile: string,
        role: iam.IRole,
        opts: {
            privileged: boolean;
            compute: codebuild.ComputeType;
            timeout: Duration;
            concurrentBuildLimit?: number;
            extraEnv?: Record<string, string>;
        }
    ): codebuild.PipelineProject {
        return new codebuild.PipelineProject(this, id, {
            projectName: `${this.prefix}-${id.toLowerCase()}`,
            description: `vra ${id.toLowerCase()} (${buildspecFile})`,
            buildSpec: this.buildspec(buildspecFile),
            environment: this.baseEnvironment(opts.privileged, opts.compute, opts.extraEnv),
            role,
            encryptionKey: this.key,
            timeout: opts.timeout,
            queuedTimeout: Duration.hours(8),
            concurrentBuildLimit: opts.concurrentBuildLimit,
            logging: { cloudWatch: { logGroup: this.logGroup, prefix: id.toLowerCase() } },
            ...this.vpcProps,
        });
    }

    private buildVpcProps(): Pick<
        codebuild.ProjectProps,
        "vpc" | "subnetSelection" | "securityGroups"
    > {
        const spec = this.cfg.codebuild?.vpc;
        if (!spec) return {};
        // fromVpcAttributes keeps synth offline (no context lookup).
        const vpc = ec2.Vpc.fromVpcAttributes(this, "Vpc", {
            vpcId: spec.vpcId,
            availabilityZones: spec.availabilityZones,
            privateSubnetIds: spec.privateSubnetIds,
        });
        const securityGroups =
            spec.securityGroupIds && spec.securityGroupIds.length > 0
                ? spec.securityGroupIds.map((sg, i) =>
                      ec2.SecurityGroup.fromSecurityGroupId(this, `Sg${i}`, sg)
                  )
                : [
                      new ec2.SecurityGroup(this, "BuildSg", {
                          vpc,
                          description:
                              "vra CodeBuild (egress only: git host, package mirrors, AWS endpoints)",
                          allowAllOutbound: true,
                      }),
                  ];
        return {
            vpc,
            subnetSelection: {
                subnets: spec.privateSubnetIds.map((s, i) =>
                    ec2.Subnet.fromSubnetId(this, `Subnet${i}`, s)
                ),
            },
            securityGroups,
        };
    }

    private tierRole(id: string, tier: string, description: string): iam.Role {
        const role = new iam.Role(this, id, {
            // the RoleNamePrefix aspect adds iamRoles.roleNamePrefix in front
            roleName: `${this.prefix}-codebuild-${tier}-${Aws.REGION}`,
            assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
            description,
            maxSessionDuration: Duration.hours(12),
        });
        this.allRoles.push(role);
        return role;
    }

    /** vra-<kind>-<env> in the env account, or the name the ledger / config gives. */
    private roleArn(
        e: EnvTarget,
        name: string | undefined,
        kind: "deploy" | "synth" | "test"
    ): string {
        return `arn:${Aws.PARTITION}:iam::${e.account}:role/${name ?? `vra-${kind}-${e.name}`}`;
    }

    private grantAssume(role: iam.Role, sid: string, roleArns: string[]): void {
        role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                sid,
                actions: ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"],
                resources: roleArns,
            })
        );
    }

    /** The configured suites plus every suite the ledger requires on the env (`vra ledger graph`). */
    static suites(configured: string | undefined, required: string[] | undefined): string {
        const list = (configured ?? "live-cli,live-web,customer").split(",").map((s) => s.trim());
        for (const r of required ?? []) if (!list.includes(r)) list.push(r);
        return list.filter((s) => s).join(",");
    }

    /**
     * Notifications, vra/* secrets, metrics; with a state store: run state, create-only history and bundles,
     * approval claims, never a delete; lock items run#* plus env#<env> for `lockEnvs` (the tier's envs).
     */
    private grantCommon(role: iam.Role, topic: sns.ITopic, lockEnvs: string[]): void {
        const p = Aws.PARTITION;
        const ss = this.cfg.stateStore;
        if (ss) {
            const obj = (rel: string) =>
                `arn:${p}:s3:::${ss.bucketName}/${ss.prefix ?? "vra"}/${rel}`;
            // a policy of its own: the tier roles' inline policies are already near the size limit
            const policy = new iam.ManagedPolicy(this, `StatePolicy-${role.node.id}`, {
                description: `vra state store for ${role.node.id}: run state, create-only history and bundles, its locks`,
                roles: [role],
            });
            this.statePolicies.push(policy);
            const add = (s: iam.PolicyStatementProps) =>
                policy.addStatements(new iam.PolicyStatement(s));
            add({
                sid: "StateRuns",
                actions: ["s3:GetObject", "s3:PutObject"],
                resources: [obj("runs/*")],
            });
            add({
                sid: "StateRead",
                actions: ["s3:GetObject"],
                resources: [
                    "history/*",
                    "artifacts/*",
                    "signatures/*",
                    "checkpoints/*",
                    "sync-state.json",
                    // IT role packages + apply records (the IAM gates), pipeline plans
                    "iam/*",
                    "pipeline/*",
                ].map(obj),
            });
            // history records, bundles, IT packages + responses and pipeline plans are create-only (If-None-Match)
            add({
                sid: "StateCreateOnly",
                actions: ["s3:PutObject"],
                resources: [obj("history/*"), obj("artifacts/*"), obj("iam/*"), obj("pipeline/*")],
                conditions: { Null: { "s3:if-none-match": "false" } },
            });
            add({
                sid: "StateApprovalClaims",
                actions: ["s3:GetObject", "s3:PutObject"],
                resources: [obj("approvals-consumed/*")],
            });
            add({
                sid: "StateRecords",
                actions: ["s3:PutObject"],
                // the IT apply record is replaced by `vra iam apply`; the create-only history record binds it
                resources: [
                    "signatures/*",
                    "checkpoints/*",
                    "sync-state.json",
                    "iam/*/applied.json",
                ].map(obj),
            });
            // not narrowed by s3:prefix: without ListBucket a read of a missing key is a 403, not a 404
            add({
                sid: "StateList",
                actions: ["s3:ListBucket"],
                resources: [`arn:${p}:s3:::${ss.bucketName}`],
            });
            add({
                sid: "HistoryIsAppendOnly",
                effect: iam.Effect.DENY,
                actions: [
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion",
                    "s3:PutObjectRetention",
                    "s3:PutObjectLegalHold",
                    "s3:PutObjectAcl",
                ],
                resources: ["history", "approvals-consumed", "artifacts", "runs"].map(
                    (d) => `arn:${p}:s3:::${ss.bucketName}/*/${d}/*`
                ),
            });
            const table = `arn:${p}:dynamodb:${Aws.REGION}:${Aws.ACCOUNT_ID}:table/${ss.lockTableName}`;
            add({
                sid: "StateLocks",
                actions: ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
                resources: [table],
                conditions: {
                    "ForAllValues:StringLike": {
                        "dynamodb:LeadingKeys": ["run#*", ...lockEnvs.map((e) => `env#${e}`)],
                    },
                },
            });
            add({ sid: "StateLockTable", actions: ["dynamodb:DescribeTable"], resources: [table] });
            kms.Key.fromKeyArn(this, `StateKey-${role.node.id}`, ss.keyArn).grantEncryptDecrypt(
                role
            );
        }
        topic.grantPublish(role);
        role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                sid: "VraSecrets",
                actions: ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                resources: [`arn:${p}:secretsmanager:${Aws.REGION}:${Aws.ACCOUNT_ID}:secret:vra/*`],
            })
        );
        role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                sid: "RunMetrics",
                actions: ["cloudwatch:PutMetricData"],
                resources: ["*"],
                conditions: { StringEquals: { "cloudwatch:namespace": "VamsReleaseAutomation" } },
            })
        );
        role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                sid: "CallerIdentity",
                actions: ["sts:GetCallerIdentity"],
                resources: ["*"],
            })
        );
    }

    /** CodeCommit repository name of a codecommit:: git URL, or undefined. */
    private static codeCommitRepo(url: string): string | undefined {
        const m = /^codecommit:(?::[a-z0-9-]+)?:\/\/(?:[^@/]+@)?([A-Za-z0-9._-]+)$/.exec(url);
        return m ? m[1] : undefined;
    }

    /**
     * CodeCommit repos: every tier clones; build pushes tags, dev (watch / sync) pushes sync/* branches. The home
     * repository is pushed by every tier when there is no state store (the jobs share state through it).
     */
    private grantCodeCommit(): void {
        const p = Aws.PARTITION;
        const arn = (n: string) => `arn:${p}:codecommit:${Aws.REGION}:${Aws.ACCOUNT_ID}:${n}`;
        const cc = Object.values(this.cfg.sources)
            .filter(
                (s): s is Extract<SourceSpec, { type: "codecommit" }> =>
                    s !== undefined && s.type === "codecommit"
            )
            .map((s) => s.repositoryName);
        const home = VraPipelineStack.codeCommitRepo(this.cfg.home.repoUrl);
        const names = Array.from(new Set([...cc, ...(home ? [home] : [])]));
        if (names.length === 0) return;
        for (const role of this.allRoles) {
            role.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "CodeCommitRepos",
                    actions: [
                        "codecommit:GitPull",
                        "codecommit:GetRepository",
                        "codecommit:GetBranch",
                    ],
                    resources: names.map(arn),
                })
            );
            if (home && !this.cfg.stateStore) {
                role.addToPrincipalPolicy(
                    new iam.PolicyStatement({
                        sid: "CodeCommitHomeState",
                        actions: ["codecommit:GitPush"],
                        resources: [arn(home)],
                    })
                );
            }
        }
        const fork = this.cfg.sources.fork;
        if (fork.type === "codecommit") {
            for (const role of [this.buildRole, ...(this.watchRole ? [this.watchRole] : [])]) {
                role.addToPrincipalPolicy(
                    new iam.PolicyStatement({
                        sid: "CodeCommitForkSync",
                        actions: ["codecommit:GitPush", "codecommit:CreatePullRequest"],
                        resources: [arn(fork.repositoryName)],
                    })
                );
            }
        }
    }

    private readonly imported = new Map<string, codecommit.IRepository | s3.IBucket>();

    private importRepo(name: string): codecommit.IRepository {
        const k = `repo:${name}`;
        let repo = this.imported.get(k) as codecommit.IRepository | undefined;
        if (!repo) {
            repo = codecommit.Repository.fromRepositoryName(this, `Repo-${name}`, name);
            this.imported.set(k, repo);
        }
        return repo;
    }

    private importBucket(name: string): s3.IBucket {
        const k = `bucket:${name}`;
        let bucket = this.imported.get(k) as s3.IBucket | undefined;
        if (!bucket) {
            bucket = s3.Bucket.fromBucketName(this, `SourceBucket-${name}`, name);
            this.imported.set(k, bucket);
        }
        return bucket;
    }

    /** Connection ARNs every source uses (created or given), for the codeconnections:UseConnection grants. */
    private readonly connectionArns = new Set<string>();
    private readonly connections = new Map<string, codeconnections.CfnConnection>();

    /**
     * The connection ARN of a source: the given one, or a connection this stack creates (one per provider and host;
     * CloudFormation creates it PENDING, a person completes the handshake once in the console).
     */
    private connectionArnOf(
        spec: Extract<SourceSpec, { type: "codeconnections" | "codestar" }>
    ): string {
        if (spec.connectionArn) {
            this.connectionArns.add(spec.connectionArn);
            return spec.connectionArn;
        }
        const s = spec as Extract<SourceSpec, { type: "codeconnections" }>;
        const key = `${s.provider}|${s.hostArn ?? ""}`;
        let conn = this.connections.get(key);
        if (!conn) {
            const n = this.connections.size;
            const short = s.provider
                .toLowerCase()
                .replace("enterpriseserver", "-es")
                .replace("selfmanaged", "-sm");
            conn = new codeconnections.CfnConnection(
                this,
                `Connection${n === 0 ? "" : n}-${s.provider}`,
                {
                    connectionName: `${this.prefix}-${short}${n === 0 ? "" : `-${n}`}`.slice(0, 32),
                    providerType: s.provider,
                    ...(s.hostArn ? { hostArn: s.hostArn } : {}),
                }
            );
            // A connection carries a person's console handshake: a source change (or stack removal) must not
            // throw it away. Retained like the buckets; an unused one is deleted by a person (B-050-4).
            conn.applyRemovalPolicy(RemovalPolicy.RETAIN);
            this.connections.set(key, conn);
            new CfnOutput(this, `ConnectionArn${n === 0 ? "" : n}`, {
                value: conn.attrConnectionArn,
                description: `CodeConnections ${s.provider} connection (PENDING until the handshake is completed in the console)`,
            });
        }
        this.connectionArns.add(conn.attrConnectionArn);
        return conn.attrConnectionArn;
    }

    /** Buckets this stack created for s3 sources without bucketName, keyed by object key. */
    private readonly createdSourceObjects: { bucket: s3.IBucket; key: string }[] = [];
    private sourceBucket?: s3.Bucket;

    private s3SourceBucket(
        spec: Extract<SourceSpec, { type: "s3" }>,
        fedByWatch: boolean
    ): s3.IBucket {
        if (spec.bucketName) return this.importBucket(spec.bucketName);
        if (!this.sourceBucket) {
            this.sourceBucket = new s3.Bucket(this, "SourceBucket", {
                encryption: s3.BucketEncryption.KMS,
                encryptionKey: this.key,
                bucketKeyEnabled: true,
                enforceSSL: true,
                minimumTLSVersion: 1.2,
                blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
                objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
                versioned: true,
                serverAccessLogsBucket: this.accessLogs,
                serverAccessLogsPrefix: "source/",
                lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(30) }],
                removalPolicy: RemovalPolicy.RETAIN,
            });
            new CfnOutput(this, "SourceBucketName", {
                value: this.sourceBucket.bucketName,
                description: "S3 source bucket fed by `vra pipeline push-source`",
            });
        }
        if (fedByWatch && !this.createdSourceObjects.some((o) => o.key === spec.objectKey))
            this.createdSourceObjects.push({ bucket: this.sourceBucket, key: spec.objectKey });
        return this.sourceBucket;
    }

    private sourceAction(
        actionName: string,
        spec: SourceSpec,
        output: codepipeline.Artifact,
        trigger: boolean,
        clone: boolean
    ): codepipeline.IAction {
        switch (spec.type) {
            case "codeconnections":
            case "codestar": {
                // owner = everything before the last "/": GitLab group/subgroup/project keeps its full id
                const cut = spec.repo.lastIndexOf("/");
                return new cpa.CodeStarConnectionsSourceAction({
                    actionName,
                    connectionArn: this.connectionArnOf(spec),
                    owner: spec.repo.slice(0, cut),
                    repo: spec.repo.slice(cut + 1),
                    branch: spec.branch,
                    output,
                    triggerOnPush: trigger,
                    codeBuildCloneOutput: clone,
                });
            }
            case "codecommit":
                return new cpa.CodeCommitSourceAction({
                    actionName,
                    repository: this.importRepo(spec.repositoryName),
                    branch: spec.branch,
                    output,
                    trigger: trigger ? cpa.CodeCommitTrigger.EVENTS : cpa.CodeCommitTrigger.NONE,
                    codeBuildCloneOutput: clone,
                });
            case "s3":
                return new cpa.S3SourceAction({
                    actionName,
                    bucket: this.s3SourceBucket(spec, actionName === "Fork"),
                    bucketKey: spec.objectKey,
                    output,
                    // a stack-created bucket is fed by `vra pipeline push-source`, which starts the pipeline itself
                    trigger: trigger && spec.bucketName ? cpa.S3Trigger.POLL : cpa.S3Trigger.NONE,
                });
        }
    }

    private releasePipeline(
        name: string,
        artifactBucket: s3.IBucket,
        topic: sns.ITopic,
        projects: Record<"validate" | "build", codebuild.IProject>,
        envProjects: Map<string, EnvProjects>,
        preflightEnvs: EnvTarget[]
    ): codepipeline.Pipeline {
        const fork = new codepipeline.Artifact("Fork");
        const vra = new codepipeline.Artifact("Vra");
        const buildOutput = new codepipeline.Artifact("BuildOutput");

        const pipeline = new codepipeline.Pipeline(this, "ReleasePipeline", {
            pipelineName: name,
            pipelineType: codepipeline.PipelineType.V2,
            // QUEUED: a newer commit never cancels a build that is waiting for an approval.
            executionMode: codepipeline.ExecutionMode.QUEUED,
            artifactBucket,
            restartExecutionOnUpdate: false,
        });

        pipeline.addStage({
            stageName: "Source",
            actions: [
                // pipeline+actions: the hand-off workflow starts executions at a checked commit instead
                this.sourceAction(
                    "Fork",
                    this.cfg.sources.fork,
                    fork,
                    sourceTrigger(this.cfg.sources.fork),
                    true
                ),
                this.sourceAction("Vra", this.cfg.sources.vra, vra, false, false),
            ],
        });

        pipeline.addStage({
            stageName: "Validate",
            actions: [
                new cpa.CodeBuildAction({
                    actionName: "LedgerAndPreflight",
                    project: projects.validate,
                    input: vra,
                    environmentVariables: {
                        VRA_LANE: { value: "release" },
                        // every other env is preflighted inside its own deploy (its role is not available here)
                        VRA_ENVS: { value: preflightEnvs.map((e) => e.name).join(" ") },
                        ...this.runVars(name),
                    },
                }),
            ],
        });

        const buildAction = new cpa.CodeBuildAction({
            actionName: "Build",
            project: projects.build,
            input: fork,
            extraInputs: [vra],
            outputs: [buildOutput],
            variablesNamespace: "Build",
            environmentVariables: { VRA_LANE: { value: "release" }, ...this.runVars(name) },
        });
        pipeline.addStage({ stageName: "Build", actions: [buildAction] });
        const buildId = buildAction.variable("VRA_BUILD_ID");

        const stages = planRelease(this.releaseEnvs);
        // env -> its approval (stage, action), for the deploy action's approval binding
        const approvals = new Map<string, { stage: string; action: string }>();
        for (const stage of stages) {
            for (const a of stage.actions.filter((x) => x.kind === "approval")) {
                approvals.set(a.env, { stage: stage.name, action: a.name });
            }
        }
        for (const stage of stages) {
            pipeline.addStage({
                stageName: stage.name,
                actions: stage.actions.map((a) =>
                    a.kind === "approval"
                        ? this.approvalAction(a, buildId, topic)
                        : this.envAction(a, {
                              projects: this.projectsOf(envProjects, a.env),
                              buildId,
                              buildOutput,
                              fork,
                              vra,
                              pipelineName: name,
                              approval: approvals.get(a.env) ?? null,
                              testSuites: this.cfg.release.testSuites,
                          })
                ),
            });
        }
        return pipeline;
    }

    private projectsOf(envProjects: Map<string, EnvProjects>, env: string): EnvProjects {
        const p = envProjects.get(env);
        if (!p) throw new Error(`no CodeBuild projects for env ${env}`);
        return p;
    }

    private approvalAction(
        a: PlannedAction,
        buildId: string,
        topic: sns.ITopic
    ): cpa.ManualApprovalAction {
        const what = a.promoteFrom
            ? `promote vra build ${buildId} from ${a.promoteFrom} to ${a.env}`
            : `deploy vra build ${buildId} to ${a.env}`;
        return new cpa.ManualApprovalAction({
            actionName: a.name,
            notificationTopic: topic,
            runOrder: a.runOrder,
            additionalInformation:
                `${a.name.startsWith("Final") ? "FINAL approval" : "Approval"}: ${what}. ` +
                `Comment format: "ticket: <id>; notes: <text>". vra rejects approvers not listed in ` +
                `envs/${a.env}/env.yaml and the pipeline's own identity.`,
        });
    }

    private envAction(
        a: PlannedAction,
        ctx: {
            projects: EnvProjects;
            buildId: string;
            buildOutput: codepipeline.Artifact;
            fork: codepipeline.Artifact;
            vra: codepipeline.Artifact;
            pipelineName: string;
            approval: { stage: string; action: string } | null;
            testSuites: string | undefined;
        }
    ): codepipeline.IAction {
        if (a.kind === "test") {
            return new cpa.CodeBuildAction({
                actionName: a.name,
                project: ctx.projects.test,
                input: ctx.fork,
                extraInputs: [ctx.vra, ctx.buildOutput],
                environmentVariables: {
                    VRA_ENV: { value: a.env },
                    VRA_TEST_SUITES: {
                        value: VraPipelineStack.suites(
                            ctx.testSuites,
                            this.releaseEnvs.find((e) => e.name === a.env)?.requiredSuites
                        ),
                    },
                    ...this.runVars(ctx.pipelineName),
                },
                runOrder: a.runOrder,
            });
        }
        const envVars: Record<string, codebuild.BuildEnvironmentVariable> = {
            VRA_ENV: { value: a.env },
            VRA_BUILD_ID: { value: ctx.buildId },
            VRA_PROMOTE_FROM: { value: a.promoteFrom ?? "" },
            ...this.runVars(ctx.pipelineName),
        };
        if (ctx.approval) {
            envVars.VRA_APPROVAL_STAGE = { value: ctx.approval.stage };
            envVars.VRA_APPROVAL_ACTION = { value: ctx.approval.action };
        }
        return new cpa.CodeBuildAction({
            actionName: a.name,
            project: ctx.projects.deploy,
            input: ctx.buildOutput,
            extraInputs: [ctx.vra],
            environmentVariables: envVars,
            runOrder: a.runOrder,
        });
    }

    private previewPipeline(
        name: string,
        artifactBucket: s3.IBucket,
        projects: Record<"build", codebuild.IProject>,
        envProjects: Map<string, EnvProjects>
    ): codepipeline.Pipeline {
        const fork = new codepipeline.Artifact("Fork");
        const vra = new codepipeline.Artifact("Vra");
        const buildOutput = new codepipeline.Artifact("BuildOutput");
        const pv = this.cfg.preview;
        const forkPreview = this.cfg.sources.forkPreview;
        if (!pv || !forkPreview)
            throw new Error("preview needs preview.env and sources.forkPreview");

        const pipeline = new codepipeline.Pipeline(this, "PreviewPipeline", {
            pipelineName: name,
            pipelineType: codepipeline.PipelineType.V2,
            // SUPERSEDED: only the newest development merge matters for dev-preview.
            executionMode: codepipeline.ExecutionMode.SUPERSEDED,
            artifactBucket,
            restartExecutionOnUpdate: false,
        });
        pipeline.addStage({
            stageName: "Source",
            actions: [
                this.sourceAction("Fork", forkPreview, fork, sourceTrigger(forkPreview), true),
                this.sourceAction("Vra", this.cfg.sources.vra, vra, false, false),
            ],
        });
        const buildAction = new cpa.CodeBuildAction({
            actionName: "Build",
            project: projects.build,
            input: fork,
            extraInputs: [vra],
            outputs: [buildOutput],
            variablesNamespace: "Build",
            environmentVariables: { VRA_LANE: { value: "preview" }, ...this.runVars(name) },
        });
        pipeline.addStage({ stageName: "Build", actions: [buildAction] });
        const ctx = {
            projects: this.projectsOf(envProjects, pv.env.name),
            buildId: buildAction.variable("VRA_BUILD_ID"),
            buildOutput,
            fork,
            vra,
            pipelineName: name,
            approval: null,
            testSuites: pv.testSuites,
        };
        pipeline.addStage({
            stageName: `Deploy-${pv.env.name}`,
            actions: [
                this.envAction(
                    {
                        kind: "deploy",
                        env: pv.env.name,
                        promoteFrom: null,
                        name: `Deploy-${pv.env.name}`,
                        runOrder: 1,
                    },
                    ctx
                ),
                this.envAction(
                    {
                        kind: "test",
                        env: pv.env.name,
                        promoteFrom: null,
                        name: `Test-${pv.env.name}`,
                        runOrder: 2,
                    },
                    ctx
                ),
            ],
        });
        return pipeline;
    }

    /** Pipeline name + execution id: vra binds approvals to the execution and reads its trigger. */
    private runVars(pipelineName: string): Record<string, codebuild.BuildEnvironmentVariable> {
        return {
            VRA_PIPELINE_NAME: { value: pipelineName },
            VRA_PIPELINE_EXECUTION_ID: { value: codepipeline.GlobalVariables.executionId },
        };
    }

    private get watchTier(): iam.Role {
        if (!this.watchRole) throw new Error("watch.enabled is false: no watch tier");
        return this.watchRole;
    }

    private watch(topic: sns.ITopic, artifactBucket: s3.IBucket): void {
        const w = this.cfg.watch;
        const env = this.baseEnvironment(true, codebuild.ComputeType.LARGE);
        const vars: Record<string, codebuild.BuildEnvironmentVariable> = {
            ...(env.environmentVariables ?? {}),
            ...(w.packageRepoUrl ? { VRA_PACKAGE_REPO: { value: w.packageRepoUrl } } : {}),
            ...(w.packageRef ? { VRA_PACKAGE_REF: { value: w.packageRef } } : {}),
        };
        const pkg = this.cfg.sources.vra;
        if (pkg.type === "s3" && !w.packageRepoUrl) {
            // the package zip the pipeline's "Vra" source reads (`vra pipeline push-source --package`)
            const bucket = this.s3SourceBucket(pkg, false);
            vars.VRA_PACKAGE_S3_URI = { value: `s3://${bucket.bucketName}/${pkg.objectKey}` };
            this.watchTier.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    sid: "ReadPackageZip",
                    actions: ["s3:GetObject"],
                    resources: [bucket.arnForObjects(pkg.objectKey)],
                })
            );
            if (!pkg.bucketName) this.key.grantDecrypt(this.watchTier);
        }
        if (w.gitTokenSecretName) {
            const secret = secretsmanager.Secret.fromSecretNameV2(
                this,
                "GitToken",
                w.gitTokenSecretName
            );
            vars.VRA_GIT_TOKEN = {
                value: secret.secretName,
                type: codebuild.BuildEnvironmentVariableType.SECRETS_MANAGER,
            };
        }
        // S3 sources this stack created: the job uploads the branch and starts the pipeline after a change
        if (this.sourceBucket) {
            vars.VRA_SOURCE_BUCKET = { value: this.sourceBucket.bucketName };
            const fork = this.cfg.sources.fork;
            if (fork.type === "s3" && !fork.bucketName) {
                vars.VRA_SOURCE_RELEASE_KEY = { value: fork.objectKey };
                vars.VRA_RELEASE_PIPELINE = { value: `${this.prefix}-release` };
            }
            const fp = this.cfg.sources.forkPreview;
            if (this.cfg.preview && fp?.type === "s3" && !fp.bucketName) {
                vars.VRA_SOURCE_PREVIEW_KEY = { value: fp.objectKey };
                vars.VRA_PREVIEW_PIPELINE = { value: `${this.prefix}-preview` };
            }
        }
        const spec = parseYaml(
            readFileSync(join(this.buildspecDir, "buildspec-watch.yml"), { encoding: "utf8" })
        );
        const command = watchCommand(this.cfg);
        if (command === "sync-merge") {
            // sync, merge each lane with something new (release: sync PR; preview: merge), feed S3 sources
            spec.phases.build.commands = [
                `bash "\${VRA_PKG_DIR}/cicd/codebuild/scripts/pipeline-watch.sh"`,
            ];
        } else if (command !== "watch") {
            // Replace the `vra watch` invocation with the configured command ("sync").
            spec.phases.build.commands = [
                `bash "\${VRA_PKG_DIR}/cicd/codebuild/scripts/run-vra.sh" watch ${command}`,
            ];
        }
        const project = new codebuild.Project(this, "Watch", {
            projectName: `${this.prefix}-watch`,
            description: `vra scheduled ${command} (buildspec-watch.yml)`,
            buildSpec: codebuild.BuildSpec.fromObjectToYaml(spec),
            environment: { ...env, environmentVariables: vars },
            // watch tier: sync / merge and the S3 sources; it assumes no env role (never deploys, R50-12)
            role: this.watchTier,
            encryptionKey: this.key,
            timeout: Duration.hours(8),
            queuedTimeout: Duration.hours(2),
            concurrentBuildLimit: 1,
            // the pipeline's own artifact bucket: the state store stays create-only for every tier
            artifacts: codebuild.Artifacts.s3({
                bucket: artifactBucket,
                path: "watch-artifacts",
                includeBuildId: true,
                packageZip: true,
                encryption: true,
            }),
            logging: { cloudWatch: { logGroup: this.logGroup, prefix: "watch" } },
            ...this.vpcProps,
        });

        project.onBuildFailed("WatchFailed", {
            description: "vra scheduled watch build failed",
            target: new eventsTargets.SnsTopic(topic),
        });

        new scheduler.Schedule(this, "WatchSchedule", {
            scheduleName: `${this.prefix}-watch-daily`,
            description: "Start the vra watch CodeBuild project",
            schedule: scheduler.ScheduleExpression.expression(
                w.schedule,
                TimeZone.of(w.timeZone ?? "UTC")
            ),
            target: new schedulerTargets.CodeBuildStartBuild(project, { retryAttempts: 0 }),
        });
    }

    private addNagSuppressions(pipelines: codepipeline.Pipeline[]): void {
        const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
        // Action wildcards generated by CDK grant helpers (bucket.grantReadWrite, key grants).
        const cdkGrantActions = [
            "Action::kms:GenerateDataKey*",
            "Action::kms:ReEncrypt*",
            "Action::s3:Abort*",
            "Action::s3:DeleteObject*",
            "Action::s3:GetBucket*",
            "Action::s3:GetObject*",
            "Action::s3:List*",
        ];
        const artifactObjects = { regex: "/^Resource::<Artifacts[A-Za-z0-9]+\\.Arn>\\/\\*$/g" };

        NagSuppressions.addResourceSuppressions(this.node.findChild("Build"), [
            {
                id: "AwsSolutions-CB3",
                reason: "CDK synth bundles VAMS Lambda code in docker containers.",
            },
        ]);
        for (const child of this.node.children) {
            if (child.node.id.startsWith("Deploy-")) {
                NagSuppressions.addResourceSuppressions(child, [
                    {
                        id: "AwsSolutions-CB3",
                        reason: "cdk deploy publishes VAMS docker image assets from cdk.out.",
                    },
                ]);
            }
            if (
                child.node.id.startsWith("Test-") &&
                this.rollbackEnvs.has(child.node.id.slice(5))
            ) {
                NagSuppressions.addResourceSuppressions(child, [
                    {
                        id: "AwsSolutions-CB3",
                        reason: "A failed test rolls the env back (vra rollback redeploys the previous build's CDK docker assets).",
                    },
                ]);
            }
        }
        const watch = this.node.tryFindChild("Watch");
        if (watch) {
            NagSuppressions.addResourceSuppressions(watch, [
                {
                    id: "AwsSolutions-CB3",
                    reason: "vra watch builds and deploys dev, which needs docker for CDK assets.",
                },
            ]);
        }

        const stateObjects = this.cfg.stateStore
            ? [
                  {
                      regex: `/^Resource::arn:<AWS::Partition>:s3:::${esc(
                          this.cfg.stateStore.bucketName
                      )}\\/[A-Za-z0-9._/*-]+$/g`,
                  },
              ]
            : [];
        for (const policy of this.statePolicies)
            NagSuppressions.addResourceSuppressions(policy, [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "State store objects below the configured prefix (runs/*, history/*, artifacts/*, approvals-consumed/*, " +
                        "signatures/*, checkpoints/*): keys carry run, env and build ids that are not known in advance. " +
                        "History and bundles are create-only (s3:if-none-match); deletes are denied.",
                    appliesTo: stateObjects,
                },
            ]);
        for (const role of this.allRoles)
            NagSuppressions.addResourceSuppressions(
                role,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason:
                            "Scoped wildcards only: CDK grant action families on the pipeline artifact bucket, the vra state bucket and their KMS keys; " +
                            "objects in those two buckets (run state, bundles, reports); vra/* secrets; report groups and log streams of these CodeBuild projects; " +
                            "VPC network interfaces when the projects run in a VPC. Resource '*' is limited to sts:GetCallerIdentity and " +
                            "cloudwatch:PutMetricData (namespace condition), which have no resource ARNs.",
                        appliesTo: [
                            ...cdkGrantActions,
                            artifactObjects,
                            "Resource::*",
                            ...stateObjects,
                            {
                                regex: "/^Resource::arn:<AWS::Partition>:codebuild:[a-z0-9-]+:[0-9]{12}:report-group\\/<[A-Za-z0-9]+>-\\*$/g",
                            },
                            {
                                regex: "/^Resource::arn:<AWS::Partition>:logs:[a-z0-9-]+:[0-9]{12}:log-group:\\/aws\\/codebuild\\/<[A-Za-z0-9]+>:\\*$/g",
                            },
                            "Resource::arn:<AWS::Partition>:secretsmanager:<AWS::Region>:<AWS::AccountId>:secret:vra/*",
                            "Resource::arn:<AWS::Partition>:ec2:<AWS::Region>:<AWS::AccountId>:network-interface/*",
                        ],
                    },
                ],
                true
            );

        // CodeBuild attaches its VPC policy (ec2:Describe*/CreateNetworkInterface, no resource ARN) once
        // per role, under the first project that uses it.
        for (const child of this.node.children) {
            const vpcPolicy = child.node.tryFindChild("PolicyDocument");
            if (vpcPolicy) {
                NagSuppressions.addResourceSuppressions(vpcPolicy, [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "CodeBuild VPC networking actions (Describe*, Create/DeleteNetworkInterface) do not support resource-level permissions.",
                        appliesTo: ["Resource::*"],
                    },
                ]);
            }
        }

        for (const pipeline of pipelines) {
            NagSuppressions.addResourceSuppressions(
                pipeline,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "CDK-generated pipeline and source-action roles: grant action families and object wildcards on this stack's own KMS-encrypted artifact bucket only.",
                        appliesTo: [...cdkGrantActions, artifactObjects],
                    },
                ],
                true
            );
        }
    }
}
