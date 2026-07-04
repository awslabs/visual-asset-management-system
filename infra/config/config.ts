/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { RemovalPolicy } from "aws-cdk-lib";
import { Runtime } from "aws-cdk-lib/aws-lambda";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import { readFileSync } from "fs";
import { join } from "path";
import * as dotenv from "dotenv";
import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { region_info } from "aws-cdk-lib";

dotenv.config();

//Top level configurations
export const VAMS_VERSION = "2.6.0";

export const LAMBDA_PYTHON_RUNTIME = Runtime.PYTHON_3_12;
export const LAMBDA_NODE_RUNTIME = Runtime.NODEJS_22_X;
export const LAMBDA_MEMORY_SIZE = 5308;
export const OPENSEARCH_VERSION = cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_3_5;
export const OPENSEARCH_VERSION_EUSOVEREIGN =
    cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_2_19;
export const CODEBUILD_BUILD_IMAGE = codebuild.LinuxBuildImage.STANDARD_7_0;

export const STACK_WAF_DESCRIPTION =
    "(SO9299) (uksb-1608h3hqer) (VAMS-WAF) (version:" +
    VAMS_VERSION +
    ") WAF Components for the Visual Asset Management Systems";
export const STACK_CORE_DESCRIPTION =
    "(SO9299) (uksb-1608h3hqer) (VAMS-CORE) (version:" +
    VAMS_VERSION +
    ") Primary Components for the Visual Asset Management Systems";

// Custom Authorizer Configuration
export const CUSTOM_AUTHORIZER_IGNORED_PATHS = ["/api/amplify-config", "/api/version"];

// Backend API implementation type. Only API Gateway REST is supported today; the value is
// fixed so the api-nestedStack can select an implementation construct and future API types
// (e.g. an ALB-based entry point) can be added without changing the selection contract.
export const API_TYPE_APIGATEWAY_REST = "APIGATEWAY_REST";
export const SUPPORTED_API_TYPES = [API_TYPE_APIGATEWAY_REST];

// Fixed REST API deployment stage name. This is intentionally NOT a per-deployment config
// option: the value is also baked into the VamsCLI endpoint constants and the web app's
// /api/* fronting, so it must stay constant across the stack and clients. The web
// distribution (CloudFront originPath / ALB redirect) absorbs this stage so browser/CLI
// base URLs remain /api/*.
export const API_GATEWAY_STAGE_NAME = "api";

