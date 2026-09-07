/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Validation rules ported rule-by-rule from `getConfig()` in
 * infra/config/config.ts. This is the fidelity contract: when config.ts adds
 * or changes a `throw new Error(...)` (or a meaningful `console.warn`), mirror
 * it here.
 *
 * Each section below names the `getConfig()` block it mirrors by quoting that
 * block's leading comment or its error-message text — search config.ts for the
 * quoted string to find it. Anchors are quoted rather than given as line numbers
 * because a line number goes stale silently: a wrong one reads exactly like a
 * right one until the file is opened.
 *
 * Predicates return true when the rule is VIOLATED (errors) or its advisory
 * condition is active (warnings).
 *
 * Note on ordering: the builder runs `applyDerived()` before evaluating rules.
 * `applyDerived()` forces nothing (see `derived.ts`), so the config these rules read
 * is exactly what the operator entered and exactly what the serialized config.json
 * carries. Where config.ts rejects a configuration rather than adjusting it — the
 * VPC requirement is the worked example (config.ts: "Features that require a VPC") —
 * the rejection is mirrored here as an error rule, not applied as a mutation there.
 */

import type { ConfigShape, Rule } from "./types";
import { getByPath } from "./pathUtils";
import { isCidr, isVpceId } from "./fields/presignedUrlFormats";
import { ipAddressFamily } from "./fields/ipAddressFormats";

/** config.ts treats null / "" / "UNDEFINED" (and missing) all as unset. */
function isUnset(value: unknown): boolean {
    return value == null || value === "" || value === "UNDEFINED";
}

/**
 * True for the values `getConfig()`'s `== undefined` backfills treat as absent — null and undefined,
 * and nothing else. A field that is absent from `config.json` is filled with its default before the
 * matching check runs, so a rule guarded by this stays silent rather than reporting a value the
 * deployment would supply for itself. `""` is deliberately NOT absent: `getConfig()` does not backfill
 * it, so an empty string reaches the check and is rejected there.
 */
function isAbsent(value: unknown): boolean {
    return value == null;
}

/** HuggingFace token check also rejects whitespace-only (config.ts: the `huggingFaceToken.trim() === ""` checks). */
function isBlank(value: unknown): boolean {
    return isUnset(value) || (typeof value === "string" && value.trim() === "");
}

function g(cfg: ConfigShape, path: string): any {
    return getByPath(cfg, path);
}

// Regexes copied verbatim from config.ts.
const CERT_ARN_PATTERN = /^arn:aws[a-z-]*:acm:us-east-1:\d{12}:certificate\/[a-f0-9-]+$/;
/**
 * Region-name prefix to partition name and DNS suffix, matching what
 * `region_info.RegionInfo.get(region)` resolves at synth time (config.ts: "garnetIngestionQueueSqsUrl
 * must be a valid SQS URL" builds the pattern from that suffix). A region with no listed prefix is
 * commercial. Verified against every Region
 * aws-cdk-lib knows. Ordered longest-prefix-first so `us-isob-` is not shadowed by `us-iso-`.
 */
const PARTITION_DNS_SUFFIXES: { prefix: string; partition: string; suffix: string }[] = [
    { prefix: "us-gov-", partition: "aws-us-gov", suffix: "amazonaws.com" },
    { prefix: "cn-", partition: "aws-cn", suffix: "amazonaws.com.cn" },
    { prefix: "eusc-", partition: "aws-eusc", suffix: "amazonaws.eu" },
    { prefix: "us-isob-", partition: "aws-iso-b", suffix: "sc2s.sgov.gov" },
    { prefix: "us-isof-", partition: "aws-iso-f", suffix: "csp.hci.ic.gov" },
    { prefix: "us-iso-", partition: "aws-iso", suffix: "c2s.ic.gov" },
    { prefix: "eu-isoe-", partition: "aws-iso-e", suffix: "cloud.adc-e.uk" },
];
const COMMERCIAL_DNS_SUFFIX = "amazonaws.com";

/**
 * The partition implied by `env.region`, or undefined when the region is unset — the deploy-time
 * region can still come from CDK context or the environment, which the browser cannot read, so the
 * partition is unknowable and partition-specific rules stay silent.
 */
function partitionForRegionName(region: unknown): string | undefined {
    if (isUnset(region) || typeof region !== "string") return undefined;
    const match = PARTITION_DNS_SUFFIXES.find((entry) => region.startsWith(entry.prefix));
    return match ? match.partition : "aws";
}

/**
 * The partition DNS suffix implied by `env.region`, or undefined when the region is unset — the
 * deploy-time region can still come from CDK context or the environment, which the browser cannot
 * read, so the partition is unknowable and suffix-specific rules stay silent.
 */
function partitionDnsSuffix(cfg: ConfigShape): string | undefined {
    const region = g(cfg, "env.region");
    if (isUnset(region) || typeof region !== "string") return undefined;
    const match = PARTITION_DNS_SUFFIXES.find((entry) => region.startsWith(entry.prefix));
    return match ? match.suffix : COMMERCIAL_DNS_SUFFIX;
}

/**
 * SQS queue URL pattern: https://sqs.<region>.<dnsSuffix>/<account>/<queue>. The suffix is pinned to
 * the configured region's partition. When the region is unset any known partition suffix is accepted,
 * so a blank region reports only a genuinely malformed URL.
 */
function sqsUrlPattern(cfg: ConfigShape): RegExp {
    const suffix = partitionDnsSuffix(cfg);
    const suffixes = suffix
        ? [suffix]
        : [COMMERCIAL_DNS_SUFFIX, ...PARTITION_DNS_SUFFIXES.map((entry) => entry.suffix)];
    const alternation = [...new Set(suffixes)]
        .map((entry) => entry.replace(/\./g, "\\."))
        .join("|");
    return new RegExp(`^https://sqs\\.[a-z0-9-]+\\.(?:${alternation})/\\d+/[a-zA-Z0-9_-]+$`);
}

/**
 * True when the region resolves to the commercial partition (config.ts: the
 * `config.env.partition !== "aws"` gates on `useCognito.useSaml` / `useOidc` and on
 * `deadlineCloudExecutionTypeEnabled`).
 */
function isCommercialPartition(cfg: ConfigShape): boolean {
    const region = g(cfg, "env.region");
    if (isUnset(region) || typeof region !== "string") return true;
    return !PARTITION_DNS_SUFFIXES.some((entry) => region.startsWith(entry.prefix));
}

/**
 * The four OpenSearch Serverless OCU fields config.ts bounds together (config.ts: "OCU bounds must be
 * non-negative integers").
 */
const OCU_FIELD_PATHS = [
    "app.openSearch.useServerless.minIndexingOcu",
    "app.openSearch.useServerless.maxIndexingOcu",
    "app.openSearch.useServerless.minSearchOcu",
    "app.openSearch.useServerless.maxSearchOcu",
];

/**
 * OpenSearch Serverless accepts only 0, 2, 4, 8, 16, or any multiple of 16 (config.ts: "must be one of
 * 0, 2, 4, 8, 16, or any multiple of 16").
 * A value that is not a non-negative integer fails the same rule, matching config.ts's ordering where
 * the integer check runs first.
 */
function isAllowedOcu(value: unknown): boolean {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 0) return false;
    return n === 0 || n === 2 || n === 4 || n === 8 || (n >= 16 && n % 16 === 0);
}
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SECRETSMANAGER_ARN_PATTERN = /^arn:aws[a-z-]*:secretsmanager:/;

// Amazon Cognito's username limit, mirroring COGNITO_USERNAME_MAX_LENGTH in infra/config/config.ts.
const COGNITO_USERNAME_MAX_LENGTH = 128;

// The taskTimeout the RapidPipeline EKS bundle declares, mirroring
// RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS in infra/config/config.ts. The parent workflow waits
// this long for the pipeline's task-token callback, so the Kubernetes job must not outlive it.
const RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS = 14400;

// The API implementation types the deployment accepts, mirroring SUPPORTED_API_TYPES in
// infra/config/config.ts.
const SUPPORTED_API_TYPES = ["APIGATEWAY_REST"];

/** True when a value looks like a valid URL (config.ts uses `new URL(...)`). */
function isValidUrl(value: unknown): boolean {
    if (typeof value !== "string" || value === "") return false;
    try {
        // eslint-disable-next-line no-new
        new URL(value);
        return true;
    } catch {
        return false;
    }
}

/** Physna credentials supplied via an operator-managed secret ARN (config.ts: `hasSecretArn`). */
function physnaHasSecretArn(cfg: ConfigShape): boolean {
    return !isUnset(g(cfg, "app.addons.usePhysnaSync.credentialsSecretArn"));
}

/** Physna credentials supplied inline as clientId + clientSecret (config.ts: `hasInlineCreds`). */
function physnaHasInlineCreds(cfg: ConfigShape): boolean {
    return (
        !isUnset(g(cfg, "app.addons.usePhysnaSync.clientId")) &&
        !isUnset(g(cfg, "app.addons.usePhysnaSync.clientSecret"))
    );
}

/**
 * True if any external-OAuth IdP required field is unset (config.ts: the
 * `useExternalOAuthIdp` required-field checks under "Check when implementing auth providers").
 */
function oauthFieldsMissing(cfg: ConfigShape): boolean {
    const base = "app.authProvider.useExternalOAuthIdp";
    const requiredPaths = [
        "idpAuthProviderUrl",
        "lambdaAuthorizorJWTIssuerUrl",
        "lambdaAuthorizorJWTAudience",
        "idpAuthClientId",
        "idpAuthPrincipalDomain",
        "idpAuthProviderScope",
        "idpAuthProviderScopeMfa",
        "idpAuthProviderTokenEndpoint",
        "idpAuthProviderAuthorizationEndpoint",
        "idpAuthProviderDiscoveryEndpoint",
    ];
    return requiredPaths.some((p) => isUnset(g(cfg, `${base}.${p}`)));
}

/** True if any Cosmos model is enabled (config.ts: "Cosmos Predict/Transfer validation"). */
function anyCosmosModelEnabled(cfg: ConfigShape): boolean {
    const p = "app.pipelines.useNvidiaCosmos";
    return (
        !!g(cfg, `${p}.modelsPredict.text2world2B_v2.enabled`) ||
        !!g(cfg, `${p}.modelsPredict.video2world2B_v2.enabled`) ||
        !!g(cfg, `${p}.modelsPredict.text2world14B_v2.enabled`) ||
        !!g(cfg, `${p}.modelsPredict.video2world14B_v2.enabled`) ||
        !!g(cfg, `${p}.modelsTransfer.transfer2B.enabled`) ||
        !!g(cfg, `${p}.modelsReason.reason2B.enabled`) ||
        !!g(cfg, `${p}.modelsReason.reason8B.enabled`)
    );
}

/** True if any Cosmos 3 model is enabled (config.ts: "Enable at least one model in useNvidiaCosmos3.modelsOmni."). */
function anyCosmos3ModelEnabled(cfg: ConfigShape): boolean {
    const p = "app.pipelines.useNvidiaCosmos3";
    return (
        !!g(cfg, `${p}.modelsOmni.nano16B.enabled`) ||
        !!g(cfg, `${p}.modelsOmni.super64B.enabled`) ||
        !!g(cfg, `${p}.modelsOmni.superText2Image64B.enabled`) ||
        !!g(cfg, `${p}.modelsOmni.superImage2Video64B.enabled`)
    );
}

/** A Cosmos/Gr00t model with `enabled` true but an empty instanceTypes array. */
function modelInstanceTypesEmpty(cfg: ConfigShape, modelPath: string): boolean {
    if (!g(cfg, `${modelPath}.enabled`)) return false;
    const types = g(cfg, `${modelPath}.instanceTypes`);
    return !Array.isArray(types) || types.length === 0;
}

// GPUs each NVIDIA Cosmos 3 job reserves, mirroring COSMOS3_NANO_GPU_COUNT / COSMOS3_SUPER_GPU_COUNT
// in infra/config/config.ts. A tier pointed at a smaller instance leaves its jobs RUNNABLE forever,
// because AWS Batch has nowhere to place them and reports no error.
const COSMOS3_NANO_GPU_COUNT = 4;
const COSMOS3_SUPER_GPU_COUNT = 8;

