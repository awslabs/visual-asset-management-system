/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Profile presets for the config builder.
 *
 * These are literal copies of the three deploy templates:
 *   - infra/config/config.template.commercial.json
 *   - infra/config/config.template.govcloud.json
 *   - infra/config/config.template.eusovereign.json
 *
 * Keep them byte-for-byte in sync with those files. `makeDefaultConfig()`
 * returns a deep clone so callers can freely mutate. GovCloud differs from
 * Commercial in ~12 values (FIPS, KMS, govCloud, VPC, OpenSearch serverless-
 * vs-provisioned, Location Service, ALB-vs-CloudFront, bedrock model id, GenAI
 * auto-trigger). EU Sovereign Cloud is GovCloud with 4 further differences
 * (region, FIPS off, aws-eusc certificate ARN, amazonaws.eu ECR URIs).
 */

import type { ConfigShape, Profile } from "./types";
import { cloneConfig } from "./pathUtils";

/** Cosmos model trees are identical across both profiles — share one literal. */
const COSMOS_DEFAULT = {
    enabled: false,
    huggingFaceToken: "",
    useCodeBuild: false,
    useWarmInstances: false,
    warmInstanceCount: 1,
    modelsPredict: {
        text2world2B_v2: {
            enabled: false,
            autoRegisterWithVAMS: true,
            instanceTypes: ["g6e.12xlarge", "g5.12xlarge", "g5.48xlarge"],
            maxVCpus: 192,
        },
        video2world2B_v2: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g6e.12xlarge", "g5.12xlarge", "g5.48xlarge"],
            maxVCpus: 192,
        },
        text2world14B_v2: {
            enabled: false,
            autoRegisterWithVAMS: true,
            instanceTypes: ["g6e.48xlarge", "p5.48xlarge"],
            maxVCpus: 192,
        },
        video2world14B_v2: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g6e.48xlarge", "p5.48xlarge"],
            maxVCpus: 192,
        },
    },
    modelsTransfer: {
        transfer2B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g6e.48xlarge", "p5.48xlarge"],
            maxVCpus: 192,
        },
    },
    modelsReason: {
        reason2B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g6e.12xlarge", "g5.12xlarge"],
            maxVCpus: 192,
        },
        reason8B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g6e.12xlarge", "g6e.24xlarge"],
            maxVCpus: 192,
        },
    },
};

const GR00T_DEFAULT = {
    enabled: false,
    huggingFaceToken: "",
    useCodeBuild: false,
    useWarmInstances: false,
    warmInstanceCount: 1,
    modelsFinetune: {
        gr00tN1_5_3B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            instanceTypes: ["g6e.4xlarge", "g6e.12xlarge", "g5.12xlarge"],
            maxVCpus: 192,
        },
    },
};