export function getConfig(app: cdk.App): Config {
    const file: string = readFileSync(join(__dirname, "config.json"), {
        encoding: "utf8",
        flag: "r",
    });

    const configPublic: ConfigPublic = JSON.parse(file);
    const config: Config = <Config>configPublic;

    //Debugging Variables
    config.dockerDefaultPlatform = <string>process.env.DOCKER_DEFAULT_PLATFORM;
    config.enableCdkNag = true;

    console.log("Python Version: ", LAMBDA_PYTHON_RUNTIME.name);
    console.log("Node Version: ", LAMBDA_NODE_RUNTIME.name);

    //Main Variables (Parameter fall-back chain: context -> config file -> environment variables -> other fallback)
    config.env.account = <string>(
        (app.node.tryGetContext("account") || config.env.account || process.env.CDK_DEFAULT_ACCOUNT)
    );
    config.env.region = <string>(
        (app.node.tryGetContext("region") ||
            config.env.region ||
            process.env.CDK_DEFAULT_REGION ||
            process.env.REGION ||
            "us-east-1")
    );
    config.env.partition = region_info.RegionInfo.get(config.env.region).partition!;

    config.app.baseStackName =
        (app.node.tryGetContext("stack-name") ||
            config.app.baseStackName ||
            process.env.STACK_NAME) +
        "-" +
        config.env.region;

    config.app.adminEmailAddress = <string>(
        (app.node.tryGetContext("adminEmailAddress") ||
            process.env.ADMIN_EMAIL_ADDRESS ||
            config.app.adminEmailAddress)
    );
    config.app.adminUserId = <string>(app.node.tryGetContext("adminUserId") ||
        app.node.tryGetContext("adminEmailAddress") || //user email in this case for ENV backwards compatibility
        process.env.ADMIN_USER_ID ||
        process.env.ADMIN_EMAIL_ADDRESS || //user email in this case for ENV backwards compatibility
        config.app.adminUserId);

    config.app.authProvider.useCognito.credTokenTimeoutSeconds = <number>(
        (app.node.tryGetContext("credTokenTimeoutSeconds") ||
            config.app.authProvider.useCognito.credTokenTimeoutSeconds ||
            process.env.CRED_TOKEN_TIMEOUT_SECONDS ||
            3600)
    );

    config.app.authProvider.presignedUrlTimeoutSeconds = <number>(
        (app.node.tryGetContext("presignedUrlTimeoutSeconds") ||
            config.app.authProvider.presignedUrlTimeoutSeconds ||
            process.env.PRESIGNED_URL_TIMEOUT_SECONDS ||
            86400)
    );

    config.app.useFips = <boolean>(
        (app.node.tryGetContext("useFips") ||
            config.app.useFips ||
            process.env.AWS_USE_FIPS_ENDPOINT ||
            false)
    );
    config.app.useWaf = <boolean>(
        (app.node.tryGetContext("useWaf") || config.app.useWaf || process.env.AWS_USE_WAF || false)
    );
    config.env.loadContextIgnoreVPCStacks = <boolean>(
        (app.node.tryGetContext("loadContextIgnoreVPCStacks") ||
            config.env.loadContextIgnoreVPCStacks ||
            false)
    );

    config.app.openSearch.reindexOnCdkDeploy = <boolean>(
        (app.node.tryGetContext("reindexOnCdkDeploy") ||
            config.app.openSearch.reindexOnCdkDeploy ||
            false)
    );

    config.app.openSearch.useServerless.deployDeferredIndexSchema = <boolean>(
        (app.node.tryGetContext("deployDeferredIndexSchema") ||
            config.app.openSearch.useServerless.deployDeferredIndexSchema ||
            false)
    );

    //OpenSearch Variables - Dual Index Configuration
    config.openSearchAssetIndexName = "vams-assets-v3";
    config.openSearchFileIndexName = "vams-files-v3";
    config.openSearchAssetIndexNameSSMParam =
        "/" + [config.name + "-" + config.app.baseStackName, "aos", "assetIndexName"].join("/");
    config.openSearchFileIndexNameSSMParam =
        "/" + [config.name + "-" + config.app.baseStackName, "aos", "fileIndexName"].join("/");
    config.openSearchDomainEndpointSSMParam =
        "/" + [config.name + "-" + config.app.baseStackName, "aos", "endPoint"].join("/");

    //Location Service Variables
    config.locationServiceApiKeyArnSSMParam =
        "/" + [config.name + "-" + config.app.baseStackName, "location", "apiKeyArn"].join("/");

    //Website URL Param Variables
    config.webUrlDeploymentSSMParam =
        "/" + [config.name + "-" + config.app.baseStackName, "web", "deployedUrl"].join("/");

    //Fill in some basic values to false if blank
    //Note: usually added for backwards compatabibility of an old config file that hasn't had the newest elements added
    if (config.app.openSearch.useServerless.enabled == undefined) {
        config.app.openSearch.useServerless.enabled = false;
    }

    //Generation of the OpenSearch Serverless collection group: true uses NEXTGEN (supports scale-to-zero),
    //false uses CLASSIC. Defaults to NEXTGEN for commercial partitions and CLASSIC for GovCloud/EU Sovereign
    //Cloud, where the next-generation generation is not yet available.
    if (config.app.openSearch.useServerless.nextGen == undefined) {
        config.app.openSearch.useServerless.nextGen = !(config.app.govCloud.enabled === true);
    }

    //Whether the Serverless collection accepts public network access. Defaults to true; set to false to
    //place the collection behind a VPC endpoint (recommended for production).
    if (config.app.openSearch.useServerless.allowPublic == undefined) {
        config.app.openSearch.useServerless.allowPublic = true;
    }

    //Whether the collection group uses standby replicas for cross-AZ redundancy. NEXTGEN collection groups
    //require standby replicas, so this defaults to the value of nextGen (enabled for NEXTGEN, disabled for
    //CLASSIC, where it favors lower cost and can be enabled for production high availability).
    if (config.app.openSearch.useServerless.enableStandbyReplicas == undefined) {
        config.app.openSearch.useServerless.enableStandbyReplicas =
            config.app.openSearch.useServerless.nextGen;
    }

    //OCU capacity bounds for the collection group. Allowed OCU values are 0, 2, 4, 8, 16, or any multiple of
    //16. minIndexing/minSearch default to 2; maxIndexing/maxSearch default to 16.
    if (config.app.openSearch.useServerless.minIndexingOcu == undefined) {
        config.app.openSearch.useServerless.minIndexingOcu = 2;
    }
    if (config.app.openSearch.useServerless.maxIndexingOcu == undefined) {
        config.app.openSearch.useServerless.maxIndexingOcu = 16;
    }
    if (config.app.openSearch.useServerless.minSearchOcu == undefined) {
        config.app.openSearch.useServerless.minSearchOcu = 2;
    }
    if (config.app.openSearch.useServerless.maxSearchOcu == undefined) {
        config.app.openSearch.useServerless.maxSearchOcu = 16;
    }

    //When a private next-gen deployment was made with addVpcEndpoints=false, VAMS deferred creating the index
    //schema (the collection was not reachable). After the operator manually creates the VPC endpoint and
    //network policy, set this to true for one deployment to run the schema-deploy and create the indexes; then
    //set it back to false. Ignored when the endpoint is created by VAMS (addVpcEndpoints=true).
    if (config.app.openSearch.useServerless.deployDeferredIndexSchema == undefined) {
        config.app.openSearch.useServerless.deployDeferredIndexSchema = false;
    }

    if (config.app.openSearch.useProvisioned.enabled == undefined) {
        config.app.openSearch.useProvisioned.enabled = false;
    }

    //Number of Availability Zones the provisioned OpenSearch domain (and its VPC subnets) spread across.
    //Defaults to 2; set to 3 for a 3-AZ domain, or keep 2 for regions/partitions that expose only 2 AZs (e.g. EU Sovereign Cloud).
    if (config.app.openSearch.useProvisioned.availabilityZoneCount == undefined) {
        config.app.openSearch.useProvisioned.availabilityZoneCount = 2;
    }

    //Number of primary shards per provisioned OpenSearch index. Defaults to 1. Increase for large indexes (roughly >60 GB / ~3M asset or file records). Changing this
    //requires re-creating the index (disable/re-enable OpenSearch, then reindex).
    if (config.app.openSearch.useProvisioned.numberOfShards == undefined) {
        config.app.openSearch.useProvisioned.numberOfShards = 1;
    }

    if (config.app.openSearch.reindexOnCdkDeploy == undefined) {
        config.app.openSearch.reindexOnCdkDeploy = false;
    }

    if (config.app.pipelines.useSplatToolbox.enabled == undefined) {
        config.app.pipelines.useSplatToolbox.enabled = false;
    }

    if (config.app.pipelines.usePreviewPcPotreeViewer.enabled == undefined) {
        config.app.pipelines.usePreviewPcPotreeViewer.enabled = false;
    }

    if (config.app.pipelines.useGenAiMetadata3dLabeling.enabled == undefined) {
        config.app.pipelines.useGenAiMetadata3dLabeling.enabled = false;
    }

    if (config.app.pipelines.useRapidPipeline.useEcs.enabled == undefined) {
        config.app.pipelines.useRapidPipeline.useEcs.enabled = false;
    }

    if (config.app.pipelines.useRapidPipeline.useEks.enabled == undefined) {
        config.app.pipelines.useRapidPipeline.useEks.enabled = false;
    }

    if (config.app.pipelines.useModelOps.enabled == undefined) {
        config.app.pipelines.useModelOps.enabled = false;
    }

    if (config.app.pipelines.useIsaacLabTraining == undefined) {
        config.app.pipelines.useIsaacLabTraining = {
            enabled: false,
            acceptNvidiaEula: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: true,
            keepWarmInstance: false,
        };
    }

    if (config.app.pipelines.useIsaacLabTraining.enabled == undefined) {
        config.app.pipelines.useIsaacLabTraining.enabled = false;
    }

    if (config.app.pipelines.useIsaacLabTraining.useCodeBuild == undefined) {
        config.app.pipelines.useIsaacLabTraining.useCodeBuild = false;
    }

    if (config.app.pipelines.useIsaacLabTraining.keepWarmInstance == undefined) {
        config.app.pipelines.useIsaacLabTraining.keepWarmInstance = false;
    }

    // Validate NVIDIA EULA acceptance when Isaac Lab Training is enabled
    if (
        config.app.pipelines.useIsaacLabTraining.enabled &&
        !config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula
    ) {
        throw new Error(
            "Configuration Error: Isaac Lab Training requires accepting the NVIDIA EULA. " +
                "Please review the NVIDIA Software License Agreement at " +
                "https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license " +
                "and set 'useIsaacLabTraining.acceptNvidiaEula' to true in your config.json."
        );
    }

    if (config.app.pipelines.usePreview3dThumbnail == undefined) {
        config.app.pipelines.usePreview3dThumbnail = {
            enabled: false,
            autoRegisterWithVAMS: false,
            autoRegisterAutoTriggerOnFileUpload: false,
        };
    }
    if (config.app.pipelines.usePreview3dThumbnail.enabled == undefined) {
        config.app.pipelines.usePreview3dThumbnail.enabled = false;
    }

    // Cosmos Predict defaults
    if (config.app.pipelines.useNvidiaCosmos == undefined) {
        config.app.pipelines.useNvidiaCosmos = {
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
            modelsTransfer: {
                transfer2B: {
                    enabled: false,
                    autoRegisterWithVAMS: true,
                    autoTriggerOnFileExtensionsUpload: "",
                    instanceTypes: ["g6e.48xlarge", "p5.48xlarge"],
                    maxVCpus: 192,
                },
            },
        };
    }
    if (config.app.pipelines.useNvidiaCosmos.enabled == undefined) {
        config.app.pipelines.useNvidiaCosmos.enabled = false;
    }

    // Gr00t Fine-Tuning defaults
    if (config.app.pipelines.useNvidiaGr00t == undefined) {
        config.app.pipelines.useNvidiaGr00t = {
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
    }
    if (config.app.pipelines.useNvidiaGr00t.enabled == undefined) {
        config.app.pipelines.useNvidiaGr00t.enabled = false;
    }

    if (config.app.addons.useGarnetFramework == undefined) {
        config.app.addons.useGarnetFramework = {
            enabled: false,
            garnetApiEndpoint: "",
            garnetApiToken: "",
            garnetIngestionQueueSqsUrl: "",
        };
    }
    if (config.app.addons.useGarnetFramework.enabled == undefined) {
        config.app.addons.useGarnetFramework.enabled = false;
    }

    if (config.app.addons.usePhysnaSync == undefined) {
        config.app.addons.usePhysnaSync = {
            enabled: false,
            tenantId: "",
            apiBaseEndpoint: "https://app-api.physna.com/v3/",
            authTokenEndpoint: "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token",
            authType: "cognito",
            clientId: "",
            clientSecret: "",
        };
    }
    if (config.app.addons.usePhysnaSync.enabled == undefined) {
        config.app.addons.usePhysnaSync.enabled = false;
    }

    if (config.app.authProvider.useCognito.useUserPasswordAuthFlow == undefined) {
        config.app.authProvider.useCognito.useUserPasswordAuthFlow = false;
    }

    if (config.app.pipelines.useConversion3dBasic.enabled == undefined) {
        config.app.pipelines.useConversion3dBasic.enabled = true;
    }

    if (config.app.pipelines.useConversionCadMeshMetadataExtraction.enabled == undefined) {
        config.app.pipelines.useConversionCadMeshMetadataExtraction.enabled = false;
    }

    if (config.app.pipelines.useConversionCoordinateTransform == undefined) {
        config.app.pipelines.useConversionCoordinateTransform = {
            enabled: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: false,
            autoRegisterAutoTriggerOnFileUpload: false,
        };
    }

    if (config.app.authProvider.useExternalOAuthIdp.enabled == undefined) {
        config.app.authProvider.useExternalOAuthIdp.enabled = false;
    }

    if (config.app.addStackCloudTrailLogs == undefined) {
        config.app.addStackCloudTrailLogs = true;
    }

    if (config.app.useAlb.addAlbS3SpecialVpcEndpoint == undefined) {
        config.app.useAlb.addAlbS3SpecialVpcEndpoint = true;
    }

    if (config.app.assetBuckets.createNewBucket == undefined) {
        config.app.assetBuckets.createNewBucket = true;
    }

    // Null/omitted restriction lists mean no restrictions (no bucket policy statement).
    if (config.app.assetBuckets.presignedUrlNetworkRestrictions == undefined) {
        config.app.assetBuckets.presignedUrlNetworkRestrictions = {
            allowedIpRanges: [],
            allowedVpceIds: [],
        };
    }
    if (config.app.assetBuckets.presignedUrlNetworkRestrictions.allowedIpRanges == undefined) {
        config.app.assetBuckets.presignedUrlNetworkRestrictions.allowedIpRanges = [];
    }
    if (config.app.assetBuckets.presignedUrlNetworkRestrictions.allowedVpceIds == undefined) {
        config.app.assetBuckets.presignedUrlNetworkRestrictions.allowedVpceIds = [];
    }

    if (config.app.webUi.allowUnsafeEvalFeatures == undefined) {
        config.app.webUi.allowUnsafeEvalFeatures = false;
    }

    // Initialize authorizerOptions if undefined
    if (config.app.authProvider.authorizerOptions == undefined) {
        config.app.authProvider.authorizerOptions = {
            allowedIpRanges: [],
        };
    }

    // Initialize allowedIpRanges if undefined
    if (config.app.authProvider.authorizerOptions.allowedIpRanges == undefined) {
        config.app.authProvider.authorizerOptions.allowedIpRanges = [];
    }

    if (config.app.api == undefined) {
        config.app.api = {
            apiType: API_TYPE_APIGATEWAY_REST,
            apiGatewayRest: {
                globalRateLimit: 50,
                globalBurstLimit: 100,
                endpointType: "REGIONAL",
                optionalExternalPrivateApigVPCEId: "",
            },
        };
    }

    if (config.app.api.apiType == undefined || config.app.api.apiType === "") {
        config.app.api.apiType = API_TYPE_APIGATEWAY_REST;
    }
    if (config.app.api.apiGatewayRest == undefined) {
        // Carry over any values from the legacy flat `app.api` layout (globalRateLimit /
        // globalBurstLimit / endpointType lived directly under `api` before the
        // apiGatewayRest sub-block) so an in-place upgrade does not silently drop operator
        // settings; fall back to the defaults when a field is absent.
        const legacyApi = config.app.api as any;
        config.app.api.apiGatewayRest = {
            globalRateLimit: legacyApi.globalRateLimit ?? 50,
            globalBurstLimit: legacyApi.globalBurstLimit ?? 100,
            endpointType: legacyApi.endpointType ?? "REGIONAL",
            optionalExternalPrivateApigVPCEId: legacyApi.externalRegionalAPIGatewayVPCEId ?? "",
        };
    }

    if (config.app.api.apiGatewayRest.globalRateLimit == undefined) {
        config.app.api.apiGatewayRest.globalRateLimit = 50;
    }

    if (config.app.api.apiGatewayRest.globalBurstLimit == undefined) {
        config.app.api.apiGatewayRest.globalBurstLimit = 100;
    }

    if (config.app.api.apiGatewayRest.endpointType == undefined) {
        config.app.api.apiGatewayRest.endpointType = "REGIONAL";
    }
    if (config.app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId == undefined) {
        config.app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId = "";
    }

    // Initialize CloudFront custom domain configuration if undefined (backward compatibility)
    if (config.app.useCloudFront == undefined) {
        config.app.useCloudFront = {
            enabled: true,
            customDomain: {
                enabled: false,
                domainHost: "",
                certificateArn: "",
                optionalHostedZoneId: "",
            },
        };
    }

    if (config.app.useCloudFront.customDomain == undefined) {
        config.app.useCloudFront.customDomain = {
            enabled: false,
            domainHost: "",
            certificateArn: "",
            optionalHostedZoneId: "",
        };
    }

    // Initialize IAM role customization configuration if undefined (backward compatibility)
    if (config.app.iamRoleConfig == undefined) {
        config.app.iamRoleConfig = {
            useCustomBootstrapRoles: false,
            useCustomVamsStackRoles: false,
        };
    }
    if (config.app.iamRoleConfig.useCustomBootstrapRoles == undefined) {
        config.app.iamRoleConfig.useCustomBootstrapRoles = false;
    }
    if (config.app.iamRoleConfig.useCustomVamsStackRoles == undefined) {
        config.app.iamRoleConfig.useCustomVamsStackRoles = false;
    }

    // Initialize metadataSchema configuration if undefined (backward compatibility)
    if (config.app.metadataSchema == undefined) {
        config.app.metadataSchema = {
            autoLoadDefaultAssetLinksSchema: true,
            autoLoadDefaultDatabaseSchema: true,
            autoLoadDefaultAssetSchema: true,
            autoLoadDefaultAssetFileSchema: true,
        };
    }

    //Load S3 Policy statements JSON
    const s3AdditionalBucketPolicyFile: string = readFileSync(
        join(__dirname, "policy", "s3AdditionalBucketPolicyConfig.json"),
        {
            encoding: "utf8",
            flag: "r",
        }
    );

    if (s3AdditionalBucketPolicyFile && s3AdditionalBucketPolicyFile.length > 0) {
        config.s3AdditionalBucketPolicyJSON = JSON.parse(s3AdditionalBucketPolicyFile);
    } else {
        config.s3AdditionalBucketPolicyJSON = undefined;
    }

    //Load IAM role customization mappings JSON (only when a custom-roles flag is enabled)
    config.iamRoleCustomizationJSON = undefined;
    if (
        config.app.iamRoleConfig.useCustomBootstrapRoles ||
        config.app.iamRoleConfig.useCustomVamsStackRoles
    ) {
        const iamRoleConfigFile: string = readFileSync(
            join(__dirname, "policy", "iamRoleConfig.json"),
            {
                encoding: "utf8",
                flag: "r",
            }
        );

        if (iamRoleConfigFile && iamRoleConfigFile.trim().length > 0) {
            config.iamRoleCustomizationJSON = JSON.parse(iamRoleConfigFile);
        }

        if (!config.iamRoleCustomizationJSON) {
            throw new Error(
                "Configuration Error: app.iamRoleConfig enables custom IAM roles but " +
                    "infra/config/policy/iamRoleConfig.json is empty. Define the bootstrap and/or " +
                    "vamsStacks mappings in that file. See the configuration reference documentation."
            );
        }

        if (
            config.app.iamRoleConfig.useCustomBootstrapRoles &&
            !config.iamRoleCustomizationJSON.bootstrap
        ) {
            throw new Error(
                "Configuration Error: app.iamRoleConfig.useCustomBootstrapRoles is true but " +
                    "infra/config/policy/iamRoleConfig.json has no 'bootstrap' section."
            );
        }

        if (
            config.app.iamRoleConfig.useCustomVamsStackRoles &&
            !config.iamRoleCustomizationJSON.vamsStacks
        ) {
            throw new Error(
                "Configuration Error: app.iamRoleConfig.useCustomVamsStackRoles is true but " +
                    "infra/config/policy/iamRoleConfig.json has no 'vamsStacks' section."
            );
        }
    }

    //If we are govCloud, check for certain features that are required to be on or off.
    //Note: FIP not required for use in GovCloud. Some GovCloud endpoints are natively FIPS compliant regardless of this flag to use specific FIPS endpoints.
    //Note: FedRAMP best practices require all Lambdas/OpenSearch behind VPC but not required for GovCloud
    if (config.app.govCloud.enabled) {
        if (!config.app.useGlobalVpc.enabled) {
            throw new Error(
                "Configuration Error: GovCloud must have useGlobalVpc.enabled set to true"
            );
        }

        if (config.app.useCloudFront.enabled) {
            throw new Error(
                "Configuration Error: GovCloud does not support Cloudfront deployments, use the ALB configuration if a VAMS front-end website deployment is desired. "
            );
        }

        if (config.app.useLocationService.enabled) {
            throw new Error(
                "Configuration Error: GovCloud must have app.useLocationService.enabled set to false"
            );
        }

        //Now check additional IL6 compliance
        // https://aws.amazon.com/compliance/services-in-scope/DoD_CC_SRG/
        if (config.app.govCloud.il6Compliant) {
            if (config.app.authProvider.useCognito.enabled) {
                throw new Error(
                    "Configuration Error: GovCloud IL6 must have app.authProvider.useCognito.enabled set to false"
                );
            }

            if (config.app.useWaf) {
                throw new Error(
                    "Configuration Error: GovCloud IL6 must have config.app.useWaf set to false"
                );
            }

            if (!config.app.useKmsCmkEncryption.enabled) {
                throw new Error(
                    "Configuration Error: GovCloud IL6 must have config.app.useKmsCmkEncryption.enabled set to true"
                );
            }
        }
    }

    //Features that require a VPC. If any are enabled, useGlobalVpc.enabled must be true — we do not
    //auto-enable the VPC, because silently turning it on hides a significant deployment-topology
    //change from the operator. Collect the offending features and fail with an explicit error.
    const vpcRequiringFeatures: string[] = [];
    if (config.app.useAlb.enabled) vpcRequiringFeatures.push("useAlb");
    if (config.app.openSearch.useProvisioned.enabled)
        vpcRequiringFeatures.push("openSearch.useProvisioned");
    if (
        config.app.openSearch.useServerless.enabled &&
        !config.app.openSearch.useServerless.allowPublic
    )
        vpcRequiringFeatures.push("openSearch.useServerless (allowPublic=false)");
    if (config.app.pipelines.usePreviewPcPotreeViewer.enabled)
        vpcRequiringFeatures.push("pipelines.usePreviewPcPotreeViewer");
    if (config.app.pipelines.useSplatToolbox.enabled)
        vpcRequiringFeatures.push("pipelines.useSplatToolbox");
    if (config.app.pipelines.useGenAiMetadata3dLabeling.enabled)
        vpcRequiringFeatures.push("pipelines.useGenAiMetadata3dLabeling");
    if (config.app.pipelines.useRapidPipeline.useEcs.enabled)
        vpcRequiringFeatures.push("pipelines.useRapidPipeline.useEcs");
    if (config.app.pipelines.useRapidPipeline.useEks.enabled)
        vpcRequiringFeatures.push("pipelines.useRapidPipeline.useEks");
    if (config.app.pipelines.useModelOps.enabled)
        vpcRequiringFeatures.push("pipelines.useModelOps");
    if (config.app.pipelines.useIsaacLabTraining.enabled)
        vpcRequiringFeatures.push("pipelines.useIsaacLabTraining");
    if (config.app.pipelines.usePreview3dThumbnail.enabled)
        vpcRequiringFeatures.push("pipelines.usePreview3dThumbnail");
    if (config.app.pipelines.useNvidiaCosmos.enabled)
        vpcRequiringFeatures.push("pipelines.useNvidiaCosmos");
    if (config.app.pipelines.useNvidiaGr00t.enabled)
        vpcRequiringFeatures.push("pipelines.useNvidiaGr00t");
    if (config.app.pipelines.useConversionCoordinateTransform.enabled)
        vpcRequiringFeatures.push("pipelines.useConversionCoordinateTransform");

    if (vpcRequiringFeatures.length > 0 && !config.app.useGlobalVpc.enabled) {
        throw new Error(
            "Configuration Error: app.useGlobalVpc.enabled must be true because the following " +
                "enabled feature(s) require a VPC: " +
                vpcRequiringFeatures.join(", ") +
                ". Set app.useGlobalVpc.enabled to true, or disable these features."
        );
    }

    // Cosmos Predict/Transfer validation
    if (config.app.pipelines.useNvidiaCosmos.enabled) {
        const cosmosModels = config.app.pipelines.useNvidiaCosmos.modelsPredict;
        const cosmosTransferModels = config.app.pipelines.useNvidiaCosmos.modelsTransfer;
        const cosmosReasonModels = config.app.pipelines.useNvidiaCosmos.modelsReason;
        const anyModelEnabled =
            cosmosModels.text2world2B_v2.enabled ||
            cosmosModels.video2world2B_v2.enabled ||
            cosmosModels.text2world14B_v2.enabled ||
            cosmosModels.video2world14B_v2.enabled ||
            (cosmosTransferModels?.transfer2B?.enabled ?? false) ||
            (cosmosReasonModels?.reason2B?.enabled ?? false) ||
            (cosmosReasonModels?.reason8B?.enabled ?? false);

        if (!anyModelEnabled) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos is enabled but no model types are enabled. " +
                    "Enable at least one model in useNvidiaCosmos.modelsPredict, modelsTransfer, or modelsReason."
            );
        }

        if (
            !config.app.pipelines.useNvidiaCosmos.huggingFaceToken ||
            config.app.pipelines.useNvidiaCosmos.huggingFaceToken.trim() === ""
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos requires huggingFaceToken " +
                    "(SSM SecureString parameter path, e.g., '/vams/cosmos/hf-token') for model downloads."
            );
        }

        if (
            cosmosModels.text2world2B_v2.enabled &&
            (!cosmosModels.text2world2B_v2.instanceTypes ||
                cosmosModels.text2world2B_v2.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsPredict.text2world2B_v2.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosModels.video2world2B_v2.enabled &&
            (!cosmosModels.video2world2B_v2.instanceTypes ||
                cosmosModels.video2world2B_v2.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsPredict.video2world2B_v2.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosModels.text2world14B_v2.enabled &&
            (!cosmosModels.text2world14B_v2.instanceTypes ||
                cosmosModels.text2world14B_v2.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsPredict.text2world14B_v2.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosModels.video2world14B_v2.enabled &&
            (!cosmosModels.video2world14B_v2.instanceTypes ||
                cosmosModels.video2world14B_v2.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsPredict.video2world14B_v2.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosTransferModels?.transfer2B?.enabled &&
            (!cosmosTransferModels.transfer2B.instanceTypes ||
                cosmosTransferModels.transfer2B.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsTransfer.transfer2B.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosReasonModels?.reason2B?.enabled &&
            (!cosmosReasonModels.reason2B.instanceTypes ||
                cosmosReasonModels.reason2B.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsReason.reason2B.instanceTypes must be a non-empty array."
            );
        }

        if (
            cosmosReasonModels?.reason8B?.enabled &&
            (!cosmosReasonModels.reason8B.instanceTypes ||
                cosmosReasonModels.reason8B.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaCosmos.modelsReason.reason8B.instanceTypes must be a non-empty array."
            );
        }
    }

    // Gr00t Fine-Tuning validation
    if (config.app.pipelines.useNvidiaGr00t.enabled) {
        const gr00tModels = config.app.pipelines.useNvidiaGr00t.modelsFinetune;
        const anyGr00tModelEnabled = gr00tModels.gr00tN1_5_3B.enabled;

        if (!anyGr00tModelEnabled) {
            throw new Error(
                "Configuration Error: useNvidiaGr00t is enabled but no model types are enabled. " +
                    "Enable at least one model in useNvidiaGr00t.modelsFinetune."
            );
        }

        if (
            !config.app.pipelines.useNvidiaGr00t.huggingFaceToken ||
            config.app.pipelines.useNvidiaGr00t.huggingFaceToken.trim() === ""
        ) {
            throw new Error(
                "Configuration Error: useNvidiaGr00t requires huggingFaceToken " +
                    "for model downloads from HuggingFace."
            );
        }

        if (
            gr00tModels.gr00tN1_5_3B.enabled &&
            (!gr00tModels.gr00tN1_5_3B.instanceTypes ||
                gr00tModels.gr00tN1_5_3B.instanceTypes.length === 0)
        ) {
            throw new Error(
                "Configuration Error: useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes must be a non-empty array."
            );
        }
    }

    //Any configuration warnings/errors checks
    if (
        config.app.assetBuckets.createNewBucket &&
        (!config.app.assetBuckets.defaultNewBucketSyncDatabaseId ||
            config.app.assetBuckets.defaultNewBucketSyncDatabaseId == "" ||
            config.app.assetBuckets.defaultNewBucketSyncDatabaseId == "UNDEFINED")
    ) {
        throw new Error(
            "Configuration Error: Must define a app.assetBuckets.defaultNewBucketSyncDatabaseId if app.assetBuckets.createNewBucke is true"
        );
    }

    //If we aren't creating a new bucket and aren't adding any external asset buckets throw an error
    if (!config.app.assetBuckets.createNewBucket && !config.app.assetBuckets.externalAssetBuckets) {
        throw new Error(
            "Configuration Error: Must define at least a new asset bucket and/or app.assetBuckets.externalAssetBuckets"
        );
    }

    //Validate external asset bucket entries
    if (
        config.app.assetBuckets.externalAssetBuckets &&
        config.app.assetBuckets.externalAssetBuckets.length > 0
    ) {
        validateExternalAssetBuckets(
            config.app.assetBuckets.externalAssetBuckets,
            config.env.partition,
            config.env.account
        );
    }

    //Validate presigned URL network restriction configuration
    validatePresignedUrlRestrictions(
        config.app.assetBuckets.presignedUrlNetworkRestrictions,
        "app.assetBuckets.presignedUrlNetworkRestrictions"
    );

    if (
        config.app.useGlobalVpc.enabled &&
        config.app.useGlobalVpc.optionalExternalVpcId &&
        config.app.useGlobalVpc.optionalExternalVpcId != "UNDEFINED" &&
        config.app.useGlobalVpc.optionalExternalVpcId != "" &&
        !config.env.loadContextIgnoreVPCStacks
    ) {
        console.warn(
            "Configuration Notice: You have elected to import external VPCs/Subnets. If experiencing VPC/Subnet lookup errors, synethize your CDK first with the 'loadContextIgnoreVPCStacks' flag first."
        );
    }

    if (config.app.useGlobalVpc.enabled && !config.app.useGlobalVpc.addVpcEndpoints) {
        console.warn(
            "Configuration Warning: This configuration has disabled Add VPC Endpoints. Please manually ensure the VPC used has all nessesary VPC Interface Endpoints to ensure proper VAMS operations."
        );
    }

    if (config.app.useAlb.enabled && config.app.useAlb.usePublicSubnet) {
        console.warn(
            "Configuration Warning: YOU HAVE ENABLED ALB PUBLIC SUBNETS. THIS CAN EXPOSE YOUR STATIC WEBSITE SOLUTION TO THE PUBLIC INTERNET. PLEASE VERIFY THIS IS CORRECT."
        );
    }

    if (!config.app.useWaf) {
        console.warn(
            "Configuration Warning: YOU HAVE DISABLED USING WEB APPLICATION FIREWALL (WAF). ENSURE YOU HAVE OTHER FIREWALL MEASURES IN PLACE TO PREVENT ILLICIT NETWORK ACCESS. PLEASE VERIFY THIS IS CORRECT."
        );
    }

    if (
        config.app.useGlobalVpc.enabled &&
        (!config.app.useGlobalVpc.vpcCidrRange ||
            config.app.useGlobalVpc.vpcCidrRange == "UNDEFINED" ||
            config.app.useGlobalVpc.vpcCidrRange == "") &&
        (!config.app.useGlobalVpc.optionalExternalVpcId ||
            config.app.useGlobalVpc.optionalExternalVpcId == "UNDEFINED" ||
            config.app.useGlobalVpc.optionalExternalVpcId == "")
    ) {
        throw new Error(
            "Configuration Error: Must define either a global VPC Cidr Range or an External VPC ID."
        );
    }

    if (
        config.app.useGlobalVpc.enabled &&
        config.app.useGlobalVpc.optionalExternalVpcId &&
        config.app.useGlobalVpc.optionalExternalVpcId != "UNDEFINED" &&
        config.app.useGlobalVpc.optionalExternalVpcId != ""
    ) {
        if (
            !config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds ||
            config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds == "UNDEFINED" ||
            config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds == ""
        ) {
            throw new Error(
                "Configuration Error: Must define at least one isolated subnet ID when using an External VPC ID."
            );
        }
    }

    //If using RapidPipeline or ModelOps, make sure Imported VPC has at least one private subnet included
    if (
        config.app.useGlobalVpc.enabled &&
        config.app.useGlobalVpc.optionalExternalVpcId &&
        config.app.useGlobalVpc.optionalExternalVpcId != "UNDEFINED" &&
        config.app.useGlobalVpc.optionalExternalVpcId != ""
    ) {
        if (
            config.app.pipelines.useRapidPipeline.useEcs.enabled ||
            config.app.pipelines.useRapidPipeline.useEks.enabled ||
            config.app.pipelines.useModelOps.enabled
        ) {
            if (
                !config.app.useGlobalVpc.optionalExternalPrivateSubnetIds ||
                config.app.useGlobalVpc.optionalExternalPrivateSubnetIds == "UNDEFINED" ||
                config.app.useGlobalVpc.optionalExternalPrivateSubnetIds == ""
            ) {
                throw new Error(
                    "Configuration Error: Must define at least one private subnet ID when using RapidPipeline."
                );
            }
        }
    }
    //Cloudfront + ALB check (not more than 1)
    if (!config.app.useCloudFront.enabled && !config.app.useAlb.enabled) {
        console.warn(
            "Configuration Warning: YOU HAVE DISABLED DEPLOYING ANY VAMS FRONT-END WITH CLOUDFRONT OR ALB. THIS WILL BE A API-DRIVEN SOLUTION-ONLY DEPLOYMENT."
        );
    }

    //Cloudfront + ALB neither warning check
    if (config.app.useCloudFront.enabled && config.app.useAlb.enabled) {
        throw new Error(
            "Configuration Error: Must choose either only Cloufront or ALB for static website deployment use (or neither), cannot have both enabled."
        );
    }

    // CloudFront Custom Domain Configuration Validation
    if (config.app.useCloudFront.customDomain.enabled) {
        if (
            !config.app.useCloudFront.customDomain.certificateArn ||
            config.app.useCloudFront.customDomain.certificateArn == "UNDEFINED" ||
            config.app.useCloudFront.customDomain.certificateArn == "" ||
            !config.app.useCloudFront.customDomain.domainHost ||
            config.app.useCloudFront.customDomain.domainHost == "UNDEFINED" ||
            config.app.useCloudFront.customDomain.domainHost == ""
        ) {
            throw new Error(
                "Configuration Error: Cannot use CloudFront custom domain without specifying a valid domain hostname and a ACM Certificate ARN to use for SSL/TLS security!"
            );
        }

        // Validate certificate ARN format
        const certArnPattern = /^arn:aws[a-z-]*:acm:us-east-1:\d{12}:certificate\/[a-f0-9-]+$/;
        if (!certArnPattern.test(config.app.useCloudFront.customDomain.certificateArn)) {
            throw new Error(
                "Configuration Warning: CloudFront custom domain certificate ARN should be in us-east-1 region. CloudFront requires certificates to be in us-east-1 regardless of deployment region. Provided ARN: " +
                    config.app.useCloudFront.customDomain.certificateArn
            );
        }
    }

    if (
        ((config.app.useAlb.enabled && config.app.useAlb.usePublicSubnet) ||
            config.app.pipelines.useRapidPipeline.useEcs.enabled ||
            config.app.pipelines.useRapidPipeline.useEks.enabled ||
            config.app.pipelines.useModelOps.enabled) &&
        config.app.useGlobalVpc.enabled &&
        config.app.useGlobalVpc.optionalExternalVpcId &&
        config.app.useGlobalVpc.optionalExternalVpcId != "UNDEFINED" &&
        config.app.useGlobalVpc.optionalExternalVpcId != ""
    ) {
        if (
            !config.app.useGlobalVpc.optionalExternalPublicSubnetIds ||
            config.app.useGlobalVpc.optionalExternalPublicSubnetIds == "UNDEFINED" ||
            config.app.useGlobalVpc.optionalExternalPublicSubnetIds == ""
        ) {
            throw new Error(
                "Configuration Error: Must define at least one public subnet ID when using an External VPC ID and Public ALB or RapidPipeline configuration."
            );
        }
    }

    if (
        config.app.useAlb.enabled &&
        (!config.app.useAlb.certificateArn ||
            config.app.useAlb.certificateArn == "UNDEFINED" ||
            config.app.useAlb.certificateArn == "" ||
            !config.app.useAlb.domainHost ||
            config.app.useAlb.domainHost == "UNDEFINED" ||
            config.app.useAlb.domainHost == "")
    ) {
        throw new Error(
            "Configuration Error: Cannot use ALB deployment without specifying a valid domain hostname and a ACM Certificate ARN to use for SSL/TLS security!"
        );
    }

    if (
        !config.app.adminEmailAddress ||
        config.app.adminEmailAddress == "" ||
        config.app.adminEmailAddress == "UNDEFINED"
    ) {
        throw new Error(
            "Configuration Error: Must specify an initial admin email address as part of this deployment configuration!"
        );
    }

    if (
        !config.app.adminUserId ||
        config.app.adminUserId == "" ||
        config.app.adminUserId == "UNDEFINED"
    ) {
        throw new Error(
            "Configuration Error: Must specify an initial admin user ID as part of this deployment configuration!"
        );
    }

    //Error check when implementing openSearch
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useProvisioned.enabled
    ) {
        throw new Error("Configuration Error: Must specify either none or one openSearch method!");
    }

    //Next-gen Serverless is not yet supported in GovCloud or the EU Sovereign Cloud. Block the combination
    //so the configuration fails fast rather than at deploy time.
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useServerless.nextGen &&
        config.app.govCloud.enabled
    ) {
        throw new Error(
            "Configuration Error: openSearch.useServerless.nextGen is not supported when app.govCloud.enabled " +
                "is true (GovCloud and EU Sovereign Cloud). Set openSearch.useServerless.nextGen to false for these partitions."
        );
    }

    //NEXTGEN collection groups require standby replicas — OpenSearch Serverless rejects a NEXTGEN group with
    //StandbyReplicas=DISABLED. Fail fast rather than at deploy time.
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useServerless.nextGen &&
        !config.app.openSearch.useServerless.enableStandbyReplicas
    ) {
        throw new Error(
            "Configuration Error: openSearch.useServerless.nextGen requires openSearch.useServerless.enableStandbyReplicas " +
                "to be true. NEXTGEN OpenSearch Serverless collection groups do not support disabled standby replicas."
        );
    }

    //Scale-to-zero (a minimum OCU of 0) is only available on next-gen Serverless. Classic Serverless cannot
    //scale indexing or search capacity down to 0.
    if (
        config.app.openSearch.useServerless.enabled &&
        !config.app.openSearch.useServerless.nextGen &&
        (config.app.openSearch.useServerless.minIndexingOcu === 0 ||
            config.app.openSearch.useServerless.minSearchOcu === 0)
    ) {
        throw new Error(
            "Configuration Error: a minimum OCU of 0 (scale-to-zero) requires next-gen Serverless. " +
                "Set openSearch.useServerless.nextGen to true, or set minIndexingOcu and minSearchOcu to 1 or greater."
        );
    }

    //OCU bounds must be non-negative integers, each maximum must be at least 1, and each maximum must be >= its minimum.
    {
        const ocuFields: { name: string; value: number }[] = [
            { name: "minIndexingOcu", value: config.app.openSearch.useServerless.minIndexingOcu },
            { name: "maxIndexingOcu", value: config.app.openSearch.useServerless.maxIndexingOcu },
            { name: "minSearchOcu", value: config.app.openSearch.useServerless.minSearchOcu },
            { name: "maxSearchOcu", value: config.app.openSearch.useServerless.maxSearchOcu },
        ];
        if (config.app.openSearch.useServerless.enabled) {
            for (const f of ocuFields) {
                if (!Number.isInteger(f.value) || f.value < 0) {
                    throw new Error(
                        `Configuration Error: openSearch.useServerless.${f.name} must be a non-negative integer.`
                    );
                }
                //OpenSearch Serverless only accepts specific OCU values: 0, 2, 4, 8, 16, or any multiple of 16.
                const isAllowedOcu =
                    f.value === 0 ||
                    f.value === 2 ||
                    f.value === 4 ||
                    f.value === 8 ||
                    (f.value >= 16 && f.value % 16 === 0);
                if (!isAllowedOcu) {
                    throw new Error(
                        `Configuration Error: openSearch.useServerless.${f.name} must be one of 0, 2, 4, 8, 16, or any multiple of 16.`
                    );
                }
            }
            if (config.app.openSearch.useServerless.maxIndexingOcu < 1) {
                throw new Error(
                    "Configuration Error: openSearch.useServerless.maxIndexingOcu must be 1 or greater."
                );
            }
            if (config.app.openSearch.useServerless.maxSearchOcu < 1) {
                throw new Error(
                    "Configuration Error: openSearch.useServerless.maxSearchOcu must be 1 or greater."
                );
            }
            if (
                config.app.openSearch.useServerless.maxIndexingOcu <
                config.app.openSearch.useServerless.minIndexingOcu
            ) {
                throw new Error(
                    "Configuration Error: openSearch.useServerless.maxIndexingOcu must be greater than or equal to minIndexingOcu."
                );
            }
            if (
                config.app.openSearch.useServerless.maxSearchOcu <
                config.app.openSearch.useServerless.minSearchOcu
            ) {
                throw new Error(
                    "Configuration Error: openSearch.useServerless.maxSearchOcu must be greater than or equal to minSearchOcu."
                );
            }
        }
    }

    //A private (non-public) Serverless collection is reachable only through a VPC endpoint, so it requires a
    //VPC. Only the OpenSearch-facing Lambdas (search and indexers) are placed in the VPC — useForAllLambdas is
    //not required. The vpcRequiringFeatures check above already fails when useGlobalVpc.enabled is false for a
    //private collection, mirroring the provisioned-OpenSearch behavior.

    //A deployment that places all Lambdas in the VPC (useGlobalVpc.enabled + useForAllLambdas) is fully
    //network-isolated, so a public Serverless collection is contradictory — the collection must be private.
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useServerless.allowPublic &&
        config.app.useGlobalVpc.enabled &&
        config.app.useGlobalVpc.useForAllLambdas
    ) {
        throw new Error(
            "Configuration Error: a deployment with app.useGlobalVpc.enabled and app.useGlobalVpc.useForAllLambdas " +
                "set to true (all Lambdas behind the VPC) cannot use a public OpenSearch Serverless collection. " +
                "Set openSearch.useServerless.allowPublic to false to place the collection behind a VPC endpoint."
        );
    }

    //Public Serverless in GovCloud/EU Sovereign Cloud is allowed but not recommended.
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useServerless.allowPublic &&
        config.app.govCloud.enabled
    ) {
        console.warn(
            "Configuration Warning: a public OpenSearch Serverless collection (openSearch.useServerless.allowPublic=true) " +
                "is not recommended for GovCloud or EU Sovereign Cloud deployments. Consider setting allowPublic to false."
        );
    }

    //A private NEXTGEN Serverless collection is reached through a standard EC2 interface VPC endpoint
    //(com.amazonaws.{region}.aoss-data). Like every other EC2 interface endpoint, VAMS only creates it when
    //useGlobalVpc.addVpcEndpoints is true. When it is false, VAMS skips both the endpoint and the collection's
    //VPC network access policy, and the operator must create them manually after deployment. Warn so this is
    //not a surprise. (CLASSIC uses the OpenSearch Serverless-managed endpoint, which is not governed by
    //addVpcEndpoints and is always created for a private collection.)
    if (
        config.app.openSearch.useServerless.enabled &&
        !config.app.openSearch.useServerless.allowPublic &&
        config.app.openSearch.useServerless.nextGen &&
        !config.app.useGlobalVpc.addVpcEndpoints
    ) {
        console.warn(
            "Configuration Warning: a private next-gen OpenSearch Serverless collection (allowPublic=false, nextGen=true) " +
                "with app.useGlobalVpc.addVpcEndpoints=false will deploy WITHOUT its data-plane VPC endpoint and network " +
                "access policy. VAMS writes the OpenSearch SSM parameters and skips index creation; you must create the " +
                "standard com.amazonaws.{region}.aoss-data interface endpoint and a matching network access policy " +
                "(with that endpoint id in SourceVPCEs) manually, then reindex. See the OpenSearch developer guide."
        );
    }

    //OpenSearch provisioned only supports a zone-aware domain spread across 2 or 3 Availability Zones.
    if (
        config.app.openSearch.useProvisioned.enabled &&
        config.app.openSearch.useProvisioned.availabilityZoneCount != 2 &&
        config.app.openSearch.useProvisioned.availabilityZoneCount != 3
    ) {
        throw new Error(
            "Configuration Error: openSearch.useProvisioned.availabilityZoneCount must be either 2 or 3."
        );
    }

    //OpenSearch provisioned shard count must be a positive integer.
    if (
        config.app.openSearch.useProvisioned.enabled &&
        (!Number.isInteger(config.app.openSearch.useProvisioned.numberOfShards) ||
            config.app.openSearch.useProvisioned.numberOfShards < 1)
    ) {
        throw new Error(
            "Configuration Error: openSearch.useProvisioned.numberOfShards must be an integer of 1 or greater."
        );
    }

    //The EU Sovereign Cloud (Germany) region eusc-de-east-1 currently exposes only 2 Availability Zones,
    //so a provisioned OpenSearch domain there cannot be spread across 3 AZs.
    if (
        config.app.openSearch.useProvisioned.enabled &&
        config.env.region == "eusc-de-east-1" &&
        config.app.openSearch.useProvisioned.availabilityZoneCount > 2
    ) {
        throw new Error(
            "Configuration Error: Region eusc-de-east-1 (EU Sovereign Cloud) only supports up to 2 Availability Zones. " +
                "Set openSearch.useProvisioned.availabilityZoneCount to 2 when deploying OpenSearch provisioned to this region."
        );
    }

    //Error check for reindexOnDeploy - requires OpenSearch to be enabled
    if (
        config.app.openSearch.reindexOnCdkDeploy &&
        !config.app.openSearch.useServerless.enabled &&
        !config.app.openSearch.useProvisioned.enabled
    ) {
        throw new Error(
            "Configuration Error: reindexOnDeploy requires either OpenSearch Serverless or Provisioned to be enabled!"
        );
    }

    //Check when implementing auth providers
    if (
        config.app.authProvider.useCognito.enabled &&
        config.app.authProvider.useExternalOAuthIdp.enabled
    ) {
        throw new Error("Configuration Error: Must specify only one authentication method!");
    }

    if (
        config.app.authProvider.useCognito.enabled &&
        config.app.authProvider.useCognito.useUserPasswordAuthFlow
    ) {
        console.warn(
            "Configuration Warning: UserPasswordAuth flow is enabled for Cognito which allows non-SRP authentication methods with username/passwords. This could be a security finding in some deployment environments!"
        );
    }

    if (
        config.app.authProvider.useExternalOAuthIdp.enabled &&
        (!config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl == "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl == "" ||
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl ==
                "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl == "" ||
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience ==
                "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthClientId == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthClientId == "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthPrincipalDomain == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthPrincipalDomain == "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScope == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScope == "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa == "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderTokenEndpoint == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderTokenEndpoint ==
                "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderAuthorizationEndpoint ==
                "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderAuthorizationEndpoint ==
                "UNDEFINED" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderDiscoveryEndpoint == "" ||
            config.app.authProvider.useExternalOAuthIdp.idpAuthProviderDiscoveryEndpoint ==
                "UNDEFINED")
    ) {
        throw new Error(
            "Configuration Error: Must specify a external IDP auth URL, external IDP principal domain, external IDP client ID, external IDP client secret, Lambda Authorizer JWT Issuer URL, Lambda Authorizer JWT Identity Source, and Lambda Authorizer JWT Audience when using an external OAUTH provider!"
        );
    }

    //API Configuration Error Checks

    // API type: only API Gateway REST is supported today.
    if (SUPPORTED_API_TYPES.indexOf(config.app.api.apiType) === -1) {
        throw new Error(
            `Configuration Error: app.api.apiType must be one of [${SUPPORTED_API_TYPES.join(
                ", "
            )}]. Got: '${config.app.api.apiType}'.`
        );
    }

    const apiGatewayRest = config.app.api.apiGatewayRest;

    if (apiGatewayRest.globalRateLimit <= 0) {
        throw new Error(
            "Configuration Error: API globalRateLimit must be a positive number greater than 0."
        );
    }

    if (apiGatewayRest.globalBurstLimit <= 0) {
        throw new Error(
            "Configuration Error: API globalBurstLimit must be a positive number greater than 0."
        );
    }

    if (apiGatewayRest.globalBurstLimit < apiGatewayRest.globalRateLimit) {
        throw new Error(
            "Configuration Error: API globalBurstLimit must be greater than or equal to globalRateLimit."
        );
    }

    if (apiGatewayRest.endpointType !== "REGIONAL" && apiGatewayRest.endpointType !== "PRIVATE") {
        throw new Error(
            "Configuration Error: app.api.apiGatewayRest.endpointType must be 'REGIONAL' or 'PRIVATE'."
        );
    }

    const externalPrivateVpceId = apiGatewayRest.optionalExternalPrivateApigVPCEId || "";

    if (apiGatewayRest.endpointType === "PRIVATE") {
        // PRIVATE requires a VPC and an execute-api VPC interface endpoint — either created
        // by VAMS (addVpcEndpoints) or supplied externally. It cannot be fronted by public
        // CloudFront, and must be fronted by an ALB that lives in non-public (isolated)
        // subnets (useAlb.usePublicSubnet = false).
        if (!config.app.useGlobalVpc.enabled) {
            throw new Error(
                "Configuration Error: app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useGlobalVpc.enabled = true."
            );
        }
        if (!config.app.useGlobalVpc.addVpcEndpoints && externalPrivateVpceId === "") {
            throw new Error(
                "Configuration Error: app.api.apiGatewayRest.endpointType 'PRIVATE' requires an execute-api " +
                    "interface VPC endpoint. Set app.useGlobalVpc.addVpcEndpoints = true to have VAMS create one, " +
                    "or provide app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId with an existing endpoint id."
            );
        }
        if (config.app.useCloudFront.enabled) {
            throw new Error(
                "Configuration Error: app.api.apiGatewayRest.endpointType 'PRIVATE' is incompatible with public CloudFront (app.useCloudFront.enabled). Use ALB/VPC fronting."
            );
        }
        // A PRIVATE API is reachable only from inside the VPC, so it must be fronted by the
        // ALB, and that ALB must sit in private (non-public) subnets. A public-subnet ALB
        // would expose an internet-facing path that forwards to the private API, defeating
        // the point of making the API private.
        if (!config.app.useAlb.enabled) {
            throw new Error(
                "Configuration Error: app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useAlb.enabled = true (a private API must be fronted by the ALB)."
            );
        }
        if (config.app.useAlb.usePublicSubnet) {
            throw new Error(
                "Configuration Error: app.api.apiGatewayRest.endpointType 'PRIVATE' requires app.useAlb.usePublicSubnet = false. A public-subnet ALB would expose an internet-facing path to the private API."
            );
        }
    } else {
        // REGIONAL is a public endpoint and does not use any execute-api VPC endpoint, even
        // when a VPC is enabled. An external private endpoint id is only meaningful for
        // PRIVATE, so warn if one is set here.
        if (externalPrivateVpceId !== "") {
            console.warn(
                "Configuration Warning: app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId is set but will not be used. " +
                    "It applies only to a PRIVATE endpoint. A REGIONAL endpoint is public and does not route through a VPC endpoint."
            );
        }
    }

    // Validate IP ranges configuration
    if (config.app.authProvider.authorizerOptions.allowedIpRanges) {
        for (let i = 0; i < config.app.authProvider.authorizerOptions.allowedIpRanges.length; i++) {
            const range = config.app.authProvider.authorizerOptions.allowedIpRanges[i];
            if (!Array.isArray(range) || range.length !== 2) {
                throw new Error(
                    `Configuration Error: IP range at index ${i} must be an array of exactly 2 IP addresses [min, max]. Got: ${JSON.stringify(
                        range
                    )}`
                );
            }

            // Basic IP format validation
            const ipRegex =
                /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
            if (!ipRegex.test(range[0]) || !ipRegex.test(range[1])) {
                throw new Error(
                    `Configuration Error: Invalid IP address format in range at index ${i}. Expected format: ["192.168.1.1", "192.168.1.255"]. Got: ${JSON.stringify(
                        range
                    )}`
                );
            }
        }
    }

    // Garnet Framework Configuration Validation
    if (config.app.addons.useGarnetFramework.enabled) {
        if (
            !config.app.addons.useGarnetFramework.garnetApiEndpoint ||
            config.app.addons.useGarnetFramework.garnetApiEndpoint === "UNDEFINED" ||
            config.app.addons.useGarnetFramework.garnetApiEndpoint === ""
        ) {
            throw new Error(
                "Configuration Error: Garnet Framework requires garnetApiEndpoint when enabled"
            );
        }

        if (
            !config.app.addons.useGarnetFramework.garnetApiToken ||
            config.app.addons.useGarnetFramework.garnetApiToken === "UNDEFINED" ||
            config.app.addons.useGarnetFramework.garnetApiToken === ""
        ) {
            throw new Error(
                "Configuration Error: Garnet Framework requires garnetApiToken when enabled"
            );
        }

        if (
            !config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl ||
            config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl === "UNDEFINED" ||
            config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl === ""
        ) {
            throw new Error(
                "Configuration Error: Garnet Framework requires garnetIngestionQueueSqsUrl when enabled"
            );
        }

        // Validate API endpoint URL format
        try {
            new URL(config.app.addons.useGarnetFramework.garnetApiEndpoint);
        } catch (e) {
            throw new Error(
                `Configuration Error: Garnet Framework garnetApiEndpoint must be a valid URL. Got: ${config.app.addons.useGarnetFramework.garnetApiEndpoint}`
            );
        }

        // Validate SQS URL format (basic validation)
        const sqsUrlPattern = /^https:\/\/sqs\.[a-z0-9-]+\.amazonaws\.com\/\d+\/[a-zA-Z0-9_-]+$/;
        if (!sqsUrlPattern.test(config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl)) {
            throw new Error(
                `Configuration Error: Garnet Framework garnetIngestionQueueSqsUrl must be a valid SQS URL. Expected format: https://sqs.region.amazonaws.com/account/queue-name. Got: ${config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl}`
            );
        }

        // Warn if OpenSearch is not enabled (Garnet works independently but this might be unintended)
        if (
            !config.app.openSearch.useServerless.enabled &&
            !config.app.openSearch.useProvisioned.enabled
        ) {
            console.warn(
                "Configuration Warning: Garnet Framework is enabled but OpenSearch is disabled. Garnet indexing will work independently of VAMS search functionality."
            );
        }
    }

    // Physna Sync Configuration Validation
    if (config.app.addons.usePhysnaSync.enabled) {
        const physna = config.app.addons.usePhysnaSync;

        // tenantId must be a UUID
        const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        if (!physna.tenantId || !uuidPattern.test(physna.tenantId)) {
            throw new Error(
                `Configuration Error: Physna Sync requires tenantId to be a valid UUID when enabled. Got: ${physna.tenantId}`
            );
        }

        // apiBaseEndpoint required, must be a valid URL ending with /
        if (
            !physna.apiBaseEndpoint ||
            physna.apiBaseEndpoint === "UNDEFINED" ||
            physna.apiBaseEndpoint === ""
        ) {
            throw new Error(
                "Configuration Error: Physna Sync requires apiBaseEndpoint when enabled"
            );
        }
        try {
            new URL(physna.apiBaseEndpoint);
        } catch (e) {
            throw new Error(
                `Configuration Error: Physna Sync apiBaseEndpoint must be a valid URL. Got: ${physna.apiBaseEndpoint}`
            );
        }
        if (!physna.apiBaseEndpoint.endsWith("/")) {
            throw new Error(
                `Configuration Error: Physna Sync apiBaseEndpoint must end with a trailing slash '/'. Got: ${physna.apiBaseEndpoint}`
            );
        }

        // authTokenEndpoint required, must be a valid URL
        if (
            !physna.authTokenEndpoint ||
            physna.authTokenEndpoint === "UNDEFINED" ||
            physna.authTokenEndpoint === ""
        ) {
            throw new Error(
                "Configuration Error: Physna Sync requires authTokenEndpoint when enabled"
            );
        }
        try {
            new URL(physna.authTokenEndpoint);
        } catch (e) {
            throw new Error(
                `Configuration Error: Physna Sync authTokenEndpoint must be a valid URL. Got: ${physna.authTokenEndpoint}`
            );
        }

        // authType must be "cognito" (only supported mode phase 1)
        if (physna.authType !== "cognito") {
            throw new Error(
                `Configuration Error: Physna Sync authType must be "cognito" (only supported value in phase 1). Got: ${physna.authType}`
            );
        }

        // clientId and clientSecret must be non-empty
        if (!physna.clientId || physna.clientId === "UNDEFINED" || physna.clientId === "") {
            throw new Error("Configuration Error: Physna Sync requires clientId when enabled");
        }
        if (
            !physna.clientSecret ||
            physna.clientSecret === "UNDEFINED" ||
            physna.clientSecret === ""
        ) {
            throw new Error("Configuration Error: Physna Sync requires clientSecret when enabled");
        }
    }

    return config;
}

