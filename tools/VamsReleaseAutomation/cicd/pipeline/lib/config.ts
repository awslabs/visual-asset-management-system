/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from "crypto";
import { existsSync, readdirSync, readFileSync } from "fs";
import { dirname, isAbsolute, join, resolve } from "path";
import { parse as parseYaml } from "yaml";

/** AWS CodeConnections provider types (AWS::CodeConnections::Connection ProviderType). */
export const CONNECTION_PROVIDERS = [
    "GitHub",
    "GitHubEnterpriseServer",
    "GitLab",
    "GitLabSelfManaged",
    "Bitbucket",
] as const;
export type ConnectionProvider = (typeof CONNECTION_PROVIDERS)[number];
/** Providers that reach a self-managed server through a CodeConnections host (hostArn). */
export const HOST_PROVIDERS: readonly ConnectionProvider[] = [
    "GitHubEnterpriseServer",
    "GitLabSelfManaged",
];

/**
 * Where a repository comes from.
 * - codeconnections: AWS CodeConnections to GitHub, GitHub Enterprise Server, GitLab (gitlab.com or
 *   self-managed) or Bitbucket. Without connectionArn the stack creates the connection (status PENDING: a person
 *   completes the handshake once in the console); GitHub Enterprise Server and GitLab self-managed then need the
 *   hostArn of a CodeConnections host. Availability differs by partition and region.
 * - codestar: the 0.4 spelling of codeconnections with an existing connectionArn (kept for old configs).
 * - codecommit: a CodeCommit repository in the tools account (e.g. a mirror of the fork).
 * - s3: a zip object in a versioned S3 bucket (every partition). Without bucketName the stack creates the bucket
 *   and `vra pipeline push-source` (scheduled watch job) uploads the branch and starts the pipeline; with an
 *   existing bucketName, whatever uploads the zip triggers the pipeline by polling.
 */
export type SourceSpec =
    | {
          type: "codeconnections";
          provider: ConnectionProvider;
          /** Existing connection (arn:<p>:codeconnections:... or arn:<p>:codestar-connections:...). */
          connectionArn?: string;
          /** CodeConnections host for GitHubEnterpriseServer / GitLabSelfManaged when the stack creates the connection. */
          hostArn?: string;
          /** owner/repo; GitLab groups may nest (group/subgroup/project). */
          repo: string;
          branch: string;
          /** Start the pipeline on a push (default true; automation.mode pipeline+actions renders false). */
          triggerOnPush?: boolean;
      }
    | {
          type: "codestar";
          connectionArn: string;
          /** owner/repo */
          repo: string;
          branch: string;
          /** Start the pipeline on a push (default true; automation.mode pipeline+actions renders false). */
          triggerOnPush?: boolean;
      }
    | {
          type: "codecommit";
          repositoryName: string;
          branch: string;
          /** Start the pipeline on a push (default true; automation.mode pipeline+actions renders false). */
          triggerOnPush?: boolean;
      }
    | {
          type: "s3";
          /** Omit to let the stack create the bucket (fed by `vra pipeline push-source`). */
          bucketName?: string;
          objectKey: string;
      };

/** `automation.mode` of the ledger (vra.yaml) the pipeline was rendered for. */
export type AutomationMode = "hybrid" | "pipeline" | "pipeline+actions";

/**
 * How the stack treats its own IAM roles (strict-IAM customers):
 * - create (default): CloudFormation creates every role.
 * - report: set only by `vra pipeline plan` (-c vraIamReport=true): writes cdk.out/iam-policy-report.json, which
 *   plan turns into the IT package. That assembly is not deployable (CDK renders the role ARNs as placeholders),
 *   so a config file cannot ask for it.
 * - precreated: no role is created; every role must be listed in precreatedRoles (construct path -> role name)
 *   or synth fails, listing the missing ones in the report.
 */
export interface IamRolesSpec {
    mode?: "create" | "report" | "precreated";
    precreatedRoles?: Record<string, string>;
    /** Prefix for every role name this stack creates (IT naming rule), e.g. "app-". */
    roleNamePrefix?: string;
}

export interface EnvTarget {
    /** Ledger env name (envs/<name>/env.yaml). */
    name: string;
    /** Env account id; the env's CodeBuild tier may assume vra-deploy-<name> there. */
    account: string;
    /** Deploy role name, default vra-deploy-<name>. Only this env's own CodeBuild tier may assume it. */
    deployRoleName?: string;
    /** Build-only synth role name (vra-deploy-role.yaml CreateSynthRole), default vra-synth-<name>. */
    synthRoleName?: string;
    /** Live-test role name (vra-deploy-role.yaml CreateTestRole), default vra-test-<name>; used with isolateTests. */
    testRoleName?: string;
    /**
     * env.yaml approvalSigningKeyArn: the KMS key whose signature every approval record of this env must carry.
     * The env's tier role gets kms:Verify on it (never kms:Sign).
     */
    approvalSigningKeyArn?: string;
}

