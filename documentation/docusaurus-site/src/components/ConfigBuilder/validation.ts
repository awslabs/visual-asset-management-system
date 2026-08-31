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

/** config.ts treats null / "" / "UNDEFINED" (and missing) all as unset. */
function isUnset(value: unknown): boolean {
    return value == null || value === "" || value === "UNDEFINED";
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
const IPV4_PATTERN =
    /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
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

function usesContainerVpcPipeline(cfg: ConfigShape): boolean {
    return (
        !!g(cfg, "app.pipelines.useRapidPipeline.useEcs.enabled") ||
        !!g(cfg, "app.pipelines.useRapidPipeline.useEks.enabled") ||
        !!g(cfg, "app.pipelines.useModelOps.enabled")
    );
}

function hasExternalVpc(cfg: ConfigShape): boolean {
    return (
        !!g(cfg, "app.useGlobalVpc.enabled") &&
        !isUnset(g(cfg, "app.useGlobalVpc.optionalExternalVpcId"))
    );
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
        fieldPaths: [
            "app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.instanceTypes",
            "app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.instanceTypes",
            "app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.instanceTypes",
        ],
        appliesWhen: (c) =>
            g(c, "app.pipelines.useNvidiaCosmos3.enabled") &&
            ["super64B", "superText2Image64B", "superImage2Video64B"].some(
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
        appliesWhen: (c) =>
            hasExternalVpc(c) &&
            usesContainerVpcPipeline(c) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalPrivateSubnetIds")),
        message:
            "Must define at least one private subnet ID when using RapidPipeline/ModelOps with an external VPC.",
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
    {
        id: "frontend-neither",
        severity: "error",
        fieldPaths: ["app.useCloudFront.enabled", "app.useAlb.enabled"],
        appliesWhen: (c) => !g(c, "app.useCloudFront.enabled") && !g(c, "app.useAlb.enabled"),
        message:
            "Must enable either CloudFront or ALB for static website hosting (one of the two).",
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

    // ----- Admin identity (config.ts: "Must specify an initial admin email address") -----
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

    // ----- OpenSearch
    // (config.ts: "Error check when implementing openSearch", "Error check for reindexOnDeploy") -----
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

    // ----- API throttling (config.ts: "API Configuration Error Checks") -----
    {
        id: "api-rate-positive",
        severity: "error",
        fieldPaths: ["app.api.globalRateLimit"],
        appliesWhen: (c) => Number(g(c, "app.api.globalRateLimit")) <= 0,
        message: "API globalRateLimit must be a positive number greater than 0.",
    },
    {
        id: "api-burst-positive",
        severity: "error",
        fieldPaths: ["app.api.globalBurstLimit"],
        appliesWhen: (c) => Number(g(c, "app.api.globalBurstLimit")) <= 0,
        message: "API globalBurstLimit must be a positive number greater than 0.",
    },
    {
        id: "api-burst-ge-rate",
        severity: "error",
        fieldPaths: ["app.api.globalBurstLimit", "app.api.globalRateLimit"],
        appliesWhen: (c) =>
            Number(g(c, "app.api.globalBurstLimit")) < Number(g(c, "app.api.globalRateLimit")),
        message: "API globalBurstLimit must be greater than or equal to globalRateLimit.",
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
                    (!IPV4_PATTERN.test(r[0]) || !IPV4_PATTERN.test(r[1]))
            );
        },
        message:
            'Invalid IP address format in an allowed IP range. Expected e.g. ["192.168.1.1", "192.168.1.255"].',
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
        id: "frontend-both-warn",
        severity: "warning",
        fieldPaths: ["app.useCloudFront.enabled", "app.useAlb.enabled"],
        appliesWhen: (c) => g(c, "app.useCloudFront.enabled") && g(c, "app.useAlb.enabled"),
        message:
            "Both CloudFront and ALB are enabled. Typically only one front-end distribution is used.",
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
        fieldPaths: ["app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId", "env.partition"],
        appliesWhen: (c) => {
            if (!g(c, "app.pipelines.useGenAiMetadata3dLabeling.enabled")) return false;
            const id = String(
                g(c, "app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId") ?? ""
            );
            const partition = String(g(c, "env.partition") ?? "aws");
            return partition !== "aws" && (id.startsWith("global.") || id.startsWith("us."));
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