const COMMERCIAL: ConfigShape = {
    name: "vams",
    env: {
        account: null,
        region: null,
        loadContextIgnoreVPCStacks: false,
    },
    app: {
        baseStackName: "prod",
        assetBuckets: {
            createNewBucket: true,
            defaultNewBucketSyncDatabaseId: "default",
            presignedUrlNetworkRestrictions: {
                allowedIpRanges: [],
                allowedVpceIds: [],
            },
            externalAssetBuckets: null,
        },
        adminUserId: "administrator",
        adminEmailAddress: "adminEmail@example.com",
        iamRoleConfig: {
            useCustomBootstrapRoles: false,
            useCustomVamsStackRoles: false,
        },
        useFips: false,
        useWaf: true,
        addStackCloudTrailLogs: true,
        useKmsCmkEncryption: {
            enabled: false,
            optionalExternalCmkArn: null,
        },
        govCloud: {
            enabled: false,
            il6Compliant: false,
        },
        useGlobalVpc: {
            enabled: false,
            useForAllLambdas: false,
            addVpcEndpoints: true,
            optionalExternalVpcId: null,
            optionalExternalIsolatedSubnetIds: null,
            optionalExternalPrivateSubnetIds: null,
            optionalExternalPublicSubnetIds: null,
            vpcCidrRange: "10.1.0.0/16",
        },
        openSearch: {
            useServerless: {
                enabled: true,
                nextGen: true,
                allowPublic: true,
                enableStandbyReplicas: true,
                minIndexingOcu: 2,
                maxIndexingOcu: 16,
                minSearchOcu: 2,
                maxSearchOcu: 16,
                deployDeferredIndexSchema: false,
            },
            useProvisioned: {
                enabled: false,
                availabilityZoneCount: 2,
                numberOfShards: 1,
                dataNodeInstanceType: "r7g.large.search",
                masterNodeInstanceType: "r7g.large.search",
                ebsInstanceNodeSizeGb: 120,
            },
            reindexOnCdkDeploy: false,
        },
        useLocationService: { enabled: true },
        useAlb: {
            enabled: false,
            usePublicSubnet: false,
            addAlbS3SpecialVpcEndpoint: true,
            domainHost: "vams1.example.com",
            certificateArn: "arn:aws-us-gov:acm:<REGION>:<ACCOUNTID>:certificate/<CERTIFICATEID>",
            optionalHostedZoneId: null,
        },
        useCloudFront: {
            enabled: true,
            customDomain: {
                enabled: false,
                domainHost: "",
                certificateArn: "",
                optionalHostedZoneId: "",
            },
        },
        pipelines: {
            useConversion3dBasic: {
                enabled: true,
                autoRegisterWithVAMS: true,
            },
            usePreview3dThumbnail: {
                enabled: false,
                autoRegisterWithVAMS: true,
                autoRegisterAutoTriggerOnFileUpload: true,
            },
            useConversionCadMeshMetadataExtraction: {
                enabled: false,
                autoRegisterWithVAMS: true,
                autoRegisterAutoTriggerOnFileUpload: true,
            },
            useConversionCoordinateTransform: {
                enabled: false,
                useCodeBuild: false,
                autoRegisterWithVAMS: true,
                autoRegisterAutoTriggerOnFileUpload: false,
            },
            usePreviewPcPotreeViewer: {
                enabled: false,
                autoRegisterWithVAMS: true,
                autoRegisterAutoTriggerOnFileUpload: true,
                sqsAutoRunOnAssetModified: false,
            },
            useGenAiMetadata3dLabeling: {
                enabled: false,
                bedrockModelId: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                autoRegisterWithVAMS: true,
                autoRegisterAutoTriggerOnFileUpload: false,
            },
            useSplatToolbox: {
                enabled: false,
                autoRegisterWithVAMS: true,
            },
            useRapidPipeline: {
                useEcs: {
                    enabled: false,
                    ecrContainerImageURI:
                        "<ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG>",
                    autoRegisterWithVAMS: true,
                },
                useEks: {
                    enabled: false,
                    ecrContainerImageURI:
                        "<ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG>",
                    autoRegisterWithVAMS: true,
                    eksClusterVersion: "1.31",
                    nodeInstanceType: "m5.2xlarge",
                    minNodes: 1,
                    maxNodes: 10,
                    desiredNodes: 2,
                    jobTimeout: 7200,
                    jobMemory: "16Gi",
                    jobCpu: "2000m",
                    jobBackoffLimit: 2,
                    jobTTLSecondsAfterFinished: 600,
                    observability: {
                        enableControlPlaneLogs: false,
                        enableContainerInsights: false,
                    },
                },
            },
            useModelOps: {
                enabled: false,
                ecrContainerImageURI:
                    "<ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG>",
                autoRegisterWithVAMS: true,
            },
            useIsaacLabTraining: {
                enabled: false,
                acceptNvidiaEula: false,
                useCodeBuild: false,
                autoRegisterWithVAMS: true,
                keepWarmInstance: false,
            },
            useNvidiaCosmos: cloneConfig(COSMOS_DEFAULT),
            useNvidiaGr00t: cloneConfig(GR00T_DEFAULT),
        },
        addons: {
            useGarnetFramework: {
                enabled: false,
                garnetApiEndpoint: "",
                garnetApiToken: "",
                garnetIngestionQueueSqsUrl: "",
            },
            usePhysnaSync: {
                enabled: false,
                tenantId: "",
                apiBaseEndpoint: "https://app-api.physna.com/v3/",
                authTokenEndpoint:
                    "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token",
                authType: "cognito",
                credentialsSecretArn: "",
                clientId: "",
                clientSecret: "",
            },
        },
        authProvider: {
            presignedUrlTimeoutSeconds: 86400,
            authorizerOptions: {
                allowedIpRanges: [],
            },
            useCognito: {
                enabled: true,
                useSaml: false,
                useUserPasswordAuthFlow: false,
                credTokenTimeoutSeconds: 3600,
            },
            useExternalOAuthIdp: {
                enabled: false,
                idpAuthProviderUrl: null,
                idpAuthClientId: null,
                idpAuthProviderScope: null,
                idpAuthProviderScopeMfa: null,
                idpAuthPrincipalDomain: null,
                idpAuthProviderTokenEndpoint: null,
                idpAuthProviderAuthorizationEndpoint: null,
                idpAuthProviderDiscoveryEndpoint: null,
                lambdaAuthorizorJWTIssuerUrl: null,
                lambdaAuthorizorJWTAudience: null,
            },
        },
        webUi: {
            optionalBannerHtmlMessage: "",
            allowUnsafeEvalFeatures: false,
        },
        api: {
            apiType: "APIGATEWAY_REST",
            apiGatewayRest: {
                globalRateLimit: 50,
                globalBurstLimit: 100,
                endpointType: "REGIONAL",
                optionalExternalPrivateApigVPCEId: "",
            },
        },
        metadataSchema: {
            autoLoadDefaultAssetLinksSchema: true,
            autoLoadDefaultDatabaseSchema: true,
            autoLoadDefaultAssetSchema: true,
            autoLoadDefaultAssetFileSchema: true,
        },
    },
};