/** One env of the release lane, as the ledger defines it (envs/<name>/env.yaml). */
export interface ReleaseEnv extends EnvTarget {
    /** Env whose tested build this one receives (ledger promotesFrom). Omitted or null = a root, deployed from the build. */
    promotesFrom?: string | null;
    /** A manual approval (and the approval record vra checks) before this env (ledger requiresApproval). */
    requiresApproval?: boolean;
    /** Run the live suites in a separate Test action after the deploy. Default: true when another env promotes from this one. */
    test?: boolean;
    /** Ledger tier (prod | nonprod | preview), from `vra ledger graph`. */
    tier?: string;
    /** Suites the engine requires on this env before an env promotes from it (`vra ledger graph` requiredSuites). */
    requiredSuites?: string[];
}

/** The customer's home repository: vra-home.yaml, ledger/, shared state. Every CodeBuild job clones it. */
export interface HomeSpec {
    /** git URL: codecommit::<region>://<repo> (IAM auth, every partition) or https://... */
    repoUrl: string;
    /** Branch, default main. */
    ref?: string;
    /**
     * Secrets Manager secret NAME holding an HTTPS token for repoUrl (not needed for CodeCommit). With
     * stateStore set the jobs only read the home repository; without it they push the state they write.
     */
    tokenSecretName?: string;
}

export interface VpcSpec {
    vpcId: string;
    availabilityZones: string[];
    privateSubnetIds: string[];
    /** Optional existing security groups; default = one new egress-only group. */
    securityGroupIds?: string[];
}

export interface PipelineConfig {
    /** Prefix for every resource name, default "vra". */
    prefix?: string;
    /** Tools account and region the pipelines run in. */
    toolsAccount: string;
    region: string;

    sources: {
        /** Customer VAMS fork, trunk branch (release lane). */
        fork: SourceSpec;
        /** Customer VAMS fork, preview branch (preview lane). Omit to skip the preview pipeline. */
        forkPreview?: SourceSpec;
        /** vams-release-automation package, pinned (tag or commit branch). */
        vra: SourceSpec;
    };

    home: HomeSpec;

    /**
     * Path (relative to this config file) of the JSON printed by `vra --json ledger graph` for the home's ledger.
     * When set, the release lane's envs, accounts, promotesFrom, requiresApproval, tier and requiredSuites come
     * from the engine's own view of the ledger, so the pipeline never re-derives a gate; release.envs entries then
     * only override role names, approval keys and test. Regenerate it (and redeploy) after a topology change: the
     * Validate stage fails while the ledger differs.
     */
    ledgerGraph?: string;

    /**
     * Path of a checkout of the home repository's ledger/ (relative to this config file). Read only for what the
     * graph does not carry: role names (deployRoleArn, synthRoleArn, testRoleArn) and approvalSigningKeyArn of
     * envs/<name>/env.yaml. Requires ledgerGraph.
     */
    ledgerDir?: string;

    release: {
        /**
         * Release-lane envs. Any topology: one or more roots, fan-out (siblings with the same promotesFrom run in
         * parallel), waves (a chain of gated envs), ungated intermediate envs (no approval). Required unless
         * ledgerDir is set.
         */
        envs?: ReleaseEnv[];
        testSuites?: string;
        /**
         * Run each env's Test action under a test-only tier role that may assume only vra-test-<env>
         * (vra-deploy-role.yaml CreateTestRole, env.yaml testRoleArn), instead of the env's deploy tier.
         * Unset (default): every approval-gated env isolates its tests, ungated envs do not (they keep
         * autoRollback). true: every env. false: none, refused when a prod-class env runs tests.
         */
        isolateTests?: boolean;
        /**
         * Roll an env back (`vra rollback --env <env>`) when its Test action fails, for envs without approval
         * whose rollbackPolicy is auto. Default true; not available with isolateTests (the test-only tier cannot
         * deploy): there the deploy's own smoke rolls back.
         */
        autoRollback?: boolean;
    };

    /**
     * Which release envs get stages: "all" (default) or "dev" (hybrid tier: only envs reachable from a root
     * without any approval; people or agents run the gated envs with `vra promote`).
     */
    scope?: "all" | "dev";