/**
 * Validates a presigned URL network restriction block: each allowedIpRanges entry
 * must be an IPv4 or IPv6 CIDR and each allowedVpceIds entry a VPC endpoint ID
 * (interface or gateway). Throws a Configuration Error on any violation. The
 * context string identifies which bucket entry the block belongs to in error
 * messages. Exported for unit testing.
 */
export function validatePresignedUrlRestrictions(
    restrictions: ConfigPresignedUrlNetworkRestrictions | undefined,
    context: string
): void {
    if (!restrictions) {
        return;
    }

    // IP-range and VPC-endpoint restrictions are mutually exclusive: a request
    // arrives either over the public path (aws:SourceIp) or through a VPC endpoint
    // (aws:SourceVpce), so restrict on one dimension per deployment.
    if (
        (restrictions.allowedIpRanges || []).length > 0 &&
        (restrictions.allowedVpceIds || []).length > 0
    ) {
        throw new Error(
            `Configuration Error: ${context} cannot set both allowedIpRanges and allowedVpceIds. Restrict presigned URLs by IP range or by VPC endpoint, not both.`
        );
    }

    const ipv4CidrRegex = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
    const ipv6CidrRegex = /^([0-9a-fA-F:]+)\/(\d{1,3})$/;

    for (const range of restrictions.allowedIpRanges || []) {
        const v4 = range.match(ipv4CidrRegex);
        if (v4) {
            const octetsValid = v4.slice(1, 5).every((o) => parseInt(o) <= 255);
            if (!octetsValid || parseInt(v4[5]) > 32) {
                throw new Error(
                    `Configuration Error: ${context} allowedIpRanges entry '${range}' is not a valid IPv4 CIDR.`
                );
            }
            continue;
        }
        const v6 = range.match(ipv6CidrRegex);
        if (v6 && range.includes(":") && parseInt(v6[2]) <= 128) {
            continue;
        }
        throw new Error(
            `Configuration Error: ${context} allowedIpRanges entry '${range}' is not a valid IPv4 or IPv6 CIDR (address/prefixLength).`
        );
    }

    for (const vpceId of restrictions.allowedVpceIds || []) {
        if (!/^vpce-[0-9a-f]{8,}$/.test(vpceId)) {
            throw new Error(
                `Configuration Error: ${context} allowedVpceIds entry '${vpceId}' is not a valid VPC endpoint ID (vpce-...).`
            );
        }
    }
}