// GPUs per accelerated Amazon EC2 instance type, mirroring GPU_COUNT_BY_INSTANCE_TYPE in
// infra/config/config.ts. The count is not derivable from the size — g6e.16xlarge carries one GPU
// while the nominally smaller g6e.12xlarge carries four — so the mapping is explicit. Keep the two
// tables in step; the drift check does not cover this file.
const GPU_COUNT_BY_INSTANCE_TYPE: Record<string, number> = {
    "g4dn.xlarge": 1,
    "g4dn.2xlarge": 1,
    "g4dn.4xlarge": 1,
    "g4dn.8xlarge": 1,
    "g4dn.16xlarge": 1,
    "g4dn.12xlarge": 4,
    "g4dn.metal": 8,
    "g5.xlarge": 1,
    "g5.2xlarge": 1,
    "g5.4xlarge": 1,
    "g5.8xlarge": 1,
    "g5.16xlarge": 1,
    "g5.12xlarge": 4,
    "g5.24xlarge": 4,
    "g5.48xlarge": 8,
    "g6.xlarge": 1,
    "g6.2xlarge": 1,
    "g6.4xlarge": 1,
    "g6.8xlarge": 1,
    "g6.16xlarge": 1,
    "g6.12xlarge": 4,
    "g6.24xlarge": 4,
    "g6.48xlarge": 8,
    "g6e.xlarge": 1,
    "g6e.2xlarge": 1,
    "g6e.4xlarge": 1,
    "g6e.8xlarge": 1,
    "g6e.16xlarge": 1,
    "g6e.12xlarge": 4,
    "g6e.24xlarge": 4,
    "g6e.48xlarge": 8,
    "p3.2xlarge": 1,
    "p3.8xlarge": 4,
    "p3.16xlarge": 8,
    "p3dn.24xlarge": 8,
    "p4d.24xlarge": 8,
    "p4de.24xlarge": 8,
    "p5.48xlarge": 8,
    "p5e.48xlarge": 8,
    "p5en.48xlarge": 8,
};

/** Entries at `path` that are KNOWN to carry fewer than `requiredGpus` GPUs. */
function underGpuInstanceTypes(cfg: ConfigShape, path: string, requiredGpus: number): string[] {
    const types = g(cfg, path);
    if (!Array.isArray(types)) return [];
    return types.filter((t) => {
        const gpus = GPU_COUNT_BY_INSTANCE_TYPE[String(t)];
        return gpus !== undefined && gpus < requiredGpus;
    });
}

/** Entries at `path` whose GPU count is not in the table, so it cannot be checked either way. */
function unverifiedGpuInstanceTypes(cfg: ConfigShape, path: string): string[] {
    const types = g(cfg, path);
    if (!Array.isArray(types)) return [];
    return types.filter((t) => GPU_COUNT_BY_INSTANCE_TYPE[String(t)] === undefined);
}

/** The three NVIDIA Cosmos 3 Super variants that share one AWS Batch compute environment. */
const COSMOS3_SUPER_MODELS = ["super64B", "superText2Image64B", "superImage2Video64B"];

/**
 * The instanceTypes lists of the enabled Super variants, keyed by variant name (config.ts: "the
 * enabled useNvidiaCosmos3.modelsOmni Super variants share one"). The variants share one compute
 * environment, so the pool it can launch on is the intersection of these lists.
 */
function enabledSuperInstanceTypeLists(cfg: ConfigShape): [string, string[] | undefined][] {
    return COSMOS3_SUPER_MODELS.filter((model) =>
        g(cfg, `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.enabled`)
    ).map((model): [string, string[] | undefined] => [
        model,
        g(cfg, `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`),
    ]);
}

/** The instance types every supplied list permits, mirroring intersectInstanceTypes in config.ts. */
function intersectInstanceTypes(lists: (string[] | undefined)[]): string[] {
    const present = lists.filter(
        (list): list is string[] => Array.isArray(list) && list.length > 0
    );
    if (present.length === 0) return [];
    return present.reduce((common, list) => common.filter((type) => list.includes(type)));
}

/**
 * Pipelines whose containers run in (or reach an endpoint in) PUBLIC subnets (config.ts: the
 * `useAlb.usePublicSubnet` / RapidPipeline public-subnet check).
 */
function usesContainerVpcPipeline(cfg: ConfigShape): boolean {
    return (
        !!g(cfg, "app.pipelines.useRapidPipeline.useEcs.enabled") ||
        !!g(cfg, "app.pipelines.useRapidPipeline.useEks.enabled") ||
        !!g(cfg, "app.pipelines.useModelOps.enabled")
    );
}

/**
 * Pipelines that require a PRIVATE subnet in an imported VPC (config.ts: "If using a pipeline that
 * runs in (or reaches an endpoint in) private subnets"). A longer list than the public-subnet one —
 * every AWS Batch pipeline places its compute environment in private subnets.
 */
function usesPrivateSubnetPipeline(cfg: ConfigShape): boolean {
    return (
        usesContainerVpcPipeline(cfg) ||
        !!g(cfg, "app.pipelines.useSplatToolbox.enabled") ||
        !!g(cfg, "app.pipelines.useIsaacLabTraining.enabled") ||
        !!g(cfg, "app.pipelines.useNvidiaCosmos.enabled") ||
        !!g(cfg, "app.pipelines.useNvidiaCosmos3.enabled") ||
        !!g(cfg, "app.pipelines.useNvidiaGr00t.enabled")
    );
}

function hasExternalVpc(cfg: ConfigShape): boolean {
    return (
        !!g(cfg, "app.useGlobalVpc.enabled") &&
        !isUnset(g(cfg, "app.useGlobalVpc.optionalExternalVpcId"))
    );
}

/** The configured external asset bucket entries, as an array whatever the field holds. */
function externalBuckets(cfg: ConfigShape): any[] {
    const value = g(cfg, "app.assetBuckets.externalAssetBuckets");
    return Array.isArray(value) ? value.filter((entry) => !!entry) : [];
}

/**
 * A `baseAssetsPrefix` reduced to a comparable form (config.ts: `normalizePrefix` in
 * validateExternalAssetBuckets). "", "/" and missing all mean the bucket root; anything else gets a
 * single trailing slash.
 */
function normalizeExternalPrefix(prefix: unknown): string {
    if (isUnset(prefix) || typeof prefix !== "string" || prefix === "/") return "/";
    return prefix.endsWith("/") ? prefix : prefix + "/";
}

/**
 * True when one prefix is a path-prefix of the other, so Amazon S3 cannot route an object-created
 * event to a single prefix-filtered notification (config.ts: `prefixesOverlap`). The root overlaps
 * everything.
 */
function externalPrefixesOverlap(a: string, b: string): boolean {
    if (a === "/" || b === "/") return true;
    return a === b || a.startsWith(b) || b.startsWith(a);
}

/** A per-bucket attribute reduced to undefined when unset (config.ts: `normalizeOptional`). */
function normalizeExternalAttribute(value: unknown): string | undefined {
    return isUnset(value) ? undefined : String(value);
}

/**
 * External asset bucket ARNs registered more than once with overlapping `baseAssetsPrefix` values
 * (config.ts: "has overlapping baseAssetsPrefix values").
 */
function externalBucketsWithOverlappingPrefixes(cfg: ConfigShape): string[] {
    const seen = new Map<string, string[]>();
    const offenders: string[] = [];
    for (const bucket of externalBuckets(cfg)) {
        const arn = String(bucket.bucketArn ?? "");
        const prefix = normalizeExternalPrefix(bucket.baseAssetsPrefix);
        const registered = seen.get(arn);
        if (!registered) {
            seen.set(arn, [prefix]);
            continue;
        }
        if (registered.some((other) => externalPrefixesOverlap(prefix, other))) {
            offenders.push(arn);
        }
        registered.push(prefix);
    }
    return [...new Set(offenders)];
}

/**
 * External asset bucket ARNs whose entries disagree on account, region, or KMS key (config.ts: "is
 * registered with inconsistent"). One ARN is one physical bucket, so those attributes must match
 * across every entry for it.
 */
function externalBucketsWithInconsistentAttributes(cfg: ConfigShape): string[] {
    const seen = new Map<string, (string | undefined)[]>();
    const offenders: string[] = [];
    for (const bucket of externalBuckets(cfg)) {
        const arn = String(bucket.bucketArn ?? "");
        const attributes = [
            normalizeExternalAttribute(bucket.bucketAccountId),
            normalizeExternalAttribute(bucket.bucketRegion),
            normalizeExternalAttribute(bucket.bucketKmsKeyArn),
        ];
        const first = seen.get(arn);
        if (!first) {
            seen.set(arn, attributes);
            continue;
        }
        if (attributes.some((value, index) => value !== first[index])) {
            offenders.push(arn);
        }
    }
    return [...new Set(offenders)];
}

/** True for every ISO partition variant (aws-iso, aws-iso-b, aws-iso-e, aws-iso-f). */
function isIsoPartitionRegion(region: unknown): boolean {
    return (partitionForRegionName(region) ?? "").startsWith("aws-iso");
}

/**
 * Every feature that requires a VPC, transcribed from the `vpcRequiringFeatures` collection in
 * config.ts (config.ts: "Features that require a VPC"). `label` is the exact string config.ts
 * pushes, so a builder message and a `cdk synth` failure name the feature identically. `fieldPaths`
 * are the fields the operator would change to clear the error. Adding a feature to the config.ts
 * collection means adding it here.
 */
const VPC_REQUIRING_FEATURES: {
    id: string;
    label: string;
    fieldPaths: string[];
    appliesWhen: (cfg: ConfigShape) => boolean;
}[] = [
    {
        id: "vpc-required-use-alb",
        label: "useAlb",
        fieldPaths: ["app.useAlb.enabled"],
        appliesWhen: (c) => !!g(c, "app.useAlb.enabled"),
    },
    {
        id: "vpc-required-opensearch-provisioned",
        label: "openSearch.useProvisioned",
        fieldPaths: ["app.openSearch.useProvisioned.enabled"],
        appliesWhen: (c) => !!g(c, "app.openSearch.useProvisioned.enabled"),
    },
    {
        id: "vpc-required-opensearch-serverless-private",
        label: "openSearch.useServerless (allowPublic=false)",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.allowPublic",
        ],
        appliesWhen: (c) =>
            !!g(c, "app.openSearch.useServerless.enabled") &&
            !g(c, "app.openSearch.useServerless.allowPublic"),
    },
    {
        id: "vpc-required-potree-viewer",
        label: "pipelines.usePreviewPcPotreeViewer",
        fieldPaths: ["app.pipelines.usePreviewPcPotreeViewer.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.usePreviewPcPotreeViewer.enabled"),
    },
    {
        id: "vpc-required-splat-toolbox",
        label: "pipelines.useSplatToolbox",
        fieldPaths: ["app.pipelines.useSplatToolbox.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useSplatToolbox.enabled"),
    },
    {
        id: "vpc-required-genai-metadata-labeling",
        label: "pipelines.useGenAiMetadata3dLabeling",
        fieldPaths: ["app.pipelines.useGenAiMetadata3dLabeling.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useGenAiMetadata3dLabeling.enabled"),
    },
    {
        id: "vpc-required-rapidpipeline-ecs",
        label: "pipelines.useRapidPipeline.useEcs",
        fieldPaths: ["app.pipelines.useRapidPipeline.useEcs.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useRapidPipeline.useEcs.enabled"),
    },
    {
        id: "vpc-required-rapidpipeline-eks",
        label: "pipelines.useRapidPipeline.useEks",
        fieldPaths: ["app.pipelines.useRapidPipeline.useEks.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useRapidPipeline.useEks.enabled"),
    },
    {
        id: "vpc-required-model-ops",
        label: "pipelines.useModelOps",
        fieldPaths: ["app.pipelines.useModelOps.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useModelOps.enabled"),
    },
    {
        id: "vpc-required-isaac-lab-training",
        label: "pipelines.useIsaacLabTraining",
        fieldPaths: ["app.pipelines.useIsaacLabTraining.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useIsaacLabTraining.enabled"),
    },
    {
        id: "vpc-required-preview-3d-thumbnail",
        label: "pipelines.usePreview3dThumbnail",
        fieldPaths: ["app.pipelines.usePreview3dThumbnail.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.usePreview3dThumbnail.enabled"),
    },
    {
        id: "vpc-required-nvidia-cosmos",
        label: "pipelines.useNvidiaCosmos",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useNvidiaCosmos.enabled"),
    },
    {
        id: "vpc-required-nvidia-cosmos3",
        label: "pipelines.useNvidiaCosmos3",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useNvidiaCosmos3.enabled"),
    },
    {
        id: "vpc-required-nvidia-gr00t",
        label: "pipelines.useNvidiaGr00t",
        fieldPaths: ["app.pipelines.useNvidiaGr00t.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useNvidiaGr00t.enabled"),
    },
    {
        id: "vpc-required-coordinate-transform",
        label: "pipelines.useConversionCoordinateTransform",
        fieldPaths: ["app.pipelines.useConversionCoordinateTransform.enabled"],
        appliesWhen: (c) => !!g(c, "app.pipelines.useConversionCoordinateTransform.enabled"),
    },
];