    /** The vra.yaml automation.mode this config was rendered for (informational; recorded as a stack tag). */
    automationMode?: AutomationMode;

    /** The pipeline's own IAM roles (strict-IAM customers). */
    iamRoles?: IamRolesSpec;

    /**
     * Exact IAM role ARNs (no wildcards) that may write the S3 sources the stack created, besides the watch tier:
     * the operator role that runs the first `vra pipeline push-source --package` (the watch job needs that zip
     * before it can run at all) and manual `push-source` runs. Never list a role that runs fork or upstream code.
     * Without it only the watch tier writes the sources (B-050-5).
     */
    sourceWriterArns?: string[];

    /**
     * pipeline+actions: an OIDC role the GitHub Actions hand-off workflow (cicd/github-actions/hand-off-to-codepipeline.yml)
     * assumes to start the release pipeline at a checked commit. It may only call codepipeline:StartPipelineExecution
     * and GetPipelineExecution on the release pipeline. Output HandoffRoleArn.
     */
    actionsHandoff?: {
        /** IAM OIDC provider of the GitHub issuer (github.com, a GHEC enterprise issuer, or a GHES host). */
        oidcProviderArn: string;
        /**
         * Allowed `sub` values, e.g. repo:example-org/vams-fork:environment:vra-handoff. Optional with
         * repositoryIds; both are allowed together.
         */
        subjects?: string[];
        /**
         * The fork repository and its immutable ids: subject repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<env>
         * (cicd/oidc/github_trust.py IMMUTABLE_SUBJECT), for repositories that send immutable subjects.
         */
        repositoryIds?: {
            repository: string;
            ownerId: string;
            repositoryId: string;
            environment?: string;
        };
        /** Default sts.amazonaws.com. */
        audience?: string;
    };

    preview?: {
        env: EnvTarget;
        testSuites?: string;
    };

    watch: {
        enabled: boolean;
        /** EventBridge Scheduler expression, e.g. "cron(0 6 * * ? *)". */
        schedule: string;
        timeZone?: string;
        /**
         * What the scheduled build runs:
         * - "watch" (default without a release pipeline owner): the whole cron workflow (deploys dev itself).
         * - "sync": mirror upstream and detect new release tags and development commits only.
         * - "sync-merge" (default when this pipeline owns dev): sync, then `vra merge` for each lane with something
         *   new (the release lane opens a sync PR into the trunk; the preview lane merges into its branch, which
         *   starts the preview pipeline), then `vra pipeline push-source` for S3 sources the stack created.
         */
        command?: "watch" | "sync" | "sync-merge";
        /**
         * git URL of the package, cloned by buildspec-watch.yml. Optional when sources.vra is an s3 source: the job
         * then downloads the same zip the pipeline reads.
         */
        packageRepoUrl?: string;
        packageRef?: string;
        /** Secrets Manager secret NAME holding the HTTPS git token for the fork and package hosts. */
        gitTokenSecretName?: string;
    };

    /**
     * Optional strong state backend (iam/cloudformation/vra-state-store.yaml) for vra.yaml stateStore.type s3.
     * Without it the jobs share state through the home repository (git sync) and push what they write.
     */
    stateStore?: {
        bucketName: string;
        /** vra.yaml stateStore.prefix, default "vra": the tiers reach only objects below it. */
        prefix?: string;
        lockTableName: string;
        /** KMS key ARN (StateKeyArn output). */
        keyArn: string;
    };

    notifications: {
        /** SNS topic ARN. Approval requests and failures go here. */
        topicArn: string;
    };

    codebuild?: {
        /** Build image, default aws/codebuild/amazonlinux-x86_64-standard:5.0 (Node 22, Python 3.12). */
        image?: string;
        vpc?: VpcSpec;
        /** Extra plaintext env vars (registry mirrors etc.). Never put secrets here. */
        environment?: Record<string, string>;
    };

    /** Optional permissions boundary ARN applied to every IAM role this app creates. */
    permissionsBoundaryArn?: string;

    /**
     * Optional asymmetric KMS key (ECC_NIST_P256, SIGN_VERIFY) of the ledger's build.signing.kmsKeyArn. The build
     * tier may sign with it (manifests, release tags); the deploying tiers may only verify.
     */
    signingKeyArn?: string;
}

const ACCOUNT_RE = /^[0-9]{12}$/;

/**
 * VAMS config variables (engine ledger.VAMS_CONFIG_ENV_VARS). A job that exports one stops the build unless the
 * env records it in env.yaml synthEnv; a CI lane never sets them.
 */
