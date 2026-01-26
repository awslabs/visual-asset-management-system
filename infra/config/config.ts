/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { RemovalPolicy } from "aws-cdk-lib";
import { Runtime } from "aws-cdk-lib/aws-lambda";
import { readFileSync } from "fs";
import { join } from "path";
import * as dotenv from "dotenv";
import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { region_info } from "aws-cdk-lib";

dotenv.config();

//Top level configurations
export const VAMS_VERSION = "2.4.1";

export const LAMBDA_PYTHON_RUNTIME = Runtime.PYTHON_3_12;
export const LAMBDA_NODE_RUNTIME = Runtime.NODEJS_20_X;
export const LAMBDA_MEMORY_SIZE = 5308;
export const OPENSEARCH_VERSION = cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_2_7;

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

    //OpenSearch Variables - Dual Index Configuration
    config.openSearchAssetIndexName = "vams-assets-v2";
    config.openSearchFileIndexName = "vams-files-v2";
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

    if (config.app.openSearch.useProvisioned.enabled == undefined) {
        config.app.openSearch.useProvisioned.enabled = false;
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
            autoRegisterWithVAMS: true,
            keepWarmInstance: false,
        };
    }

    if (config.app.pipelines.useIsaacLabTraining.enabled == undefined) {
        config.app.pipelines.useIsaacLabTraining.enabled = false;
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

    if (config.app.addons.useGarnetFramework.enabled == undefined) {
        config.app.addons.useGarnetFramework.enabled = false;
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
        config.app.api = { globalRateLimit: 50, globalBurstLimit: 100 };
    }

    if (config.app.api.globalRateLimit == undefined) {
        config.app.api.globalRateLimit = 50;
    }

    if (config.app.api.globalBurstLimit == undefined) {
        config.app.api.globalBurstLimit = 100;
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

    //If using ALB, data pipelines , or opensearch provisioned, make sure Global VPC is on as this needs to be in a VPC
    if (
        config.app.useAlb.enabled ||
        config.app.pipelines.usePreviewPcPotreeViewer.enabled ||
        config.app.pipelines.useSplatToolbox.enabled ||
        config.app.pipelines.useGenAiMetadata3dLabeling.enabled ||
        config.app.pipelines.useRapidPipeline.useEcs.enabled ||
        config.app.pipelines.useRapidPipeline.useEks.enabled ||
        config.app.pipelines.useModelOps.enabled ||
        config.app.pipelines.useIsaacLabTraining.enabled ||
        config.app.openSearch.useProvisioned.enabled
    ) {
        if (!config.app.useGlobalVpc.enabled) {
            console.warn(
                "Configuration Warning: Due to ALB, Use-Case Pipelines, or OpenSearch Provisioned being enabled, auto-enabling Use Global VPC flag"
            );
        }

        config.app.useGlobalVpc.enabled = true;
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
    if (config.app.useCloudFront.enabled && config.app.useAlb.enabled) {
        console.warn(
            "Configuration Warning: YOU HAVE DISABLED DEPLOYING ANY VAMS FRONT-END WITH CLOUDFRONT OR ALB. THIS WILL BE A API-DRIVEN SOLUTION-ONLY DEPLOYMENT."
        );
    }

    //Cloudfront + ALB neither warning check
    if (!config.app.useCloudFront.enabled && !config.app.useAlb.enabled) {
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
    if (config.app.api.globalRateLimit <= 0) {
        throw new Error(
            "Configuration Error: API globalRateLimit must be a positive number greater than 0."
        );
    }

    if (config.app.api.globalBurstLimit <= 0) {
        throw new Error(
            "Configuration Error: API globalBurstLimit must be a positive number greater than 0."
        );
    }

    if (config.app.api.globalBurstLimit < config.app.api.globalRateLimit) {
        throw new Error(
            "Configuration Error: API globalBurstLimit must be greater than or equal to globalRateLimit."
        );
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

    return config;
}

export interface ConfigPublicAssetS3Buckets {
    bucketArn: string;
    baseAssetsPrefix: string;
    defaultSyncDatabaseId: string;
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
            externalAssetBuckets: [ConfigPublicAssetS3Buckets];
        };
        adminUserId: string;
        adminEmailAddress: string;
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
            };
            useProvisioned: {
                enabled: boolean;
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
                autoRegisterWithVAMS: boolean;
                keepWarmInstance: boolean;
            };
        };
        addons: {
            useGarnetFramework: {
                enabled: boolean;
                garnetApiEndpoint: string;
                garnetApiToken: string;
                garnetIngestionQueueSqsUrl: string;
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
            globalRateLimit: number;
            globalBurstLimit: number;
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
    openSearchAssetIndexName: string; // Asset index name
    openSearchFileIndexName: string; // File index name
    openSearchAssetIndexNameSSMParam: string;
    openSearchFileIndexNameSSMParam: string;
    openSearchDomainEndpointSSMParam: string;
    locationServiceApiKeyArnSSMParam: string; // Location Service API key SSM parameter
    webUrlDeploymentSSMParam: string; // Web URL Deployment SSM parameter
}