/** Partitions whose capability downgrades are gated on app.govCloud.enabled (config.ts: `restrictedPartitionRequiringFlag`). */
function requiresGovCloudFlag(region: unknown): boolean {
    const partition = partitionForRegionName(region);
    return partition === "aws-us-gov" || partition === "aws-eusc" || isIsoPartitionRegion(region);
}

export const RULES: Rule[] = [
    // ----- Restricted-partition flag agreement
    // (config.ts: "app.govCloud.enabled is the restricted-partition switch") -----
    {
        id: "restricted-partition-requires-govcloud-flag",
        severity: "error",
        fieldPaths: ["env.region", "app.govCloud.enabled"],
        appliesWhen: (c) =>
            requiresGovCloudFlag(g(c, "env.region")) && g(c, "app.govCloud.enabled") !== true,
        message:
            "Deploying to the AWS GovCloud, AWS European Sovereign Cloud, or an ISO partition requires app.govCloud.enabled to be true. The flag gates that partition's capability downgrades (including removing unsupported EventSourceMapping tags), so leaving it false deploys resources the partition rejects.",
    },
    {
        id: "iso-partition-requires-il6",
        severity: "error",
        fieldPaths: ["env.region", "app.govCloud.il6Compliant"],
        appliesWhen: (c) =>
            isIsoPartitionRegion(g(c, "env.region")) && g(c, "app.govCloud.il6Compliant") !== true,
        message: "Deploying to an ISO partition requires app.govCloud.il6Compliant to be true.",
    },

    // ----- GovCloud (config.ts: "If we are govCloud, check for certain features") -----
    {
        id: "govcloud-requires-vpc",
        severity: "error",
        fieldPaths: ["app.govCloud.enabled", "app.useGlobalVpc.enabled"],
        appliesWhen: (c) => g(c, "app.govCloud.enabled") && !g(c, "app.useGlobalVpc.enabled"),
        message: "GovCloud must have useGlobalVpc.enabled set to true.",
    },
    {
        id: "govcloud-no-cloudfront",
        severity: "error",
        fieldPaths: ["app.govCloud.enabled", "app.useCloudFront.enabled"],
        appliesWhen: (c) => g(c, "app.govCloud.enabled") && g(c, "app.useCloudFront.enabled"),
        message:
            "GovCloud does not support CloudFront. Use the ALB configuration for a VAMS front-end website deployment.",
    },
    {
        id: "govcloud-no-location",
        severity: "error",
        fieldPaths: ["app.govCloud.enabled", "app.useLocationService.enabled"],
        appliesWhen: (c) => g(c, "app.govCloud.enabled") && g(c, "app.useLocationService.enabled"),
        message: "GovCloud must have app.useLocationService.enabled set to false.",
    },
    {
        id: "govcloud-no-deadline-cloud",
        severity: "error",
        fieldPaths: ["app.govCloud.enabled", "app.pipelines.deadlineCloudExecutionTypeEnabled"],
        appliesWhen: (c) =>
            g(c, "app.govCloud.enabled") && g(c, "app.pipelines.deadlineCloudExecutionTypeEnabled"),
        message:
            "AWS Deadline Cloud is not available in GovCloud. Set app.pipelines.deadlineCloudExecutionTypeEnabled to false.",
    },

    // ----- EU Sovereign Cloud availability zones
    // (config.ts: "The EU Sovereign Cloud (Germany) region eusc-de-east-1") -----
    {
        id: "eusovereign-opensearch-az",
        severity: "error",
        fieldPaths: [
            "env.region",
            "app.openSearch.useProvisioned.enabled",
            "app.openSearch.useProvisioned.availabilityZoneCount",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useProvisioned.enabled") &&
            g(c, "env.region") === "eusc-de-east-1" &&
            g(c, "app.openSearch.useProvisioned.availabilityZoneCount") > 2,
        message:
            "Region eusc-de-east-1 (EU Sovereign Cloud) only supports up to 2 Availability Zones. " +
            "Set openSearch.useProvisioned.availabilityZoneCount to 2 when deploying OpenSearch provisioned to this region.",
    },

    // ----- GovCloud IL6 (config.ts: "Now check additional IL6 compliance") -----
    {
        id: "il6-no-cognito",
        severity: "error",
        fieldPaths: ["app.govCloud.il6Compliant", "app.authProvider.useCognito.enabled"],
        appliesWhen: (c) =>
            g(c, "app.govCloud.enabled") &&
            g(c, "app.govCloud.il6Compliant") &&
            g(c, "app.authProvider.useCognito.enabled"),
        message: "GovCloud IL6 must have app.authProvider.useCognito.enabled set to false.",
    },
    {
        id: "il6-no-waf",
        severity: "error",
        fieldPaths: ["app.govCloud.il6Compliant", "app.useWaf"],
        appliesWhen: (c) =>
            g(c, "app.govCloud.enabled") && g(c, "app.govCloud.il6Compliant") && g(c, "app.useWaf"),
        message: "GovCloud IL6 must have app.useWaf set to false.",
    },
    {
        id: "il6-requires-kms",
        severity: "error",
        fieldPaths: ["app.govCloud.il6Compliant", "app.useKmsCmkEncryption.enabled"],
        appliesWhen: (c) =>
            g(c, "app.govCloud.enabled") &&
            g(c, "app.govCloud.il6Compliant") &&
            !g(c, "app.useKmsCmkEncryption.enabled"),
        message: "GovCloud IL6 must have app.useKmsCmkEncryption.enabled set to true.",
    },

    // ----- Isaac Lab EULA
    // (config.ts: "Validate NVIDIA EULA acceptance when Isaac Lab Training is enabled") -----
    {
        id: "isaac-eula",
        severity: "error",
        fieldPaths: [
            "app.pipelines.useIsaacLabTraining.enabled",
            "app.pipelines.useIsaacLabTraining.acceptNvidiaEula",
        ],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useIsaacLabTraining.enabled") &&
            !g(c, "app.pipelines.useIsaacLabTraining.acceptNvidiaEula"),
        message:
            "Isaac Lab Training requires accepting the NVIDIA EULA — set useIsaacLabTraining.acceptNvidiaEula to true.",
    },

    // ----- Pipeline upload trigger without registration
    // (config.ts: "The upload trigger ships with the VamsSchemaRegistration custom resource") -----
    //
    // The trigger is created by the registration custom resource, which exists only when
    // autoRegisterWithVAMS is true, so an armed trigger on an unregistered pipeline is discarded
    // silently. One rule per pipeline, over the same five config.ts iterates, so the marker lands on
    // the pipeline whose two adjacent toggles disagree.
    ...(
        [
            "useConversionCadMeshMetadataExtraction",
            "useConversionCoordinateTransform",
            "usePreviewPcPotreeViewer",
            "usePreview3dThumbnail",
            "useGenAiMetadata3dLabeling",
        ] as const
    ).map((name): Rule => {
        const base = `app.pipelines.${name}`;
        return {
            id: `pipeline-armed-trigger-unregistered-${name}`,
            severity: "warning",
            fieldPaths: [
                `${base}.autoRegisterWithVAMS`,
                `${base}.autoRegisterAutoTriggerOnFileUpload`,
            ],
            appliesWhen: (c) =>
                !!g(c, `${base}.enabled`) &&
                !!g(c, `${base}.autoRegisterAutoTriggerOnFileUpload`) &&
                g(c, `${base}.autoRegisterWithVAMS`) !== true,
            message:
                `pipelines.${name}.autoRegisterAutoTriggerOnFileUpload is true but autoRegisterWithVAMS ` +
                `is not, so no registration and no upload trigger are created. Set autoRegisterWithVAMS ` +
                `to true to arm the trigger.`,
        };
    }),

    // ----- NVIDIA Cosmos (config.ts: "Cosmos Predict/Transfer validation") -----
    {
        id: "cosmos-no-model",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.enabled"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") && !anyCosmosModelEnabled(c),
        message:
            "useNvidiaCosmos is enabled but no model is enabled. Enable at least one model in modelsPredict, modelsTransfer, or modelsReason.",
    },
    {
        id: "cosmos-hf-token",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.huggingFaceToken"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            isBlank(g(c, "app.pipelines.useNvidiaCosmos.huggingFaceToken")),
        message: "useNvidiaCosmos requires a huggingFaceToken for model downloads.",
    },
    {
        id: "cosmos-text2world2b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2"
            ),
        message:
            "useNvidiaCosmos.modelsPredict.text2world2B_v2.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-video2world2b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2"
            ),
        message:
            "useNvidiaCosmos.modelsPredict.video2world2B_v2.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-text2world14b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2"
            ),
        message:
            "useNvidiaCosmos.modelsPredict.text2world14B_v2.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-video2world14b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2"
            ),
        message:
            "useNvidiaCosmos.modelsPredict.video2world14B_v2.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-transfer2b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B"),
        message:
            "useNvidiaCosmos.modelsTransfer.transfer2B.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-reason2b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsReason.reason2B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaCosmos.modelsReason.reason2B"),
        message: "useNvidiaCosmos.modelsReason.reason2B.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos-reason8b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos.modelsReason.reason8B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaCosmos.modelsReason.reason8B"),
        message: "useNvidiaCosmos.modelsReason.reason8B.instanceTypes must be a non-empty array.",
    },

    // ----- NVIDIA Gr00t (config.ts: "Gr00t Fine-Tuning validation") -----
    {
        id: "gr00t-no-model",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaGr00t.enabled"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaGr00t.enabled") &&
            !g(c, "app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled"),
        message:
            "useNvidiaGr00t is enabled but no model is enabled. Enable at least one model in modelsFinetune.",
    },
    {
        id: "gr00t-hf-token",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaGr00t.huggingFaceToken"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaGr00t.enabled") &&
            isBlank(g(c, "app.pipelines.useNvidiaGr00t.huggingFaceToken")),
        message: "useNvidiaGr00t requires a huggingFaceToken for model downloads from HuggingFace.",
    },
    {
        id: "gr00t-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaGr00t.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B"),
        message:
            "useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes must be a non-empty array.",
    },

    // ----- RapidPipeline EKS cluster version (config.ts: "RapidPipeline EKS cluster version") -----
    {
        id: "rapidpipeline-eks-cluster-version",
        severity: "error",
        fieldPaths: ["app.pipelines.useRapidPipeline.useEks.eksClusterVersion"],
        appliesWhen: (c) => {
            if (!g(c, "app.pipelines.useRapidPipeline.useEks.enabled")) return false;
            const v = g(c, "app.pipelines.useRapidPipeline.useEks.eksClusterVersion");
            return typeof v !== "string" || !/^1\.\d{2,}$/.test(v);
        },
        message:
            'useRapidPipeline.useEks.eksClusterVersion must be an Amazon EKS Kubernetes minor version of the form "1.NN" (for example "1.31").',
    },

    // ----- RapidPipeline EKS job timeout
    // (config.ts: "The job timeout sits in the middle of a three-link chain") -----
    //
    // The state machine derives its poll ceiling from this value, the Kubernetes pod's
    // activeDeadlineSeconds is set from it, and the registered pipeline bundle declares a taskTimeout
    // the parent workflow waits for. An inconsistent value produces a wrong outcome rather than an
    // error: a pod outliving the poll makes the execution report FAILED while it keeps writing output.
    {
        id: "rapidpipeline-eks-job-timeout",
        severity: "error",
        fieldPaths: ["app.pipelines.useRapidPipeline.useEks.jobTimeout"],
        appliesWhen: (c) => {
            if (!g(c, "app.pipelines.useRapidPipeline.useEks.enabled")) return false;
            const timeout = g(c, "app.pipelines.useRapidPipeline.useEks.jobTimeout");
            return typeof timeout !== "number" || !Number.isInteger(timeout) || timeout <= 0;
        },
        message: "useRapidPipeline.useEks.jobTimeout must be a positive integer number of seconds.",
    },
    {
        id: "rapidpipeline-eks-job-timeout-exceeds-task-timeout",
        severity: "error",
        fieldPaths: ["app.pipelines.useRapidPipeline.useEks.jobTimeout"],
        appliesWhen: (c) =>
            !!g(c, "app.pipelines.useRapidPipeline.useEks.enabled") &&
            Number(g(c, "app.pipelines.useRapidPipeline.useEks.jobTimeout")) >
                RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS,
        message:
            `useRapidPipeline.useEks.jobTimeout exceeds the ` +
            `${RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS}s taskTimeout the registered pipeline ` +
            `declares, so the parent workflow would stop waiting while the Kubernetes job is still ` +
            `allowed to run. Lower jobTimeout, or raise taskTimeout in ` +
            `backendPipelines/multi/rapidPipelineEKS/vamsSchema/pipeline.json to match.`,
    },

    // ----- NVIDIA Cosmos 3
    // (config.ts: the `useNvidiaCosmos3.enabled` block, "Enable at least one model in
    // useNvidiaCosmos3.modelsOmni.") -----
    {
        id: "cosmos3-no-model",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.enabled"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") && !anyCosmos3ModelEnabled(c),
        message:
            "useNvidiaCosmos3 is enabled but no model is enabled. Enable at least one model in modelsOmni.",
    },
    {
        id: "cosmos3-hf-token",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.huggingFaceToken"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            isBlank(g(c, "app.pipelines.useNvidiaCosmos3.huggingFaceToken")),
        message: "useNvidiaCosmos3 requires a huggingFaceToken for model downloads.",
    },
    {
        id: "cosmos3-nano16b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B"),
        message: "useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos3-super64b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            modelInstanceTypesEmpty(c, "app.pipelines.useNvidiaCosmos3.modelsOmni.super64B"),
        message: "useNvidiaCosmos3.modelsOmni.super64B.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos3-supertext2image64b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B"
            ),
        message:
            "useNvidiaCosmos3.modelsOmni.superText2Image64B.instanceTypes must be a non-empty array.",
    },
    {
        id: "cosmos3-superimage2video64b-instancetypes",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            modelInstanceTypesEmpty(
                c,
                "app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B"
            ),
        message:
            "useNvidiaCosmos3.modelsOmni.superImage2Video64B.instanceTypes must be a non-empty array.",
    },

    // ----- NVIDIA Cosmos 3 instance-type GPU capacity
    // (config.ts: "Every tier's instance types must be able to hold the GPUs its jobs reserve.",
    // validateInstanceTypeGpuCount) -----
    //
    // Mirrors both halves of that helper: a KNOWN instance type with too few GPUs is an error, and one
    // absent from the table is a warning rather than an error, so a newly released accelerated family
    // is not blocked by a stale table.
    {
        id: "cosmos3-nano16b-instancetypes-gpu-count",
        severity: "error",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            g(c, "app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled") &&
            underGpuInstanceTypes(
                c,
                "app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes",
                COSMOS3_NANO_GPU_COUNT
            ).length > 0,
        message:
            `useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes includes an instance type with fewer ` +
            `than ${COSMOS3_NANO_GPU_COUNT} GPUs. Nano jobs reserve ${COSMOS3_NANO_GPU_COUNT}, so ` +
            `AWS Batch can never place them on it and they stay RUNNABLE without reporting an error. ` +
            `Use g6e.12xlarge or larger.`,
    },
    {
        id: "cosmos3-nano16b-instancetypes-gpu-unverified",
        severity: "warning",
        fieldPaths: ["app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            g(c, "app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled") &&
            unverifiedGpuInstanceTypes(
                c,
                "app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes"
            ).length > 0,
        message:
            `useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes includes an instance type whose GPU ` +
            `count cannot be verified here. Nano jobs reserve ${COSMOS3_NANO_GPU_COUNT} GPUs; ` +
            `confirm the type carries at least that many.`,
    },
    {
        id: "cosmos3-super-instancetypes-gpu-count",
        severity: "error",
        fieldPaths: COSMOS3_SUPER_MODELS.map(
            (model) => `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`
        ),
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            COSMOS3_SUPER_MODELS.some(
                (model) =>
                    g(c, `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.enabled`) &&
                    underGpuInstanceTypes(
                        c,
                        `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`,
                        COSMOS3_SUPER_GPU_COUNT
                    ).length > 0
            ),
        message:
            `A useNvidiaCosmos3 Super tier's instanceTypes includes an instance type with fewer than ` +
            `${COSMOS3_SUPER_GPU_COUNT} GPUs. Super jobs reserve ${COSMOS3_SUPER_GPU_COUNT}, so AWS ` +
            `Batch can never place them on it and they stay RUNNABLE without reporting an error.`,
    },
    {
        id: "cosmos3-super-instancetypes-gpu-unverified",
        severity: "warning",
        fieldPaths: COSMOS3_SUPER_MODELS.map(
            (model) => `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`
        ),
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            COSMOS3_SUPER_MODELS.some(
                (model) =>
                    g(c, `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.enabled`) &&
                    unverifiedGpuInstanceTypes(
                        c,
                        `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`
                    ).length > 0
            ),
        message:
            `A useNvidiaCosmos3 Super tier's instanceTypes includes an instance type whose GPU count ` +
            `cannot be verified here. Super jobs reserve ${COSMOS3_SUPER_GPU_COUNT} GPUs; confirm the ` +
            `type carries at least that many.`,
    },

    // ----- NVIDIA Cosmos 3 shared Super compute environment
    // (config.ts: "the enabled useNvidiaCosmos3.modelsOmni Super variants share one") -----
    //
    // The enabled Super variants share ONE compute environment, so its instance pool is the
    // intersection of their lists. Disjoint lists leave no type any variant permits, rendering a
    // compute environment that can launch nothing while its jobs sit RUNNABLE forever without an
    // error — and the symptom gives no hint that two unrelated config blocks are what disagree.
    {
        id: "cosmos3-super-instancetypes-disjoint",
        severity: "error",
        fieldPaths: COSMOS3_SUPER_MODELS.map(
            (model) => `app.pipelines.useNvidiaCosmos3.modelsOmni.${model}.instanceTypes`
        ),
        appliesWhen: (c) => {
            if (!g(c, "app.pipelines.useNvidiaCosmos3.enabled")) return false;
            const enabled = enabledSuperInstanceTypeLists(c);
            if (enabled.length < 2) return false;
            return intersectInstanceTypes(enabled.map(([, list]) => list)).length === 0;
        },
        message:
            "The enabled useNvidiaCosmos3.modelsOmni Super variants share one AWS Batch compute environment, and their instanceTypes have no type in common. Give them at least one instance type in common, or enable only one Super variant per deployment.",
    },

    // ----- Asset buckets
    // (config.ts: "If we aren't creating a new bucket and aren't adding any external asset
    // buckets throw an error", "Validate external asset bucket entries", "Validate the default
    // asset bucket") -----
    {
        id: "assetbucket-sync-db",
        severity: "error",
        fieldPaths: ["app.assetBuckets.defaultNewBucketSyncDatabaseId"],
        appliesWhen: (c) =>
            g(c, "app.assetBuckets.createNewBucket") &&
            isUnset(g(c, "app.assetBuckets.defaultNewBucketSyncDatabaseId")),
        message:
            "Must define app.assetBuckets.defaultNewBucketSyncDatabaseId when createNewBucket is true.",
    },
    {
        id: "assetbucket-none",
        severity: "error",
        fieldPaths: ["app.assetBuckets.createNewBucket", "app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) =>
            !g(c, "app.assetBuckets.createNewBucket") &&
            !g(c, "app.assetBuckets.externalAssetBuckets"),
        message:
            "Must define a new asset bucket and/or at least one app.assetBuckets.externalAssetBuckets.",
    },
    {
        id: "assetbucket-default-too-many",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) =>
            ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).filter(
                (b) => b && b.isDefault
            ).length > 1,
        message: "At most one app.assetBuckets.externalAssetBuckets entry may set isDefault=true.",
    },
    {
        id: "assetbucket-default-required",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets", "app.assetBuckets.createNewBucket"],
        appliesWhen: (c) =>
            !g(c, "app.assetBuckets.createNewBucket") &&
            ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).filter(
                (b) => b && b.isDefault
            ).length === 0,
        message:
            "Exactly one app.assetBuckets.externalAssetBuckets entry must set isDefault=true when createNewBucket is false.",
    },

    // ----- External asset bucket entries (config.ts validateExternalAssetBuckets) -----
    // Amazon S3 requires an event-notification destination to be in the same region as the
    // bucket, and VAMS creates its notification topics in the deployment region. A mismatch
    // otherwise surfaces only as a PutBucketNotificationConfiguration InvalidArgument from a
    // custom resource, well into the deploy.
    {
        id: "externalbucket-region-matches-deployment",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets", "env.region"],
        appliesWhen: (c) => {
            const region = g(c, "env.region");
            if (isUnset(region)) return false;
            return ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).some(
                (b) => b && !isUnset(b.bucketRegion) && b.bucketRegion !== region
            );
        },
        message:
            "Every app.assetBuckets.externalAssetBuckets bucketRegion must equal env.region. Amazon S3 requires an event-notification destination to be in the same region as the bucket, and VAMS creates its notification topics in the deployment region.",
    },
    {
        id: "externalbucket-account-id-format",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) =>
            ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).some(
                (b) =>
                    b && !isUnset(b.bucketAccountId) && !/^\d{12}$/.test(String(b.bucketAccountId))
            ),
        message:
            "app.assetBuckets.externalAssetBuckets bucketAccountId must be a 12-digit AWS account ID.",
    },
    {
        id: "externalbucket-prefix-trailing-slash",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) =>
            ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).some((b) => {
                if (!b || isUnset(b.baseAssetsPrefix)) return false;
                const p = String(b.baseAssetsPrefix);
                return p !== "/" && p !== "" && !p.endsWith("/");
            }),
        message:
            "app.assetBuckets.externalAssetBuckets baseAssetsPrefix must end in a slash, or be '/' for the bucket root.",
    },
    {
        id: "externalbucket-sync-database-required",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) =>
            ((g(c, "app.assetBuckets.externalAssetBuckets") || []) as any[]).some(
                (b) => b && isUnset(b.defaultSyncDatabaseId)
            ),
        message:
            "Every app.assetBuckets.externalAssetBuckets entry must set defaultSyncDatabaseId.",
    },
    {
        id: "externalbucket-arn-partition-matches-deployment",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets", "env.region"],
        // config.ts: "uses partition '...' which does not match the deployment partition". Silent when
        // the region is unset, because the deploy-time partition is then unknowable here.
        appliesWhen: (c) => {
            const partition = isUnset(g(c, "env.region"))
                ? undefined
                : partitionForRegionName(g(c, "env.region"));
            if (!partition) return false;
            return externalBuckets(c).some((b) => {
                const arnPartition = String(b.bucketArn ?? "").split(":")[1];
                return !!arnPartition && arnPartition !== partition;
            });
        },
        message:
            "Every app.assetBuckets.externalAssetBuckets bucketArn must use the same AWS partition as the deployment region.",
    },
    {
        id: "externalbucket-overlapping-prefixes",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        // One bucket ARN may be registered under several prefixes, but Amazon S3 permits one
        // notification configuration per bucket and cannot route an object to an ambiguous prefix.
        // "/" and "" both mean the bucket root, so either overlaps every other prefix.
        appliesWhen: (c) => externalBucketsWithOverlappingPrefixes(c).length > 0,
        message:
            "An app.assetBuckets.externalAssetBuckets bucketArn is registered with overlapping baseAssetsPrefix values. Prefixes registered for the same bucket must not overlap, and the bucket root ('/' or empty) overlaps every prefix.",
    },
    {
        id: "externalbucket-inconsistent-attributes",
        severity: "error",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets"],
        appliesWhen: (c) => externalBucketsWithInconsistentAttributes(c).length > 0,
        message:
            "An app.assetBuckets.externalAssetBuckets bucketArn is registered with inconsistent bucketAccountId, bucketRegion, or bucketKmsKeyArn values across its entries. One ARN is one physical bucket, so those must match on every entry for it.",
    },
    {
        id: "externalbucket-account-matches-deployment-warn",
        severity: "warning",
        fieldPaths: ["app.assetBuckets.externalAssetBuckets", "env.account"],
        appliesWhen: (c) => {
            const account = g(c, "env.account");
            if (isUnset(account)) return false;
            return externalBuckets(c).some(
                (b) => !isUnset(b.bucketAccountId) && String(b.bucketAccountId) === String(account)
            );
        },
        message:
            "An app.assetBuckets.externalAssetBuckets bucketAccountId matches the deployment account, so that bucket is not actually cross-account. Leave bucketAccountId unset for a same-account bucket.",
    },

    // ----- Presigned URL network restrictions (config.ts: validatePresignedUrlRestrictions) -----
    //
    // A request arrives either over the public path (aws:SourceIp) or through a VPC endpoint
    // (aws:SourceVpce), so a deployment restricts on one dimension. Entry formats come from
    // `fields/presignedUrlFormats`, transcribed from that function so the builder marks exactly the
    // entries a `cdk synth` would reject.
    {
        id: "presigned-restrictions-mutually-exclusive",
        severity: "error",
        fieldPaths: ["app.assetBuckets.presignedUrlNetworkRestrictions"],
        appliesWhen: (c) => {
            const base = "app.assetBuckets.presignedUrlNetworkRestrictions";
            const ranges = g(c, `${base}.allowedIpRanges`);
            const vpceIds = g(c, `${base}.allowedVpceIds`);
            return (
                Array.isArray(ranges) &&
                ranges.length > 0 &&
                Array.isArray(vpceIds) &&
                vpceIds.length > 0
            );
        },
        message:
            "app.assetBuckets.presignedUrlNetworkRestrictions cannot set both allowedIpRanges and allowedVpceIds. Restrict presigned URLs by IP range or by VPC endpoint, not both.",
    },
    {
        id: "presigned-restrictions-ip-range-format",
        severity: "error",
        fieldPaths: ["app.assetBuckets.presignedUrlNetworkRestrictions"],
        appliesWhen: (c) => {
            const ranges = g(c, "app.assetBuckets.presignedUrlNetworkRestrictions.allowedIpRanges");
            if (!Array.isArray(ranges)) return false;
            return ranges.some((entry) => !isCidr(String(entry)));
        },
        message:
            "Every app.assetBuckets.presignedUrlNetworkRestrictions allowedIpRanges entry must be an IPv4 or IPv6 CIDR (address/prefixLength), for example 203.0.113.0/24.",
    },
    {
        id: "presigned-restrictions-vpce-id-format",
        severity: "error",
        fieldPaths: ["app.assetBuckets.presignedUrlNetworkRestrictions"],
        appliesWhen: (c) => {
            const vpceIds = g(c, "app.assetBuckets.presignedUrlNetworkRestrictions.allowedVpceIds");
            if (!Array.isArray(vpceIds)) return false;
            return vpceIds.some((entry) => !isVpceId(String(entry)));
        },
        message:
            "Every app.assetBuckets.presignedUrlNetworkRestrictions allowedVpceIds entry must be a VPC endpoint ID (vpce- followed by at least eight hexadecimal digits).",
    },

    // ----- VPC-requiring features (config.ts: "Features that require a VPC") -----
    // config.ts collects every enabled feature that needs a VPC and rejects the configuration
    // when app.useGlobalVpc.enabled is false, naming the offenders. Emitted as one rule per
    // feature so the marker lands on the feature the operator actually turned on, and so the
    // per-section error counts attribute to that feature's own section. The feature labels are
    // the strings config.ts pushes, keeping these messages diffable against the deploy error.
    ...VPC_REQUIRING_FEATURES.map(
        ({ id, label, fieldPaths, appliesWhen }): Rule => ({
            id,
            severity: "error",
            fieldPaths: ["app.useGlobalVpc.enabled", ...fieldPaths],
            appliesWhen: (c) => appliesWhen(c) && !g(c, "app.useGlobalVpc.enabled"),
            message: `app.useGlobalVpc.enabled must be true because the following enabled feature(s) require a VPC: ${label}. Set app.useGlobalVpc.enabled to true, or disable these features.`,
        })
    ),

    // ----- Global VPC subnets / CIDR
    // (config.ts: "Must define either a global VPC Cidr Range or an External VPC ID.", "If using a
    // pipeline that runs in (or reaches an endpoint in) private subnets") -----
    {
        id: "vpc-cidr-or-external",
        severity: "error",
        fieldPaths: ["app.useGlobalVpc.vpcCidrRange", "app.useGlobalVpc.optionalExternalVpcId"],
        appliesWhen: (c) =>
            g(c, "app.useGlobalVpc.enabled") &&
            isUnset(g(c, "app.useGlobalVpc.vpcCidrRange")) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalVpcId")),
        message: "Must define either a global VPC CIDR range or an external VPC ID.",
    },
    {
        id: "vpc-isolated-subnets",
        severity: "error",
        fieldPaths: ["app.useGlobalVpc.optionalExternalIsolatedSubnetIds"],
        appliesWhen: (c) =>
            hasExternalVpc(c) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalIsolatedSubnetIds")),
        message: "Must define at least one isolated subnet ID when using an external VPC ID.",
    },
    {
        id: "vpc-private-subnets",
        severity: "error",
        fieldPaths: ["app.useGlobalVpc.optionalExternalPrivateSubnetIds"],
        // The private-subnet list is wider than the public-subnet one: every AWS Batch pipeline places
        // its compute environment in private subnets, so Splat Toolbox, Isaac Lab Training and the
        // three NVIDIA pipelines require one too, not only RapidPipeline and ModelOps.
        appliesWhen: (c) =>
            hasExternalVpc(c) &&
            usesPrivateSubnetPipeline(c) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalPrivateSubnetIds")),
        message:
            "Must define at least one private subnet ID when using an external VPC with a pipeline that requires private subnets (RapidPipeline, ModelOps, Splat Toolbox, Isaac Lab Training, or an NVIDIA pipeline).",
    },
    {
        id: "vpc-public-subnets",
        severity: "error",
        fieldPaths: ["app.useGlobalVpc.optionalExternalPublicSubnetIds"],
        appliesWhen: (c) =>
            hasExternalVpc(c) &&
            ((g(c, "app.useAlb.enabled") && g(c, "app.useAlb.usePublicSubnet")) ||
                usesContainerVpcPipeline(c)) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalPublicSubnetIds")),
        message:
            "Must define at least one public subnet ID when using an external VPC with a public ALB or RapidPipeline/ModelOps.",
    },

    // ----- Front-end CloudFront / ALB
    // (config.ts: "Cloudfront + ALB check (not more than 1)", "Cloudfront + ALB neither warning
    // check", "CloudFront Custom Domain Configuration Validation") -----
    //
    // Take the severities from the two branches, not from those two leading comments: in config.ts
    // the comment naming a check sits above the OTHER one's branch. Enabling BOTH distributions is
    // the `throw new Error("...cannot have both enabled.")`; enabling NEITHER is the
    // `console.warn("...API-DRIVEN SOLUTION-ONLY DEPLOYMENT.")`, which is a supported topology.
    {
        id: "frontend-both",
        severity: "error",
        fieldPaths: ["app.useCloudFront.enabled", "app.useAlb.enabled"],
        appliesWhen: (c) => g(c, "app.useCloudFront.enabled") && g(c, "app.useAlb.enabled"),
        message:
            "Must choose either CloudFront or ALB for static website hosting (or neither) — both cannot be enabled.",
    },
    {
        id: "frontend-neither",
        severity: "warning",
        fieldPaths: ["app.useCloudFront.enabled", "app.useAlb.enabled"],
        appliesWhen: (c) => !g(c, "app.useCloudFront.enabled") && !g(c, "app.useAlb.enabled"),
        message:
            "Neither CloudFront nor ALB is enabled, so no VAMS front-end website is deployed. This is an API-driven, solution-only deployment — verify this is intended.",
    },
    {
        id: "cloudfront-domain-fields",
        severity: "error",
        fieldPaths: [
            "app.useCloudFront.customDomain.certificateArn",
            "app.useCloudFront.customDomain.domainHost",
        ],
        appliesWhen: (c) =>
            g(c, "app.useCloudFront.customDomain.enabled") &&
            (isUnset(g(c, "app.useCloudFront.customDomain.certificateArn")) ||
                isUnset(g(c, "app.useCloudFront.customDomain.domainHost"))),
        message:
            "CloudFront custom domain requires both a domain hostname and an ACM certificate ARN.",
    },
    {
        id: "cloudfront-cert-region",
        severity: "error",
        fieldPaths: ["app.useCloudFront.customDomain.certificateArn"],
        appliesWhen: (c) => {
            if (!g(c, "app.useCloudFront.customDomain.enabled")) return false;
            const cert = g(c, "app.useCloudFront.customDomain.certificateArn");
            const host = g(c, "app.useCloudFront.customDomain.domainHost");
            // Only checked once the required fields are present (config.ts: the custom-domain required-field checks run first).
            if (isUnset(cert) || isUnset(host)) return false;
            return !CERT_ARN_PATTERN.test(cert);
        },
        message:
            "CloudFront custom domain certificate ARN must be in us-east-1 (CloudFront requires us-east-1 regardless of deployment region).",
    },
    {
        id: "alb-domain-cert",
        severity: "error",
        fieldPaths: ["app.useAlb.certificateArn", "app.useAlb.domainHost"],
        appliesWhen: (c) =>
            g(c, "app.useAlb.enabled") &&
            (isUnset(g(c, "app.useAlb.certificateArn")) || isUnset(g(c, "app.useAlb.domainHost"))),
        message: "ALB deployment requires both a domain hostname and an ACM certificate ARN.",
    },

    // ----- Admin identity
    // (config.ts: "Must specify an initial admin email address", "Only what Amazon Cognito itself
    // rejects", "The admin user is a CloudFormation-managed resource keyed on its username") -----
    {
        id: "admin-email",
        severity: "error",
        fieldPaths: ["app.adminEmailAddress"],
        appliesWhen: (c) => isUnset(g(c, "app.adminEmailAddress")),
        message: "Must specify an initial admin email address.",
    },
    {
        id: "admin-userid",
        severity: "error",
        fieldPaths: ["app.adminUserId"],
        appliesWhen: (c) => isUnset(g(c, "app.adminUserId")),
        message: "Must specify an initial admin user ID.",
    },
    {
        id: "admin-userid-length",
        severity: "error",
        fieldPaths: ["app.adminUserId"],
        appliesWhen: (c) => {
            const userId = g(c, "app.adminUserId");
            return typeof userId === "string" && userId.length > COGNITO_USERNAME_MAX_LENGTH;
        },
        message: `app.adminUserId must be at most ${COGNITO_USERNAME_MAX_LENGTH} characters — the Amazon Cognito username limit. A longer value fails CreateUser mid-deploy, which rolls the whole core stack back.`,
    },
    {
        id: "admin-userid-whitespace",
        severity: "error",
        fieldPaths: ["app.adminUserId"],
        appliesWhen: (c) => {
            const userId = g(c, "app.adminUserId");
            return typeof userId === "string" && /\s/.test(userId);
        },
        message:
            "app.adminUserId cannot contain whitespace — Amazon Cognito does not accept it in a username.",
    },
    {
        id: "admin-userid-differs-from-email-warn",
        severity: "warning",
        fieldPaths: ["app.adminUserId", "app.adminEmailAddress"],
        // Both fields are immutable after the first deployment: the admin user is a
        // CloudFormation-managed resource keyed on its username, so changing either replaces it —
        // failing the deployment if the new username already exists in the pool, and otherwise
        // orphaning the previous admin. A warning, because an operator who accepts that must still be
        // able to deploy.
        appliesWhen: (c) => {
            const userId = g(c, "app.adminUserId");
            const email = g(c, "app.adminEmailAddress");
            if (isUnset(userId) || isUnset(email)) return false;
            return userId !== email;
        },
        message:
            "app.adminUserId and app.adminEmailAddress differ. Both are immutable after the first deployment: changing either replaces the Amazon Cognito admin user, which fails the deployment if the new username already exists in the pool and otherwise orphans the previous admin. Grant additional administrators through roles instead of by editing these fields.",
    },

    // ----- OpenSearch
    // (config.ts: "Error check when implementing openSearch", "OpenSearch provisioned only supports a
    // zone-aware domain", "OpenSearch provisioned shard count must be a positive integer",
    // "Error check for reindexOnDeploy") -----
    {
        id: "opensearch-one",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useProvisioned.enabled",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            g(c, "app.openSearch.useProvisioned.enabled"),
        message: "Must enable at most one OpenSearch method (Serverless or Provisioned, not both).",
    },
    {
        id: "opensearch-provisioned-az-count",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useProvisioned.enabled",
            "app.openSearch.useProvisioned.availabilityZoneCount",
        ],
        // config.ts: "OpenSearch provisioned only supports a zone-aware domain spread across 2 or 3
        // Availability Zones." 1 is the intuitive value for a low-cost development domain and the
        // form offers no hint that the minimum is 2.
        appliesWhen: (c) => {
            if (!g(c, "app.openSearch.useProvisioned.enabled")) return false;
            const count = g(c, "app.openSearch.useProvisioned.availabilityZoneCount");
            if (isAbsent(count)) return false; // backfilled to 2
            return count !== 2 && count !== 3;
        },
        message:
            "openSearch.useProvisioned.availabilityZoneCount must be either 2 or 3. Amazon OpenSearch Service supports only a zone-aware domain spread across 2 or 3 Availability Zones.",
    },
    {
        id: "opensearch-provisioned-shards",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useProvisioned.enabled",
            "app.openSearch.useProvisioned.numberOfShards",
        ],
        // config.ts: "OpenSearch provisioned shard count must be a positive integer."
        appliesWhen: (c) => {
            if (!g(c, "app.openSearch.useProvisioned.enabled")) return false;
            const shards = g(c, "app.openSearch.useProvisioned.numberOfShards");
            if (isAbsent(shards)) return false; // backfilled to 1
            const count = Number(shards);
            return !Number.isInteger(count) || count < 1;
        },
        message:
            "openSearch.useProvisioned.numberOfShards must be an integer of 1 or greater. Changing it later requires re-creating the index (disable and re-enable OpenSearch, then reindex).",
    },
    {
        id: "reindex-requires-opensearch",
        severity: "error",
        fieldPaths: ["app.openSearch.reindexOnCdkDeploy"],
        appliesWhen: (c) =>
            g(c, "app.openSearch.reindexOnCdkDeploy") &&
            !g(c, "app.openSearch.useServerless.enabled") &&
            !g(c, "app.openSearch.useProvisioned.enabled"),
        message: "reindexOnCdkDeploy requires OpenSearch Serverless or Provisioned to be enabled.",
    },

    // ----- Auth providers (config.ts: "Check when implementing auth providers") -----
    {
        id: "auth-one",
        severity: "error",
        fieldPaths: [
            "app.authProvider.useCognito.enabled",
            "app.authProvider.useExternalOAuthIdp.enabled",
        ],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useExternalOAuthIdp.enabled"),
        message:
            "Must specify only one authentication method (Cognito or external OAuth, not both).",
    },
    {
        id: "oauth-fields",
        severity: "error",
        fieldPaths: ["app.authProvider.useExternalOAuthIdp.enabled"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useExternalOAuthIdp.enabled") && oauthFieldsMissing(c),
        message:
            "External OAuth IdP requires all of its fields (provider URL, client ID, scopes, principal domain, endpoints, JWT issuer URL and audience).",
    },

    // ----- Cognito federation: SAML or OIDC, or neither
    // (config.ts: "Cognito federation. useSaml and useOidc describe how the Cognito USER POOL
    // federates") -----
    {
        id: "cognito-federation-flags-ignored-without-cognito",
        severity: "warning",
        fieldPaths: [
            "app.authProvider.useCognito.enabled",
            "app.authProvider.useCognito.useSaml",
            "app.authProvider.useCognito.useOidc",
        ],
        appliesWhen: (c) =>
            !g(c, "app.authProvider.useCognito.enabled") &&
            (g(c, "app.authProvider.useCognito.useSaml") ||
                g(c, "app.authProvider.useCognito.useOidc")),
        message:
            "useCognito.useSaml and useCognito.useOidc are ignored when useCognito.enabled is false, " +
            "and getConfig() resolves both to false. Cognito federation federates the Cognito user " +
            "pool, so it needs that pool.",
    },
    {
        id: "saml-commercial-partition-only",
        severity: "error",
        fieldPaths: ["app.authProvider.useCognito.useSaml", "env.region"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useSaml") &&
            !isCommercialPartition(c),
        message:
            "useCognito.useSaml is supported only in the commercial partition. The Amazon Cognito hosted UI " +
            "used for SAML federation is unavailable in GovCloud, the EU Sovereign Cloud, and the ISO partitions.",
    },
    {
        id: "saml-requires-provider-settings",
        severity: "warning",
        fieldPaths: ["app.authProvider.useCognito.useSaml"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useSaml"),
        message:
            "SAML federation also needs provider settings in infra/config/saml-config.ts (provider name, " +
            "Cognito domain prefix, and the provider metadata). getConfig() rejects the shipped placeholder " +
            "values, and those settings are not part of config.json so they cannot be set here.",
    },

    {
        id: "saml-and-oidc-mutually-exclusive",
        severity: "error",
        fieldPaths: ["app.authProvider.useCognito.useSaml", "app.authProvider.useCognito.useOidc"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useOidc") &&
            g(c, "app.authProvider.useCognito.useSaml"),
        message:
            "useCognito.useSaml and useCognito.useOidc cannot both be enabled. Choose one federation " +
            "method, or neither for native Amazon Cognito sign-in.",
    },
    {
        id: "oidc-commercial-partition-only",
        severity: "error",
        fieldPaths: ["app.authProvider.useCognito.useOidc", "env.region"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useOidc") &&
            !isCommercialPartition(c),
        message:
            "useCognito.useOidc is supported only in the commercial partition. The Amazon Cognito hosted " +
            "UI used for OIDC federation is unavailable in GovCloud, the EU Sovereign Cloud, and the ISO " +
            "partitions.",
    },
    {
        id: "oidc-requires-provider-settings",
        severity: "warning",
        fieldPaths: ["app.authProvider.useCognito.useOidc"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useOidc"),
        message:
            "OIDC federation also needs provider settings in infra/config/oidc-config.ts (issuer URL, " +
            "client ID, and a Secrets Manager ARN for the client secret). getConfig() rejects the shipped " +
            "placeholder values, and those settings are not part of config.json so they cannot be set here.",
    },

    // ----- AWS Deadline Cloud partition availability
    // (config.ts: "AWS Deadline Cloud is offered only in the commercial partition") -----
    {
        id: "deadline-cloud-commercial-partition-only",
        severity: "error",
        fieldPaths: ["app.pipelines.deadlineCloudExecutionTypeEnabled", "env.region"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.deadlineCloudExecutionTypeEnabled") && !isCommercialPartition(c),
        message:
            "AWS Deadline Cloud is offered only in the commercial partition. Set " +
            "app.pipelines.deadlineCloudExecutionTypeEnabled to false for this deployment Region.",
    },

    // ----- API type, throttling and endpoint type
    // (config.ts: "API Configuration Error Checks") -----
    //
    // The throttling fields live under `app.api.apiGatewayRest`, alongside the endpoint type and the
    // integration timeout — not directly under `app.api`, which holds only `apiType`.
    {
        id: "api-type-supported",
        severity: "error",
        fieldPaths: ["app.api.apiType"],
        // An absent apiType is backfilled to the default before config.ts checks it.
        appliesWhen: (c) => {
            const apiType = g(c, "app.api.apiType");
            if (isAbsent(apiType)) return false;
            return SUPPORTED_API_TYPES.indexOf(String(apiType)) === -1;
        },
        message: `app.api.apiType must be one of [${SUPPORTED_API_TYPES.join(", ")}].`,
    },
    {
        id: "api-rate-positive",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.globalRateLimit"],
        appliesWhen: (c) => Number(g(c, "app.api.apiGatewayRest.globalRateLimit")) <= 0,
        message: "app.api.apiGatewayRest.globalRateLimit must be a positive number greater than 0.",
    },
    {
        id: "api-burst-positive",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.globalBurstLimit"],
        appliesWhen: (c) => Number(g(c, "app.api.apiGatewayRest.globalBurstLimit")) <= 0,
        message:
            "app.api.apiGatewayRest.globalBurstLimit must be a positive number greater than 0.",
    },
    {
        id: "api-burst-ge-rate",
        severity: "error",
        fieldPaths: [
            "app.api.apiGatewayRest.globalBurstLimit",
            "app.api.apiGatewayRest.globalRateLimit",
        ],
        appliesWhen: (c) =>
            Number(g(c, "app.api.apiGatewayRest.globalBurstLimit")) <
            Number(g(c, "app.api.apiGatewayRest.globalRateLimit")),
        message:
            "app.api.apiGatewayRest.globalBurstLimit must be greater than or equal to globalRateLimit.",
    },
    {
        id: "api-endpoint-type-valid",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.endpointType"],
        // An absent endpointType is backfilled to "REGIONAL" before config.ts checks it.
        appliesWhen: (c) => {
            const type = g(c, "app.api.apiGatewayRest.endpointType");
            if (isAbsent(type)) return false;
            return type !== "REGIONAL" && type !== "PRIVATE";
        },
        message: "app.api.apiGatewayRest.endpointType must be 'REGIONAL' or 'PRIVATE'.",
    },

    // ----- PRIVATE API endpoint topology (config.ts: the `endpointType === "PRIVATE"` block) -----
    //
    // A PRIVATE API is reachable only from inside the VPC, so it needs the VPC, an execute-api
    // interface endpoint, and an ALB in non-public subnets to front it. Each condition is a separate
    // rule so the marker lands on the field the operator would change.
    {
        id: "api-private-requires-vpc",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.endpointType", "app.useGlobalVpc.enabled"],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") === "PRIVATE" &&
            !g(c, "app.useGlobalVpc.enabled"),
        message:
            "app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useGlobalVpc.enabled to be true.",
    },
    {
        id: "api-private-requires-vpc-endpoint",
        severity: "error",
        fieldPaths: [
            "app.api.apiGatewayRest.endpointType",
            "app.useGlobalVpc.addVpcEndpoints",
            "app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId",
        ],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") === "PRIVATE" &&
            !g(c, "app.useGlobalVpc.addVpcEndpoints") &&
            isUnset(g(c, "app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId")),
        message:
            "app.api.apiGatewayRest.endpointType 'PRIVATE' requires an execute-api interface VPC endpoint. Set app.useGlobalVpc.addVpcEndpoints to true to have VAMS create one, or supply app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId with an existing endpoint id.",
    },
    {
        id: "api-private-incompatible-with-cloudfront",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.endpointType", "app.useCloudFront.enabled"],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") === "PRIVATE" &&
            g(c, "app.useCloudFront.enabled"),
        message:
            "app.api.apiGatewayRest.endpointType 'PRIVATE' is incompatible with public CloudFront. Use ALB/VPC fronting instead.",
    },
    {
        id: "api-private-requires-alb",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.endpointType", "app.useAlb.enabled"],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") === "PRIVATE" &&
            !g(c, "app.useAlb.enabled"),
        message:
            "app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useAlb.enabled to be true — a private API must be fronted by the ALB.",
    },
    {
        id: "api-private-requires-private-alb-subnets",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.endpointType", "app.useAlb.usePublicSubnet"],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") === "PRIVATE" &&
            g(c, "app.useAlb.usePublicSubnet"),
        message:
            "app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useAlb.usePublicSubnet to be false. A public-subnet ALB would expose an internet-facing path to the private API.",
    },
    {
        id: "api-regional-vpce-unused-warn",
        severity: "warning",
        fieldPaths: [
            "app.api.apiGatewayRest.endpointType",
            "app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId",
        ],
        appliesWhen: (c) =>
            g(c, "app.api.apiGatewayRest.endpointType") !== "PRIVATE" &&
            !isUnset(g(c, "app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId")),
        message:
            "app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId is set but will not be used. It applies only to a PRIVATE endpoint; a REGIONAL endpoint is public and does not route through a VPC endpoint.",
    },

    // ----- API integration timeout
    // (config.ts: "apiGatewayTimeoutTime must be a whole number of") -----
    {
        id: "api-timeout-range",
        severity: "error",
        fieldPaths: ["app.api.apiGatewayRest.apiGatewayTimeoutTime"],
        appliesWhen: (c) => {
            const t = g(c, "app.api.apiGatewayRest.apiGatewayTimeoutTime");
            if (t === undefined || t === null || t === "") return false;
            const n = Number(t);
            return !Number.isInteger(n) || n < 29 || n > 300;
        },
        message:
            "app.api.apiGatewayRest.apiGatewayTimeoutTime must be a whole number of seconds between 29 and 300.",
    },
    {
        id: "api-timeout-quota-notice",
        severity: "warning",
        fieldPaths: ["app.api.apiGatewayRest.apiGatewayTimeoutTime"],
        appliesWhen: (c) => Number(g(c, "app.api.apiGatewayRest.apiGatewayTimeoutTime")) > 29,
        message:
            "An integration timeout above 29 seconds requires an approved account-level increase to the Amazon API Gateway 'Integration timeout' quota (L-E5AE38E3) in the deployment Region. Request the increase before deploying, otherwise the deployment fails.",
    },

    // ----- IP ranges (config.ts: "Validate IP ranges configuration") -----
    {
        id: "ip-range-shape",
        severity: "error",
        fieldPaths: ["app.authProvider.authorizerOptions.allowedIpRanges"],
        appliesWhen: (c) => {
            const ranges = g(c, "app.authProvider.authorizerOptions.allowedIpRanges");
            if (!Array.isArray(ranges)) return false;
            return ranges.some((r: unknown) => !Array.isArray(r) || r.length !== 2);
        },
        message: "Each IP range must be an array of exactly 2 IP addresses [min, max].",
    },
    {
        // IPv6 is supported: the authorizer compares numerically with Python's `ipaddress`, so an
        // IPv6 range is expressible and matches. An IPv4-only pattern here rejected a configuration
        // the deployment accepts.
        id: "ip-range-format",
        severity: "error",
        fieldPaths: ["app.authProvider.authorizerOptions.allowedIpRanges"],
        appliesWhen: (c) => {
            const ranges = g(c, "app.authProvider.authorizerOptions.allowedIpRanges");
            if (!Array.isArray(ranges)) return false;
            return ranges.some(
                (r: any) =>
                    Array.isArray(r) &&
                    r.length === 2 &&
                    (ipAddressFamily(r[0]) === undefined || ipAddressFamily(r[1]) === undefined)
            );
        },
        message:
            'Each allowed IP range endpoint must be an IPv4 or IPv6 literal, for example ["192.168.1.1", "192.168.1.255"] or ["2001:db8::", "2001:db8::ffff"]. A CIDR, a zone index, or a hostname is rejected.',
    },
    {
        // A mixed pair has no ordering, so the authorizer refuses to compare it and the entry admits
        // nobody. Rejecting it at authoring time is what stops a range that silently never matches.
        id: "ip-range-family",
        severity: "error",
        fieldPaths: ["app.authProvider.authorizerOptions.allowedIpRanges"],
        appliesWhen: (c) => {
            const ranges = g(c, "app.authProvider.authorizerOptions.allowedIpRanges");
            if (!Array.isArray(ranges)) return false;
            return ranges.some((r: any) => {
                if (!Array.isArray(r) || r.length !== 2) return false;
                const min = ipAddressFamily(r[0]);
                const max = ipAddressFamily(r[1]);
                return min !== undefined && max !== undefined && min !== max;
            });
        },
        message:
            "Both ends of an allowed IP range must be the same address family. A mixed IPv4/IPv6 pair has no ordering and is ignored by the authorizer, so the range would admit nobody.",
    },

    // ----- Garnet Framework (config.ts: "Garnet Framework Configuration Validation") -----
    {
        id: "garnet-endpoint",
        severity: "error",
        fieldPaths: ["app.addons.useGarnetFramework.garnetApiEndpoint"],
        appliesWhen: (c) =>
            g(c, "app.addons.useGarnetFramework.enabled") &&
            isUnset(g(c, "app.addons.useGarnetFramework.garnetApiEndpoint")),
        message: "Garnet Framework requires garnetApiEndpoint when enabled.",
    },
    {
        id: "garnet-token",
        severity: "error",
        fieldPaths: ["app.addons.useGarnetFramework.garnetApiToken"],
        appliesWhen: (c) =>
            g(c, "app.addons.useGarnetFramework.enabled") &&
            isUnset(g(c, "app.addons.useGarnetFramework.garnetApiToken")),
        message: "Garnet Framework requires garnetApiToken when enabled.",
    },
    {
        id: "garnet-sqs",
        severity: "error",
        fieldPaths: ["app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl"],
        appliesWhen: (c) =>
            g(c, "app.addons.useGarnetFramework.enabled") &&
            isUnset(g(c, "app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl")),
        message: "Garnet Framework requires garnetIngestionQueueSqsUrl when enabled.",
    },
    {
        id: "garnet-endpoint-url",
        severity: "error",
        fieldPaths: ["app.addons.useGarnetFramework.garnetApiEndpoint"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useGarnetFramework.enabled")) return false;
            const endpoint = g(c, "app.addons.useGarnetFramework.garnetApiEndpoint");
            if (isUnset(endpoint)) return false; // covered by garnet-endpoint
            try {
                // eslint-disable-next-line no-new
                new URL(endpoint);
                return false;
            } catch {
                return true;
            }
        },
        message: "Garnet Framework garnetApiEndpoint must be a valid URL.",
    },
    {
        id: "garnet-sqs-format",
        severity: "error",
        fieldPaths: ["app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl", "env.region"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useGarnetFramework.enabled")) return false;
            const url = g(c, "app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl");
            if (isUnset(url)) return false; // covered by garnet-sqs
            return !sqsUrlPattern(c).test(url);
        },
        message:
            "Garnet Framework garnetIngestionQueueSqsUrl must be a valid SQS URL for the deployment " +
            "partition (https://sqs.<region>.<dnsSuffix>/<account>/<queue>, e.g. amazonaws.com in the " +
            "commercial and GovCloud partitions, amazonaws.com.cn in China, amazonaws.eu in the EU Sovereign Cloud).",
    },

    // ----- Physna Sync (config.ts: "Physna Sync Configuration Validation") -----
    {
        id: "physna-tenant-uuid",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.tenantId"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const tenant = g(c, "app.addons.usePhysnaSync.tenantId");
            return isUnset(tenant) || !UUID_PATTERN.test(tenant);
        },
        message: "Physna Sync requires tenantId to be a valid UUID when enabled.",
    },
    {
        id: "physna-apibaseendpoint-required",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.apiBaseEndpoint"],
        appliesWhen: (c) =>
            g(c, "app.addons.usePhysnaSync.enabled") &&
            isUnset(g(c, "app.addons.usePhysnaSync.apiBaseEndpoint")),
        message: "Physna Sync requires apiBaseEndpoint when enabled.",
    },
    {
        id: "physna-apibaseendpoint-url",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.apiBaseEndpoint"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const endpoint = g(c, "app.addons.usePhysnaSync.apiBaseEndpoint");
            if (isUnset(endpoint)) return false; // covered by physna-apibaseendpoint-required
            return !isValidUrl(endpoint);
        },
        message: "Physna Sync apiBaseEndpoint must be a valid URL.",
    },
    {
        id: "physna-apibaseendpoint-trailing-slash",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.apiBaseEndpoint"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const endpoint = g(c, "app.addons.usePhysnaSync.apiBaseEndpoint");
            if (isUnset(endpoint) || !isValidUrl(endpoint)) return false; // ordered after the above
            return typeof endpoint === "string" && !endpoint.endsWith("/");
        },
        message: "Physna Sync apiBaseEndpoint must end with a trailing slash '/'.",
    },
    {
        id: "physna-authtokenendpoint-required",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.authTokenEndpoint"],
        appliesWhen: (c) =>
            g(c, "app.addons.usePhysnaSync.enabled") &&
            isUnset(g(c, "app.addons.usePhysnaSync.authTokenEndpoint")),
        message: "Physna Sync requires authTokenEndpoint when enabled.",
    },
    {
        id: "physna-authtokenendpoint-url",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.authTokenEndpoint"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const endpoint = g(c, "app.addons.usePhysnaSync.authTokenEndpoint");
            if (isUnset(endpoint)) return false; // covered by physna-authtokenendpoint-required
            return !isValidUrl(endpoint);
        },
        message: "Physna Sync authTokenEndpoint must be a valid URL.",
    },
    {
        id: "physna-authtype",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.authType"],
        appliesWhen: (c) =>
            g(c, "app.addons.usePhysnaSync.enabled") &&
            g(c, "app.addons.usePhysnaSync.authType") !== "cognito",
        message: 'Physna Sync authType must be "cognito" (the only supported value).',
    },
    {
        id: "physna-credentials-required",
        severity: "error",
        fieldPaths: [
            "app.addons.usePhysnaSync.clientId",
            "app.addons.usePhysnaSync.clientSecret",
            "app.addons.usePhysnaSync.credentialsSecretArn",
        ],
        appliesWhen: (c) =>
            g(c, "app.addons.usePhysnaSync.enabled") &&
            !physnaHasSecretArn(c) &&
            !physnaHasInlineCreds(c),
        message:
            "Physna Sync requires credentials when enabled. Set both clientId and clientSecret, or credentialsSecretArn.",
    },
    {
        id: "physna-secretarn-format",
        severity: "error",
        fieldPaths: ["app.addons.usePhysnaSync.credentialsSecretArn"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            if (!physnaHasSecretArn(c)) return false; // inline-credential path
            const arn = g(c, "app.addons.usePhysnaSync.credentialsSecretArn");
            return !SECRETSMANAGER_ARN_PATTERN.test(arn);
        },
        message: "Physna Sync credentialsSecretArn must be a valid AWS Secrets Manager secret ARN.",
    },

    // ===== Warnings (config.ts console.warn advisories) =====
    {
        id: "waf-disabled-warn",
        severity: "warning",
        fieldPaths: ["app.useWaf"],
        appliesWhen: (c) => !g(c, "app.useWaf"),
        message:
            "WAF is disabled. Ensure other firewall measures are in place to prevent illicit network access.",
    },
    {
        id: "alb-public-subnet-warn",
        severity: "warning",
        fieldPaths: ["app.useAlb.usePublicSubnet"],
        appliesWhen: (c) => g(c, "app.useAlb.enabled") && g(c, "app.useAlb.usePublicSubnet"),
        message:
            "ALB public subnets are enabled. This can expose your static website to the public internet — verify this is intended.",
    },
    {
        id: "vpc-no-endpoints-warn",
        severity: "warning",
        fieldPaths: ["app.useGlobalVpc.addVpcEndpoints"],
        appliesWhen: (c) =>
            g(c, "app.useGlobalVpc.enabled") && !g(c, "app.useGlobalVpc.addVpcEndpoints"),
        message:
            "Add VPC Endpoints is disabled. Ensure the VPC already has all required interface endpoints for VAMS to operate.",
    },
    {
        id: "vpc-all-lambdas-needs-ssm-endpoint-warn",
        severity: "warning",
        fieldPaths: ["app.useGlobalVpc.useForAllLambdas", "app.useGlobalVpc.addVpcEndpoints"],
        // config.ts: "requires an operator-managed SSM interface VPC endpoint". Every VAMS Lambda
        // resolves its resource names from SSM Parameter Store at cold start, so without that endpoint
        // an in-VPC deployment fails on the first request rather than at deploy.
        appliesWhen: (c) =>
            g(c, "app.useGlobalVpc.enabled") &&
            g(c, "app.useGlobalVpc.useForAllLambdas") &&
            !g(c, "app.useGlobalVpc.addVpcEndpoints"),
        message:
            "useGlobalVpc.useForAllLambdas with addVpcEndpoints=false requires an operator-managed SSM interface VPC endpoint (com.amazonaws.<region>.ssm). All VAMS Lambda functions resolve resource names from SSM Parameter Store at cold start and fail without it.",
    },
    {
        id: "auth-userpassword-warn",
        severity: "warning",
        fieldPaths: ["app.authProvider.useCognito.useUserPasswordAuthFlow"],
        appliesWhen: (c) =>
            g(c, "app.authProvider.useCognito.enabled") &&
            g(c, "app.authProvider.useCognito.useUserPasswordAuthFlow"),
        message:
            "Cognito USER_PASSWORD_AUTH flow is enabled (non-SRP). This may be flagged as a security finding in some environments.",
    },
    {
        id: "external-vpc-context-warn",
        severity: "warning",
        fieldPaths: ["app.useGlobalVpc.optionalExternalVpcId", "env.loadContextIgnoreVPCStacks"],
        appliesWhen: (c) => hasExternalVpc(c) && !g(c, "env.loadContextIgnoreVPCStacks"),
        message:
            "Importing an external VPC/subnets: if you hit VPC/subnet lookup errors, synthesize first with loadContextIgnoreVPCStacks enabled.",
    },
    {
        id: "garnet-no-opensearch-warn",
        severity: "warning",
        fieldPaths: ["app.addons.useGarnetFramework.enabled"],
        appliesWhen: (c) =>
            g(c, "app.addons.useGarnetFramework.enabled") &&
            !g(c, "app.openSearch.useServerless.enabled") &&
            !g(c, "app.openSearch.useProvisioned.enabled"),
        message:
            "Garnet Framework is enabled but OpenSearch is disabled. Garnet indexing works independently of VAMS search.",
    },

    // ----- OpenSearch Serverless generation + OCU bounds
    // (config.ts: "NEXTGEN collection groups require standby replicas" through "OCU bounds must be
    // non-negative integers") -----
    {
        id: "aoss-nextgen-not-in-govcloud",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.nextGen",
            "app.govCloud.enabled",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            g(c, "app.openSearch.useServerless.nextGen") &&
            g(c, "app.govCloud.enabled"),
        message:
            "openSearch.useServerless.nextGen is not supported when app.govCloud.enabled is true (GovCloud and EU Sovereign Cloud). Set nextGen to false for these partitions.",
    },
    {
        id: "aoss-serverless-not-in-eusovereign",
        severity: "error",
        fieldPaths: ["app.openSearch.useServerless.enabled", "env.region"],
        // Keyed on the region's partition rather than app.govCloud.enabled: GovCloud supports
        // OpenSearch Serverless, only the EU Sovereign Cloud (aws-eusc) does not.
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            partitionForRegionName(g(c, "env.region")) === "aws-eusc",
        message:
            "openSearch.useServerless is not supported in the EU Sovereign Cloud (aws-eusc). Set useServerless.enabled to false and use openSearch.useProvisioned instead.",
    },
    {
        id: "aoss-nextgen-requires-standby-replicas",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.nextGen",
            "app.openSearch.useServerless.enableStandbyReplicas",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            g(c, "app.openSearch.useServerless.nextGen") &&
            !g(c, "app.openSearch.useServerless.enableStandbyReplicas"),
        message:
            "openSearch.useServerless.nextGen requires enableStandbyReplicas to be true. NEXTGEN collection groups do not support disabled standby replicas.",
    },
    {
        id: "aoss-scale-to-zero-requires-nextgen",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.nextGen",
            "app.openSearch.useServerless.minIndexingOcu",
            "app.openSearch.useServerless.minSearchOcu",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            !g(c, "app.openSearch.useServerless.nextGen") &&
            (g(c, "app.openSearch.useServerless.minIndexingOcu") === 0 ||
                g(c, "app.openSearch.useServerless.minSearchOcu") === 0),
        message:
            "A minimum OCU of 0 (scale-to-zero) requires next-gen Serverless. Set nextGen to true, or set minIndexingOcu and minSearchOcu to 1 or greater.",
    },
    {
        id: "aoss-ocu-values-allowed",
        severity: "error",
        fieldPaths: OCU_FIELD_PATHS,
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            OCU_FIELD_PATHS.some((p) => !isAllowedOcu(g(c, p))),
        message:
            "Each OpenSearch Serverless OCU value must be a non-negative integer and one of 0, 2, 4, 8, 16, or any multiple of 16.",
    },
    {
        id: "aoss-max-ocu-at-least-one",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.maxIndexingOcu",
            "app.openSearch.useServerless.maxSearchOcu",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            (Number(g(c, "app.openSearch.useServerless.maxIndexingOcu")) < 1 ||
                Number(g(c, "app.openSearch.useServerless.maxSearchOcu")) < 1),
        message:
            "openSearch.useServerless.maxIndexingOcu and maxSearchOcu must each be 1 or greater — a maximum of 0 would leave the collection with no capacity.",
    },
    {
        id: "aoss-max-ocu-not-below-min",
        severity: "error",
        fieldPaths: OCU_FIELD_PATHS,
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            (Number(g(c, "app.openSearch.useServerless.maxIndexingOcu")) <
                Number(g(c, "app.openSearch.useServerless.minIndexingOcu")) ||
                Number(g(c, "app.openSearch.useServerless.maxSearchOcu")) <
                    Number(g(c, "app.openSearch.useServerless.minSearchOcu"))),
        message:
            "Each OpenSearch Serverless maximum OCU must be greater than or equal to its matching minimum (maxIndexingOcu >= minIndexingOcu, maxSearchOcu >= minSearchOcu).",
    },

    // ----- OpenSearch Serverless network access
    // (config.ts: "cannot use a public OpenSearch Serverless collection", "Public Serverless in
    // GovCloud/EU Sovereign Cloud is allowed but not recommended", "will deploy WITHOUT its
    // data-plane VPC endpoint") -----
    {
        id: "aoss-public-with-all-lambdas-in-vpc",
        severity: "error",
        fieldPaths: [
            "app.openSearch.useServerless.enabled",
            "app.openSearch.useServerless.allowPublic",
            "app.useGlobalVpc.useForAllLambdas",
        ],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            g(c, "app.openSearch.useServerless.allowPublic") &&
            g(c, "app.useGlobalVpc.enabled") &&
            g(c, "app.useGlobalVpc.useForAllLambdas"),
        message:
            "A deployment that places all Lambdas behind the VPC (useGlobalVpc.enabled and useForAllLambdas both true) cannot use a public OpenSearch Serverless collection. Set openSearch.useServerless.allowPublic to false to place the collection behind a VPC endpoint.",
    },
    {
        id: "aoss-public-in-restricted-partition-warn",
        severity: "warning",
        fieldPaths: ["app.openSearch.useServerless.allowPublic", "app.govCloud.enabled"],
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            g(c, "app.openSearch.useServerless.allowPublic") &&
            g(c, "app.govCloud.enabled"),
        message:
            "A public OpenSearch Serverless collection (allowPublic=true) is not recommended for GovCloud or EU Sovereign Cloud deployments. Consider setting allowPublic to false.",
    },
    {
        id: "aoss-private-nextgen-deferred-endpoint-warn",
        severity: "warning",
        fieldPaths: [
            "app.openSearch.useServerless.allowPublic",
            "app.openSearch.useServerless.nextGen",
            "app.useGlobalVpc.addVpcEndpoints",
        ],
        // A private NEXTGEN collection is reached through a standard EC2 interface endpoint, which
        // VAMS creates only when addVpcEndpoints is true. Without it the deployment comes up with no
        // data-plane endpoint, no network access policy, and no index mappings, and nothing else says so.
        appliesWhen: (c) =>
            g(c, "app.openSearch.useServerless.enabled") &&
            !g(c, "app.openSearch.useServerless.allowPublic") &&
            g(c, "app.openSearch.useServerless.nextGen") &&
            !g(c, "app.useGlobalVpc.addVpcEndpoints"),
        message:
            "A private next-gen OpenSearch Serverless collection (allowPublic=false, nextGen=true) with useGlobalVpc.addVpcEndpoints=false deploys WITHOUT its data-plane VPC endpoint and network access policy, and index creation is skipped. Create the com.amazonaws.<region>.aoss-data interface endpoint and a matching network access policy naming it in SourceVPCEs, then set openSearch.useServerless.deployDeferredIndexSchema to true for one deployment and reindex.",
    },

    // ----- Marketplace container images (config.ts: "is enabled but ecrContainerImageURI is not set to a real image") -----
    ...(
        [
            [
                "rapidpipeline-ecs",
                "app.pipelines.useRapidPipeline.useEcs",
                "useRapidPipeline.useEcs",
            ],
            [
                "rapidpipeline-eks",
                "app.pipelines.useRapidPipeline.useEks",
                "useRapidPipeline.useEks",
            ],
            ["model-ops", "app.pipelines.useModelOps", "useModelOps"],
        ] as const
    ).map(([slug, base, label]) => ({
        id: `container-image-placeholder-${slug}`,
        severity: "error" as const,
        fieldPaths: [`${base}.ecrContainerImageURI`],
        appliesWhen: (c: ConfigShape) => {
            if (!g(c, `${base}.enabled`)) return false;
            const uri = String(g(c, `${base}.ecrContainerImageURI`) ?? "");
            return (
                isBlank(uri) ||
                ["<ACCOUNTID>", "<REGION>", "<ECR-REPOSITORY>", "<IMAGE-ID>", "<IMAGE-TAG>"].some(
                    (token) => uri.includes(token)
                )
            );
        },
        message: `${label} is enabled but ecrContainerImageURI is still empty or holds the template placeholder. Subscribe to the AWS Marketplace container and set the image URI, or disable the pipeline.`,
    })),

    // ----- Bedrock model id (config.ts: "cross-Region inference-profile prefix exists only in the commercial partition") -----
    {
        id: "bedrock-model-id-required",
        severity: "error",
        fieldPaths: ["app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId"],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useGenAiMetadata3dLabeling.enabled") &&
            isBlank(g(c, "app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId")),
        message:
            "useGenAiMetadata3dLabeling requires a bedrockModelId available in this partition and Region. The restricted-partition presets leave it empty because the commercial cross-Region inference profiles do not exist there.",
    },
    {
        id: "bedrock-model-id-commercial-only-prefix",
        severity: "error",
        fieldPaths: ["app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId", "env.region"],
        // Keyed on the partition the configured region resolves to. `env.partition` is derived at
        // synth from that region and is not part of config.json, so it is never present here.
        appliesWhen: (c) => {
            if (!g(c, "app.pipelines.useGenAiMetadata3dLabeling.enabled")) return false;
            const id = String(
                g(c, "app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId") ?? ""
            );
            return !isCommercialPartition(c) && (id.startsWith("global.") || id.startsWith("us."));
        },
        message:
            'bedrockModelId uses a "global." or "us." cross-Region inference-profile prefix, which exists only in the commercial partition. Use a model id offered in this partition (GovCloud uses the "us-gov." prefix).',
    },

    // ----- Physna outbound endpoints (config.ts: "must use https, or the credentials VAMS sends to it travel in cleartext") -----
    ...(
        [
            ["api-base", "app.addons.usePhysnaSync.apiBaseEndpoint", "apiBaseEndpoint"],
            ["auth-token", "app.addons.usePhysnaSync.authTokenEndpoint", "authTokenEndpoint"],
        ] as const
    ).map(([slug, path, label]) => ({
        id: `physna-endpoint-https-${slug}`,
        severity: "error" as const,
        fieldPaths: [path],
        appliesWhen: (c: ConfigShape) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const value = String(g(c, path) ?? "");
            if (isBlank(value)) return false; // a separate required-field rule covers empty
            try {
                return new URL(value).protocol !== "https:";
            } catch {
                return false; // a malformed URL is reported by the parseability rule
            }
        },
        message: `usePhysnaSync.${label} must use https. The add-on sends the Physna OAuth client secret to it as HTTP Basic credentials, which plain http transmits in cleartext.`,
    })),
    ...(
        [
            ["api-base", "app.addons.usePhysnaSync.apiBaseEndpoint", "apiBaseEndpoint"],
            ["auth-token", "app.addons.usePhysnaSync.authTokenEndpoint", "authTokenEndpoint"],
        ] as const
    ).map(([slug, path, label]) => ({
        id: `physna-endpoint-private-host-${slug}`,
        severity: "error" as const,
        fieldPaths: [path],
        appliesWhen: (c: ConfigShape) => {
            if (!g(c, "app.addons.usePhysnaSync.enabled")) return false;
            const value = String(g(c, path) ?? "");
            if (isBlank(value)) return false;
            let host: string;
            try {
                host = new URL(value).hostname.toLowerCase().replace(/^\[|\]$/g, "");
            } catch {
                return false;
            }
            return (
                host === "localhost" ||
                host.endsWith(".localhost") ||
                host.endsWith(".local") ||
                host.endsWith(".internal") ||
                host === "::1" ||
                /^127\./.test(host) ||
                /^169\.254\./.test(host) ||
                /^10\./.test(host) ||
                /^192\.168\./.test(host) ||
                /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
                /^(fc|fd)[0-9a-f]{2}:/.test(host)
            );
        },
        message: `usePhysnaSync.${label} points at a loopback, link-local, or private address. It is called by a Lambda that can read the VAMS asset buckets, so it must name an external service.`,
    })),
];

/** Evaluate every rule against the config and return those that apply. */
export function evaluateRules(config: ConfigShape): Rule[] {
    return RULES.filter((rule) => {
        try {
            return rule.appliesWhen(config);
        } catch {
            // A predicate that throws on an unexpected shape should not crash the UI.
            return false;
        }
    });
}