export const VAMS_CONFIG_ENV_VARS = [
    "STACK_NAME",
    "REGION",
    "ADMIN_EMAIL_ADDRESS",
    "ADMIN_USER_ID",
    "CRED_TOKEN_TIMEOUT_SECONDS",
    "PRESIGNED_URL_TIMEOUT_SECONDS",
    "AWS_USE_FIPS_ENDPOINT",
    "AWS_USE_WAF",
    "DEFAULT_USER_ROLE_NAME",
];

/** One env of `vra --json ledger graph` (result.envs.<name>). */
export interface LedgerGraphEnv {
    lane: string;
    promotesFrom: string | null;
    requiresApproval: boolean;
    autoDeploy?: boolean;
    tier?: string;
    isProdClass?: boolean;
    requiredSuites?: string[];
    account: string;
    region?: string;
    children?: string[];
}

export interface LedgerGraph {
    envs: Record<string, LedgerGraphEnv>;
    roots?: string[];
}

/** The graph from a file holding `vra --json ledger graph` output (the envelope, or its result). */
export function readLedgerGraph(file: string): LedgerGraph {
    const doc = JSON.parse(readFileSync(file, { encoding: "utf8" })) as Record<string, unknown>;
    const graph = (doc.result ?? doc) as LedgerGraph;
    if (!graph || typeof graph.envs !== "object")
        throw new Error(`${file} is not \`vra --json ledger graph\` output (no envs)`);
    return graph;
}

/** Release-lane envs of a ledger graph, sorted by name. */
export function releaseEnvsFromGraph(graph: LedgerGraph): ReleaseEnv[] {
    return Object.entries(graph.envs)
        .filter(([, e]) => (e.lane ?? "release") === "release")
        .map(([name, e]) => ({
            name,
            account: String(e.account ?? ""),
            promotesFrom: e.promotesFrom ?? null,
            requiresApproval: Boolean(e.requiresApproval),
            tier: e.tier,
            requiredSuites: e.requiredSuites ?? [],
        }))
        .sort((a, b) => a.name.localeCompare(b.name));
}
const NAME_RE = /^[a-z0-9][a-z0-9-]{0,40}$/;
const ROLE_NAME_RE = /^[A-Za-z0-9+=,.@_-]{1,64}$/;
const CONNECTION_ARN_RE =
    /^arn:aws[a-z-]*:(codeconnections|codestar-connections):[a-z0-9-]+:[0-9]{12}:connection\/[A-Za-z0-9-]+$/;
const HOST_ARN_RE =
    /^arn:aws[a-z-]*:(codeconnections|codestar-connections):[a-z0-9-]+:[0-9]{12}:host\/[A-Za-z0-9-]+\/?[A-Za-z0-9-]*$/;

/** Problems of one source spec (empty when valid). */
export function sourceErrors(where: string, s: SourceSpec): string[] {
    const out: string[] = [];
    switch (s.type) {
        case "codeconnections":
            if (!CONNECTION_PROVIDERS.includes(s.provider))
                out.push(`${where}.provider must be one of ${CONNECTION_PROVIDERS.join(", ")}`);
            if (s.connectionArn !== undefined && !CONNECTION_ARN_RE.test(s.connectionArn))
                out.push(`${where}.connectionArn is not a CodeConnections connection ARN`);
            if (s.hostArn !== undefined && !HOST_ARN_RE.test(s.hostArn))
                out.push(`${where}.hostArn is not a CodeConnections host ARN`);
            if (!s.connectionArn && HOST_PROVIDERS.includes(s.provider) && !s.hostArn)
                out.push(
                    `${where}: provider ${s.provider} needs hostArn (a CodeConnections host for the server) or an ` +
                        `existing connectionArn`
                );
            if (s.hostArn && !HOST_PROVIDERS.includes(s.provider))
                out.push(`${where}.hostArn is only for ${HOST_PROVIDERS.join(" / ")}`);
            if (!/^[^/\s]+(\/[^/\s]+)+$/.test(s.repo ?? ""))
                out.push(`${where}.repo must be owner/repo (GitLab: group[/subgroup]/project)`);
            if (!s.branch) out.push(`${where}.branch is required`);
            break;
        case "codestar":
            if (!s.connectionArn) out.push(`${where}.connectionArn is required for type codestar`);
            if (!/^[^/\s]+\/[^/\s]+$/.test(s.repo ?? ""))
                out.push(`${where}.repo must be owner/repo`);
            if (!s.branch) out.push(`${where}.branch is required`);
            break;
        case "codecommit":
            if (!s.repositoryName) out.push(`${where}.repositoryName is required`);
            if (!s.branch) out.push(`${where}.branch is required`);
            break;
        case "s3":
            if (!s.objectKey || s.objectKey.startsWith("/"))
                out.push(`${where}.objectKey is required (a key such as fork/main.zip)`);
            break;
        default:
            out.push(`${where}.type must be codeconnections, codestar, codecommit or s3`);
    }
    return out;
}

