/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Validation rules ported line-by-line from `getConfig()` in
 * infra/config/config.ts. This is the fidelity contract: when config.ts adds
 * or changes a `throw new Error(...)` (or a meaningful `console.warn`), mirror
 * it here. Each rule carries the approximate config.ts line for diffing.
 *
 * Predicates return true when the rule is VIOLATED (errors) or its advisory
 * condition is active (warnings).
 *
 * Note on ordering: the builder runs `applyDerived()` (auto-enable VPC) before
 * evaluating rules, so `useGlobalVpc.enabled` here reflects the same forced
 * value config.ts computes at runtime (config.ts:458-479).
 */

import type { ConfigShape, Rule } from "./types";
import { getByPath } from "./pathUtils";

/** config.ts treats null / "" / "UNDEFINED" (and missing) all as unset. */
function isUnset(value: unknown): boolean {
    return value == null || value === "" || value === "UNDEFINED";
}

/** HuggingFace token check also rejects whitespace-only (config.ts:504 .trim()). */
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
const SQS_URL_PATTERN = /^https:\/\/sqs\.[a-z0-9-]+\.amazonaws\.com\/\d+\/[a-zA-Z0-9_-]+$/;
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

/** Physna credentials supplied via an operator-managed secret ARN (config.ts:1623). */
function physnaHasSecretArn(cfg: ConfigShape): boolean {
    return !isUnset(g(cfg, "app.addons.usePhysnaSync.credentialsSecretArn"));
}

/** Physna credentials supplied inline as clientId + clientSecret (config.ts:1627). */
function physnaHasInlineCreds(cfg: ConfigShape): boolean {
    return (
        !isUnset(g(cfg, "app.addons.usePhysnaSync.clientId")) &&
        !isUnset(g(cfg, "app.addons.usePhysnaSync.clientSecret"))
    );
}

/** True if any external-OAuth IdP required field is unset (config.ts:849-877). */
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

/** True if any Cosmos model is enabled (config.ts:486-493). */
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

/** A Cosmos/Gr00t model with `enabled` true but an empty instanceTypes array. */
function modelInstanceTypesEmpty(cfg: ConfigShape, modelPath: string): boolean {
    if (!g(cfg, `${modelPath}.enabled`)) return false;
    const types = g(cfg, `${modelPath}.instanceTypes`);
    return !Array.isArray(types) || types.length === 0;
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

export const RULES: Rule[] = [
    // ----- GovCloud (config.ts:415-432) -----
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

    // ----- EU Sovereign Cloud availability zones (config.ts:1273-1282) -----
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

    // ----- GovCloud IL6 (config.ts:436-453) -----
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

    // ----- Isaac Lab EULA (config.ts:193-203) -----
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

    // ----- NVIDIA Cosmos (config.ts:482-581) -----
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

    // ----- NVIDIA Gr00t (config.ts:584-614) -----
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

    // ----- Asset buckets (config.ts:617-633) -----
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

    // ----- Global VPC subnets / CIDR (config.ts:665-718, 758-777) -----
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

    // ----- Front-end CloudFront / ALB (config.ts:727-756, 779-791) -----
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
            // Only checked once the required fields are present (config.ts:734 first).
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

    // ----- Admin identity (config.ts:793-811) -----
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

    // ----- OpenSearch (config.ts:813-830) -----
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

    // ----- Auth providers (config.ts:833-882) -----
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

    // ----- API throttling (config.ts:884-901) -----
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

    // ----- IP ranges (config.ts:903-926) -----
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

    // ----- Garnet Framework (config.ts:928-975) -----
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
        fieldPaths: ["app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useGarnetFramework.enabled")) return false;
            const url = g(c, "app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl");
            if (isUnset(url)) return false; // covered by garnet-sqs
            return !SQS_URL_PATTERN.test(url);
        },
        message:
            "Garnet Framework garnetIngestionQueueSqsUrl must be a valid SQS URL (https://sqs.<region>.amazonaws.com/<account>/<queue>).",
    },

    // ----- Physna Sync (config.ts:1560-1653) -----
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