/**
 * Validates the externalAssetBuckets configuration. A single bucket ARN may be
 * registered under multiple prefixes, but the prefixes must not overlap (S3 permits
 * only one notification configuration per bucket and cannot route an object to an
 * ambiguous prefix), and the per-bucket attributes (account, region, KMS key) must
 * be consistent across every entry for that ARN. Throws a Configuration Error on any
 * violation. Exported for unit testing.
 */
export function validateExternalAssetBuckets(
    externalAssetBuckets: ConfigPublicAssetS3Buckets[],
    deploymentPartition: string,
    deploymentAccount?: string
): void {
    // Normalizes a baseAssetsPrefix to a comparable form. "", "/", and undefined all
    // represent the bucket root (matches everything); any other value is returned
    // with a guaranteed single trailing slash.
    const normalizePrefix = (prefix: string | undefined): string => {
        if (!prefix || prefix == "" || prefix == "/") {
            return "/";
        }
        return prefix.endsWith("/") ? prefix : prefix + "/";
    };

    // Two prefixes "overlap" when one is a path-prefix of the other (so S3 cannot
    // unambiguously route an object to a single prefix-filtered notification). The
    // root "/" overlaps every prefix.
    const prefixesOverlap = (a: string, b: string): boolean => {
        if (a == "/" || b == "/") {
            return true;
        }
        return a == b || a.startsWith(b) || b.startsWith(a);
    };

    // Per-ARN accumulator: tracks the prefixes already registered and the
    // account/region/KMS attributes, which must be consistent across all entries for
    // the same bucket (they describe one physical bucket).
    interface SeenBucket {
        prefixes: string[];
        accountId?: string;
        region?: string;
        kmsKeyArn?: string;
    }
    const seenBuckets = new Map<string, SeenBucket>();

    const normalizeOptional = (value: string | undefined): string | undefined =>
        value && value != "" && value != "UNDEFINED" ? value : undefined;

    for (const bucketConfig of externalAssetBuckets) {
        // The external bucket ARN must use the same partition as the deployment.
        const arnPartition = bucketConfig.bucketArn.split(":")[1];
        if (arnPartition && arnPartition != deploymentPartition) {
            throw new Error(
                `Configuration Error: external bucket ARN ${bucketConfig.bucketArn} uses partition '${arnPartition}' which does not match the deployment partition '${deploymentPartition}'.`
            );
        }

        const accountId = normalizeOptional(bucketConfig.bucketAccountId);
        const region = normalizeOptional(bucketConfig.bucketRegion);
        const kmsKeyArn = normalizeOptional(bucketConfig.bucketKmsKeyArn);

        // bucketAccountId, when provided, must be a 12-digit AWS account ID.
        if (accountId) {
            if (!/^\d{12}$/.test(accountId)) {
                throw new Error(
                    `Configuration Error: external bucket ${bucketConfig.bucketArn} bucketAccountId must be a 12-digit AWS account ID.`
                );
            }
            if (deploymentAccount && accountId == deploymentAccount) {
                console.warn(
                    `Configuration Warning: external bucket ${bucketConfig.bucketArn} bucketAccountId matches the deployment account; the bucket is not actually cross-account.`
                );
            }
        }

        const prefix = normalizePrefix(bucketConfig.baseAssetsPrefix);
        const existing = seenBuckets.get(bucketConfig.bucketArn);

        if (!existing) {
            // First registration for this bucket ARN.
            seenBuckets.set(bucketConfig.bucketArn, {
                prefixes: [prefix],
                accountId,
                region,
                kmsKeyArn,
            });
            continue;
        }

        // The same bucket may be registered under multiple prefixes, but each physical
        // bucket has one set of attributes — they must match across every entry for
        // that ARN.
        if (existing.accountId != accountId) {
            throw new Error(
                `Configuration Error: external bucket ${bucketConfig.bucketArn} is registered with inconsistent bucketAccountId values across entries.`
            );
        }
        if (existing.region != region) {
            throw new Error(
                `Configuration Error: external bucket ${bucketConfig.bucketArn} is registered with inconsistent bucketRegion values across entries.`
            );
        }
        if (existing.kmsKeyArn != kmsKeyArn) {
            throw new Error(
                `Configuration Error: external bucket ${bucketConfig.bucketArn} is registered with inconsistent bucketKmsKeyArn values across entries.`
            );
        }

        // Prefixes registered for the same bucket must not overlap, otherwise S3
        // cannot route an object-created event to a single prefix-filtered topic.
        for (const otherPrefix of existing.prefixes) {
            if (prefixesOverlap(prefix, otherPrefix)) {
                const display = (value: string) => (value == "/" ? "/ (bucket root)" : value);
                throw new Error(
                    `Configuration Error: external bucket ${
                        bucketConfig.bucketArn
                    } has overlapping baseAssetsPrefix values '${display(prefix)}' and '${display(
                        otherPrefix
                    )}'. Prefixes registered for the same bucket must not overlap.`
                );
            }
        }
        existing.prefixes.push(prefix);
    }
}