/**
 * The release envs a pipeline scope covers. "dev" keeps the envs reachable from a root without any approval
 * (an env is kept when it and every env it promotes from need no approval).
 */
export function scopeEnvs(envs: ReleaseEnv[], scope: "all" | "dev" | undefined): ReleaseEnv[] {
    if ((scope ?? "all") === "all") return envs;
    const byName = new Map(envs.map((e) => [e.name, e]));
    const ungated = (name: string, seen: Set<string>): boolean => {
        const e = byName.get(name);
        if (!e || e.requiresApproval || seen.has(name)) return false;
        if (!e.promotesFrom) return true;
        return ungated(e.promotesFrom, new Set([...seen, name]));
    };
    return envs.filter((e) => ungated(e.name, new Set()));
}

/**
 * Digest of the topology a pipeline was built from (stack output TopologyDigest). `vra pipeline status` computes
 * the same digest from `vra ledger graph` (engine/vra/pipeline.py topology_digest): envs sorted by name, fixed key
 * order, compact JSON, SHA-256.
 */
export function topologyDigest(envs: ReleaseEnv[]): string {
    const rows = [...envs]
        .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
        .map((e) => ({
            name: e.name,
            account: e.account,
            promotesFrom: e.promotesFrom ?? null,
            requiresApproval: Boolean(e.requiresApproval),
            requiredSuites: e.requiredSuites ?? [],
        }));
    return createHash("sha256")
        .update(JSON.stringify({ envs: rows }))
        .digest("hex");
}

/** The scheduled job's command: sync-merge when this pipeline deploys dev, else watch. */
export function watchCommand(cfg: PipelineConfig): "watch" | "sync" | "sync-merge" {
    return cfg.watch?.command ?? (cfg.automationMode ? "sync-merge" : "watch");
}

/** Role name from an ARN in env.yaml (arn:<p>:iam::<acct>:role/<path/>name). */
function roleName(arn: unknown): string | undefined {
    if (typeof arn !== "string" || !arn.includes(":role/")) return undefined;
    return arn.split("/").pop();
}

/** Role names and approval keys of envs/<name>/env.yaml (what `vra ledger graph` does not carry). */
export function envDetailsFromLedger(ledgerDir: string): Map<string, Partial<ReleaseEnv>> {
    const envsDir = join(ledgerDir, "envs");
    if (!existsSync(envsDir)) throw new Error(`ledgerDir ${ledgerDir} has no envs/ directory`);
    const out = new Map<string, Partial<ReleaseEnv>>();
    for (const dir of readdirSync(envsDir, { withFileTypes: true })) {
        const file = join(envsDir, dir.name, "env.yaml");
        if (!dir.isDirectory() || !existsSync(file)) continue;
        const e = (parseYaml(readFileSync(file, { encoding: "utf8" })) ?? {}) as Record<
            string,
            unknown
        >;
        out.set(String(e.name ?? dir.name), {
            deployRoleName: roleName(e.deployRoleArn),
            synthRoleName: roleName(e.synthRoleArn),
            testRoleName: roleName(e.testRoleArn),
            approvalSigningKeyArn:
                typeof e.approvalSigningKeyArn === "string" ? e.approvalSigningKeyArn : undefined,
        });
    }
    return out;
}

/**
 * The release envs the stack builds: from the ledger graph when ledgerGraph is set (release.envs entries, then
 * ledgerDir env.yaml values, fill in role names and approval keys), else release.envs.
 */