/**
 * The GovCloud preset is the Commercial preset with the ~12 GovCloud-specific
 * overrides applied. Building it from a clone guarantees the two stay
 * structurally identical except for the documented differences.
 */
function buildGovCloud(): ConfigShape {
    const cfg = cloneConfig(COMMERCIAL);
    cfg.app.useFips = true;
    cfg.app.useKmsCmkEncryption.enabled = true;
    cfg.app.govCloud.enabled = true;
    cfg.app.useGlobalVpc.enabled = true;
    cfg.app.openSearch.useServerless.enabled = false;
    cfg.app.openSearch.useServerless.nextGen = false;
    cfg.app.openSearch.useServerless.allowPublic = false;
    cfg.app.openSearch.useServerless.enableStandbyReplicas = false;
    cfg.app.openSearch.useProvisioned.enabled = true;
    cfg.app.useLocationService.enabled = false;
    cfg.app.useAlb.enabled = true;
    cfg.app.useCloudFront.enabled = false;
    cfg.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId =
        "global.anthropic.claude-sonnet-4-20250514-v1:0";
    cfg.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterAutoTriggerOnFileUpload = true;
    return cfg;
}

const GOVCLOUD: ConfigShape = buildGovCloud();

/**
 * The EU Sovereign Cloud preset is the GovCloud preset with 4 further
 * differences: the fixed `eusc-de-east-1` region, FIPS off, an `aws-eusc`
 * partition certificate ARN, and `amazonaws.eu` ECR image URIs.
 */
function buildEuSovereign(): ConfigShape {
    const cfg = cloneConfig(GOVCLOUD);
    cfg.env.region = "eusc-de-east-1";
    cfg.app.useFips = false;
    cfg.app.useAlb.certificateArn =
        "arn:aws-eusc:acm:<REGION>:<ACCOUNTID>:certificate/<CERTIFICATEID>";
    const euEcr =
        "<ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.eu/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG>";
    cfg.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = euEcr;
    cfg.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI = euEcr;
    cfg.app.pipelines.useModelOps.ecrContainerImageURI = euEcr;
    return cfg;
}

const EUSOVEREIGN: ConfigShape = buildEuSovereign();

/** Return a fresh, mutable config object for the given profile. */
export function makeDefaultConfig(profile: Profile): ConfigShape {
    return cloneConfig(
        profile === "eusovereign" ? EUSOVEREIGN : profile === "govcloud" ? GOVCLOUD : COMMERCIAL
    );
}