export interface ConfigPresignedUrlNetworkRestrictions {
    allowedIpRanges: string[];
    allowedVpceIds: string[];
}

export interface ConfigPublicAssetS3Buckets {
    bucketArn: string;
    baseAssetsPrefix: string;
    defaultSyncDatabaseId: string;
    // Optional cross-account / encryption fields. Required for buckets that live
    // in a different account (bucketAccountId) or use a customer managed KMS key
    // (bucketKmsKeyArn). bucketRegion defaults to the deployment region.
    bucketAccountId?: string;
    bucketRegion?: string;
    bucketKmsKeyArn?: string;
}

//Public config values that should go into a configuration file
export interface ConfigPublic {
    name: string;
    env: {
        account: string;
        region: string;
        partition: string;
        coreStackName: string; //Will get overwritten always when generated
        loadContextIgnoreVPCStacks: boolean;
    };
    //removalPolicy: RemovalPolicy;
    //autoDelete: boolean;
    app: {
        baseStackName: string;
        assetBuckets: {
            createNewBucket: boolean;
            defaultNewBucketSyncDatabaseId: string;
            presignedUrlNetworkRestrictions: ConfigPresignedUrlNetworkRestrictions;
            externalAssetBuckets: [ConfigPublicAssetS3Buckets];
        };
        adminUserId: string;
        adminEmailAddress: string;
        iamRoleConfig: {
            useCustomBootstrapRoles: boolean;
            useCustomVamsStackRoles: boolean;
        };
        useFips: boolean;
        useWaf: boolean;
        addStackCloudTrailLogs: boolean;
        useKmsCmkEncryption: {
            enabled: boolean;
            optionalExternalCmkArn: string;
        };
        govCloud: {
            enabled: boolean;
            il6Compliant: boolean;
        };
        useGlobalVpc: {
            enabled: boolean;
            useForAllLambdas: boolean;
            addVpcEndpoints: boolean;
            optionalExternalVpcId: string;
            optionalExternalIsolatedSubnetIds: string;
            optionalExternalPrivateSubnetIds: string;
            optionalExternalPublicSubnetIds: string;
            vpcCidrRange: string;
        };
        openSearch: {
            useServerless: {
                enabled: boolean;
                nextGen: boolean;
                allowPublic: boolean;
                enableStandbyReplicas: boolean;
                minIndexingOcu: number;
                maxIndexingOcu: number;
                minSearchOcu: number;
                maxSearchOcu: number;
                deployDeferredIndexSchema: boolean;
            };
            useProvisioned: {
                enabled: boolean;
                availabilityZoneCount: number;
                numberOfShards: number;
                dataNodeInstanceType: string;
                masterNodeInstanceType: string;
                ebsInstanceNodeSizeGb: number;
            };
            reindexOnCdkDeploy: boolean;
        };
        useLocationService: {
            enabled: boolean;
        };
        useAlb: {
            enabled: boolean;
            usePublicSubnet: boolean;
            addAlbS3SpecialVpcEndpoint: boolean;
            domainHost: string;
            certificateArn: string;
            optionalHostedZoneId: string;
        };
        useCloudFront: {
            enabled: boolean;
            customDomain: {
                enabled: boolean;
                domainHost: string;
                certificateArn: string;
                optionalHostedZoneId: string;
            };
        };
        pipelines: {
            useConversion3dBasic: {
                enabled: boolean;
                autoRegisterWithVAMS: boolean;
            };
            useConversionCadMeshMetadataExtraction: {
                enabled: boolean;
                autoRegisterWithVAMS: boolean;
                autoRegisterAutoTriggerOnFileUpload: boolean;
            };
            useConversionCoordinateTransform: {
                enabled: boolean;
                useCodeBuild: boolean;
                autoRegisterWithVAMS: boolean;
                autoRegisterAutoTriggerOnFileUpload: boolean;
            };
            usePreviewPcPotreeViewer: {
                enabled: boolean;
                autoRegisterWithVAMS: boolean;
                autoRegisterAutoTriggerOnFileUpload: boolean;
                sqsAutoRunOnAssetModified: boolean;
            };
            useSplatToolbox: {
                enabled: boolean;
                autoRegisterWithVAMS: boolean;
                sqsAutoRunOnAssetModified: boolean;
            };
            useGenAiMetadata3dLabeling: {
                enabled: boolean;
                bedrockModelId: string;
                autoRegisterWithVAMS: boolean;
                autoRegisterAutoTriggerOnFileUpload: boolean;
            };
            useRapidPipeline: {
                useEcs: {
                    enabled: boolean;
                    ecrContainerImageURI: string;
                    autoRegisterWithVAMS: boolean;
                };
                useEks: {
                    enabled: boolean;
                    ecrContainerImageURI: string;
                    autoRegisterWithVAMS: boolean;
                    eksClusterVersion: string;
                    nodeInstanceType: string;
                    minNodes: number;
                    maxNodes: number;
                    desiredNodes: number;
                    jobTimeout: number;
                    jobMemory: string;
                    jobCpu: string;
                    jobBackoffLimit: number;
                    jobTTLSecondsAfterFinished: number;
                    observability: {
                        enableControlPlaneLogs: boolean;
                        enableContainerInsights: boolean;
                    };
                };
            };
            useModelOps: {
                enabled: boolean;
                ecrContainerImageURI: string;
                autoRegisterWithVAMS: boolean;
            };
            useIsaacLabTraining: {
                enabled: boolean;
                acceptNvidiaEula: boolean;
                useCodeBuild: boolean;
                autoRegisterWithVAMS: boolean;
                keepWarmInstance: boolean;
            };
            usePreview3dThumbnail: {
                enabled: boolean;
                autoRegisterWithVAMS: boolean;
                autoRegisterAutoTriggerOnFileUpload: boolean;
            };
            useNvidiaCosmos: {
                enabled: boolean;
                huggingFaceToken: string;
                useCodeBuild: boolean;
                useWarmInstances: boolean;
                warmInstanceCount: number;
                modelsPredict: {
                    text2world2B_v2: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                    video2world2B_v2: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        autoTriggerOnFileExtensionsUpload: string;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                    text2world14B_v2: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                    video2world14B_v2: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        autoTriggerOnFileExtensionsUpload: string;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                };
                modelsTransfer?: {
                    transfer2B: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        autoTriggerOnFileExtensionsUpload: string;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                };
                modelsReason?: {
                    reason2B: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        autoTriggerOnFileExtensionsUpload: string;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                    reason8B: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        autoTriggerOnFileExtensionsUpload: string;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                };
            };
            useNvidiaGr00t: {
                enabled: boolean;
                huggingFaceToken: string;
                useCodeBuild: boolean;
                useWarmInstances: boolean;
                warmInstanceCount: number;
                modelsFinetune: {
                    gr00tN1_5_3B: {
                        enabled: boolean;
                        autoRegisterWithVAMS: boolean;
                        instanceTypes: string[];
                        maxVCpus: number;
                    };
                };
            };
        };
        addons: {
            useGarnetFramework: {
                enabled: boolean;
                garnetApiEndpoint: string;
                garnetApiToken: string;
                garnetIngestionQueueSqsUrl: string;
            };
            usePhysnaSync: {
                enabled: boolean;
                tenantId: string;
                apiBaseEndpoint: string;
                authTokenEndpoint: string;
                authType: string;
                clientId: string;
                clientSecret: string;
            };
        };
        authProvider: {
            presignedUrlTimeoutSeconds: number;
            authorizerOptions: {
                allowedIpRanges: string[][];
            };
            useCognito: {
                enabled: boolean;
                useSaml: boolean;
                useUserPasswordAuthFlow: boolean;
                credTokenTimeoutSeconds: number;
            };
            useExternalOAuthIdp: {
                enabled: boolean;
                idpAuthProviderUrl: string;
                idpAuthClientId: string;
                idpAuthProviderScope: string;
                idpAuthProviderScopeMfa: string;
                idpAuthPrincipalDomain: string;
                idpAuthProviderTokenEndpoint: string;
                idpAuthProviderAuthorizationEndpoint: string;
                idpAuthProviderDiscoveryEndpoint: string;
                lambdaAuthorizorJWTIssuerUrl: string;
                lambdaAuthorizorJWTAudience: string;
            };
        };
        webUi: {
            optionalBannerHtmlMessage: string;
            allowUnsafeEvalFeatures: boolean;
        };
        api: {
            apiType: string;
            apiGatewayRest: {
                globalRateLimit: number;
                globalBurstLimit: number;
                endpointType: "REGIONAL" | "PRIVATE";
                optionalExternalPrivateApigVPCEId: string;
            };
        };
        metadataSchema: {
            autoLoadDefaultAssetLinksSchema: boolean;
            autoLoadDefaultDatabaseSchema: boolean;
            autoLoadDefaultAssetSchema: boolean;
            autoLoadDefaultAssetFileSchema: boolean;
        };
    };
}

//Internal variables to add to config that should not go into a normal config file (debugging only)
export interface Config extends ConfigPublic {
    enableCdkNag: boolean;
    dockerDefaultPlatform: string;
    s3AdditionalBucketPolicyJSON: any | undefined;
    iamRoleCustomizationJSON: any | undefined; // Loaded from policy/iamRoleConfig.json
    openSearchAssetIndexName: string; // Asset index name
    openSearchFileIndexName: string; // File index name
    openSearchAssetIndexNameSSMParam: string;
    openSearchFileIndexNameSSMParam: string;
    openSearchDomainEndpointSSMParam: string;
    locationServiceApiKeyArnSSMParam: string; // Location Service API key SSM parameter
    webUrlDeploymentSSMParam: string; // Web URL Deployment SSM parameter
}