export function resolveReleaseEnvs(cfg: PipelineConfig, baseDir = "."): ReleaseEnv[] {
    const abs = (p: string) => (isAbsolute(p) ? p : resolve(baseDir, p));
    if (!cfg.ledgerGraph) {
        if (cfg.ledgerDir)
            throw new Error(
                "ledgerDir needs ledgerGraph: write `vra --json ledger graph > ledger-graph.json` and set " +
                    "ledgerGraph (the topology comes from the engine, never from re-reading env.yaml)"
            );
        return cfg.release?.envs ?? [];
    }
    const details = cfg.ledgerDir ? envDetailsFromLedger(abs(cfg.ledgerDir)) : new Map();
    const overrides = new Map((cfg.release?.envs ?? []).map((e) => [e.name, e]));
    return releaseEnvsFromGraph(readLedgerGraph(abs(cfg.ledgerGraph))).map((e) => {
        const o = overrides.get(e.name);
        const d = details.get(e.name) ?? {};
        return {
            ...e,
            deployRoleName: o?.deployRoleName ?? d.deployRoleName,
            synthRoleName: o?.synthRoleName ?? d.synthRoleName,
            testRoleName: o?.testRoleName ?? d.testRoleName,
            approvalSigningKeyArn: o?.approvalSigningKeyArn ?? d.approvalSigningKeyArn,
            test: o?.test,
        };
    });
}

/** cicd/oidc/github_trust.py IMMUTABLE_SUBJECT and NAME_SUBJECT: the one subject format of every vra role. */
export const IMMUTABLE_SUBJECT = "repo:{owner}@{owner_id}/{name}@{repository_id}:environment:{env}";

/** The hand-off role's allowed subjects: the listed ones plus the immutable-id subject (R50-16). */
export function handoffSubjects(h: NonNullable<PipelineConfig["actionsHandoff"]>): string[] {
    const out = [...(h.subjects ?? [])];
    const ids = h.repositoryIds;
    if (ids) {
        const [owner, name] = ids.repository.split("/");
        out.push(
            IMMUTABLE_SUBJECT.replace("{owner}", owner)
                .replace("{owner_id}", ids.ownerId)
                .replace("{name}", name)
                .replace("{repository_id}", ids.repositoryId)
                .replace("{env}", ids.environment ?? "vra-handoff")
        );
    }
    return out;
}

/**
 * Whether an env's live tests run under a test-only tier: release.isolateTests when set, else every
 * approval-gated env (R50-18: the fork's web/e2e dependency tree never runs under a gated env's deploy tier).
 */
export function testsIsolated(
    cfg: PipelineConfig,
    env: EnvTarget & { requiresApproval?: boolean }
): boolean {
    return cfg.release?.isolateTests ?? Boolean(env.requiresApproval);
}

export function loadConfig(path: string): PipelineConfig {
    const abs = resolve(path);
    const cfg = JSON.parse(readFileSync(abs, { encoding: "utf8" })) as PipelineConfig;
    const envs = resolveReleaseEnvs(cfg, dirname(abs));
    cfg.release = { ...(cfg.release ?? {}), envs };
    validateConfig(cfg);
    return cfg;
}

export function validateConfig(cfg: PipelineConfig): void {
    const errors: string[] = [];
    if (!ACCOUNT_RE.test(cfg.toolsAccount ?? ""))
        errors.push("toolsAccount must be a 12-digit account id");
    if (!cfg.region) errors.push("region is required");
    if (!cfg.home?.repoUrl)
        errors.push("home.repoUrl is required (git URL of the vra home repository)");
    const rel = cfg.release?.envs ?? [];
    if (rel.length === 0)
        errors.push(
            "release.envs needs at least one env (or set ledgerDir to read them from the ledger)"
        );
    const all = [...rel, ...(cfg.preview ? [cfg.preview.env] : [])];
    const names = new Set<string>();
    for (const e of all) {
        if (!NAME_RE.test(e.name)) errors.push(`env name '${e.name}' is invalid`);
        if (!ACCOUNT_RE.test(e.account))
            errors.push(`env ${e.name}: account must be a 12-digit id`);
        if (names.has(e.name)) errors.push(`env ${e.name} is listed twice`);
        names.add(e.name);
    }
    const relNames = new Set(rel.map((e) => e.name));
    for (const e of rel) {
        if (e.promotesFrom && !relNames.has(e.promotesFrom))
            errors.push(
                `env ${e.name}: promotesFrom '${e.promotesFrom}' is not a release-lane env of this pipeline`
            );
        if (e.promotesFrom === e.name) errors.push(`env ${e.name} promotes from itself`);
    }
    if (errors.length === 0) {
        try {
            envLevels(rel);
        } catch (err) {
            errors.push((err as Error).message);
        }
    }
    if (cfg.scope !== undefined && !["all", "dev"].includes(cfg.scope))
        errors.push(`scope must be "all" or "dev", got ${JSON.stringify(cfg.scope)}`);
    if (cfg.scope === "dev" && errors.length === 0 && scopeEnvs(rel, "dev").length === 0)
        errors.push('scope "dev" needs at least one release root env without requiresApproval');
    if (cfg.automationMode === "hybrid" && cfg.scope !== "dev")
        errors.push(
            'automationMode hybrid needs scope "dev" (gated envs are run with `vra promote`, not by the pipeline)'
        );
    if (cfg.release?.isolateTests === false) {
        for (const e of rel.filter((x) => x.tier === "prod" && x.test !== false))
            errors.push(
                `env ${e.name} is prod-class: release.isolateTests false would run the fork's test dependencies ` +
                    "under its deploy tier; remove it (gated envs isolate their tests by default)"
            );
    }
    if (cfg.release?.autoRollback === true && cfg.release?.isolateTests)
        errors.push(
            "release.autoRollback needs the Test action to run under the env's deploy tier; it cannot be combined " +
                "with release.isolateTests (the deploy's own smoke still rolls back)"
        );
    for (const [k, s] of Object.entries(cfg.sources ?? {})) {
        if (s) errors.push(...sourceErrors(`sources.${k}`, s as SourceSpec));
    }
    const iamMode = cfg.iamRoles?.mode ?? "create";
    if (!["create", "precreated"].includes(iamMode))
        errors.push(
            `iamRoles.mode must be create or precreated, got ${JSON.stringify(
                iamMode
            )} (report is what ` +
                "`vra pipeline plan` synthesizes with -c vraIamReport=true; it is not deployable)"
        );
    if (iamMode === "precreated" && Object.keys(cfg.iamRoles?.precreatedRoles ?? {}).length === 0)
        errors.push(
            "iamRoles.mode precreated needs iamRoles.precreatedRoles (construct path -> role name); run " +
                "`vra pipeline plan` for the list IT creates"
        );
    for (const [path, name] of Object.entries(cfg.iamRoles?.precreatedRoles ?? {})) {
        if (!ROLE_NAME_RE.test(name))
            errors.push(`iamRoles.precreatedRoles.${path}: '${name}' is not an IAM role name`);
    }
    const rnp = cfg.iamRoles?.roleNamePrefix;
    if (rnp !== undefined && !/^[A-Za-z0-9+=,.@_-]{1,20}$/.test(rnp))
        errors.push("iamRoles.roleNamePrefix must be 1-20 characters of [A-Za-z0-9+=,.@_-]");
    if (
        cfg.watch?.command !== undefined &&
        !["watch", "sync", "sync-merge"].includes(cfg.watch.command)
    )
        errors.push(
            `watch.command must be watch, sync or sync-merge, got ${JSON.stringify(
                cfg.watch.command
            )}`
        );
    if (cfg.actionsHandoff) {
        const h = cfg.actionsHandoff;
        if (!/^arn:aws[a-z-]*:iam::[0-9]{12}:oidc-provider\/[^\s]+$/.test(h.oidcProviderArn ?? ""))
            errors.push("actionsHandoff.oidcProviderArn must be an IAM OIDC provider ARN");
        const ids = h.repositoryIds;
        if (
            ids &&
            !(
                /^[0-9]+$/.test(ids.ownerId ?? "") &&
                /^[0-9]+$/.test(ids.repositoryId ?? "") &&
                /^[\w.-]+\/[\w.-]+$/.test(ids.repository ?? "")
            )
        )
            errors.push(
                "actionsHandoff.repositoryIds needs repository (owner/repo) and numeric ownerId and repositoryId"
            );
        if (handoffSubjects(h).length === 0 || (h.subjects ?? []).some((s) => !s || s === "*"))
            errors.push("actionsHandoff.subjects must list the allowed sub values (no bare *)");
    }
    for (const a of cfg.sourceWriterArns ?? [])
        if (!/^arn:aws[a-z-]*:iam::[0-9]{12}:role\/[\w+=,.@/-]+$/.test(a))
            errors.push(
                `sourceWriterArns: ${JSON.stringify(a)} is not an exact IAM role ARN (no wildcards)`
            );
    if (cfg.preview && !cfg.sources?.forkPreview) errors.push("preview needs sources.forkPreview");
    if (cfg.watch?.enabled && cfg.sources?.vra?.type !== "s3" && !cfg.watch.packageRepoUrl)
        errors.push("watch.packageRepoUrl is required (or make sources.vra an s3 source)");
    if (cfg.watch?.enabled && cfg.watch.packageRepoUrl && !cfg.watch.packageRef)
        errors.push("watch.packageRef must pin a tag or commit");
    if (cfg.stateStore) {
        for (const k of ["bucketName", "lockTableName", "keyArn"] as const) {
            if (!cfg.stateStore[k])
                errors.push(`stateStore.${k} is required when stateStore is set`);
        }
        if (
            cfg.stateStore.prefix !== undefined &&
            !/^[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/.test(cfg.stateStore.prefix)
        )
            errors.push(
                "stateStore.prefix must be a plain key prefix (no leading or trailing /, no wildcards)"
            );
    }
    for (const k of Object.keys(cfg.codebuild?.environment ?? {})) {
        if (VAMS_CONFIG_ENV_VARS.includes(k))
            errors.push(
                `codebuild.environment.${k} is a VAMS config variable: vra stops the build when a job exports ` +
                    `it (record it in envs/<env>/env.yaml synthEnv instead)`
            );
    }
    for (const e of all) {
        if (e.approvalSigningKeyArn && !/^arn:aws[a-z-]*:kms:/.test(e.approvalSigningKeyArn))
            errors.push(`env ${e.name}: approvalSigningKeyArn must be a KMS key ARN`);
    }
    if (!cfg.notifications?.topicArn) errors.push("notifications.topicArn is required");
    if (errors.length > 0) {
        throw new Error("Invalid pipeline config:\n  - " + errors.join("\n  - "));
    }
}

/** Depth of each env in the promotion graph (roots = 0). Throws on a cycle. */
export function envLevels(envs: ReleaseEnv[]): Map<string, number> {
    const byName = new Map(envs.map((e) => [e.name, e]));
    const levels = new Map<string, number>();
    const visit = (name: string, seen: string[]): number => {
        const known = levels.get(name);
        if (known !== undefined) return known;
        if (seen.includes(name))
            throw new Error(`promotion cycle: ${[...seen, name].join(" -> ")}`);
        const parent = byName.get(name)?.promotesFrom;
        const level = parent && byName.has(parent) ? visit(parent, [...seen, name]) + 1 : 0;
        levels.set(name, level);
        return level;
    };
    for (const e of envs) visit(e.name, []);
    return levels;
}

export interface PlannedAction {
    kind: "approval" | "deploy" | "test";
    env: string;
    /** Source env of a promotion; null for a root deploy. */
    promoteFrom: string | null;
    /** Action name inside its stage. */
    name: string;
    runOrder: number;
}

export interface PlannedStage {
    name: string;
    actions: PlannedAction[];
}

const STAGE_NAME_MAX = 100;

function stageName(prefix: string, names: string[], level: number): string {
    const n = `${prefix}-${names.join("_")}`;
    return n.length <= STAGE_NAME_MAX ? n : `${prefix}-wave-${level}`;
}

/**
 * The stages after Build, derived from the promotion graph. Envs at the same depth share a stage and run in
 * parallel (same runOrder); each promotes from its own promotesFrom. A level's approvals (only for envs that
 * require one) form the stage before its deploys. An approval is "final" for a leaf env.
 */
export function planRelease(envs: ReleaseEnv[]): PlannedStage[] {
    const levels = envLevels(envs);
    const hasChildren = new Set(envs.map((e) => e.promotesFrom).filter((p): p is string => !!p));
    const depth = Math.max(...Array.from(levels.values()));
    const stages: PlannedStage[] = [];
    for (let level = 0; level <= depth; level++) {
        const atLevel = envs
            .filter((e) => levels.get(e.name) === level)
            .sort((a, b) => a.name.localeCompare(b.name));
        const gated = atLevel.filter((e) => e.requiresApproval);
        if (gated.length > 0) {
            stages.push({
                name: stageName(
                    "Approve",
                    gated.map((e) => e.name),
                    level
                ),
                actions: gated.map((e) => ({
                    kind: "approval",
                    env: e.name,
                    promoteFrom: e.promotesFrom ?? null,
                    name: `${hasChildren.has(e.name) ? "Approval" : "FinalApproval"}-${e.name}`,
                    runOrder: 1,
                })),
            });
        }
        const actions: PlannedAction[] = [];
        for (const e of atLevel) {
            const from = e.promotesFrom ?? null;
            actions.push({
                kind: "deploy",
                env: e.name,
                promoteFrom: from,
                name: `${from ? "Promote" : "Deploy"}-${e.name}`,
                runOrder: 1,
            });
            if (e.test ?? hasChildren.has(e.name)) {
                actions.push({
                    kind: "test",
                    env: e.name,
                    promoteFrom: from,
                    name: `Test-${e.name}`,
                    runOrder: 2,
                });
            }
        }
        stages.push({
            name: stageName(
                "Deploy",
                atLevel.map((e) => e.name),
                level
            ),
            actions,
        });
    }
    return stages;
}
