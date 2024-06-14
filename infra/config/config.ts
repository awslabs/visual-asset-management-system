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
export const LAMBDA_PYTHON_RUNTIME = Runtime.PYTHON_3_10;
export const LAMBDA_NODE_RUNTIME = Runtime.NODEJS_18_X;
export const LAMBDA_MEMORY_SIZE = 3003;
export const OPENSEARCH_VERSION = cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_2_7;

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
    config.app.bucketMigrationStaging.assetBucketName = <string>(app.node.tryGetContext(
        "staging-bucket"
    ) || //here to keep backwards compatability
        app.node.tryGetContext("asset-staging-bucket") ||
        config.app.bucketMigrationStaging.assetBucketName ||
        process.env.STAGING_BUCKET || //here to keep backwards compatability
        process.env.ASSET_STAGING_BUCKET);
    config.app.adminEmailAddress = <string>(
        (app.node.tryGetContext("adminEmailAddress") ||
            config.app.adminEmailAddress ||
            process.env.ADMIN_EMAIL_ADDRESS)
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

    //OpenSearch Variables
    config.openSearchIndexName = "assets1236";
    config.openSearchIndexNameSSMParam =
        "/" + ["vams-" + config.app.baseStackName, "aos", "indexName"].join("/");
    config.openSearchDomainEndpointSSMParam =
        "/" + ["vams-" + config.app.baseStackName, "aos", "endPoint"].join("/");

    //Fill in some basic values to false if blank
    //Note: usually added for backwards compatabibility of an old config file that hasn't had the newest elements added
    if (config.app.openSearch.useServerless.enabled == undefined) {
        config.app.openSearch.useServerless.enabled = false;
    }

    if (config.app.openSearch.useProvisioned.enabled == undefined) {
        config.app.openSearch.useProvisioned.enabled = false;
    }

    if (config.app.pipelines.usePreviewPcPotreeViewer.enabled == undefined) {
        config.app.pipelines.usePreviewPcPotreeViewer.enabled = false;
    }

    if (config.app.authProvider.useCognito.useUserPasswordAuthFlow == undefined) {
        config.app.authProvider.useCognito.useUserPasswordAuthFlow = false;
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

    //If we are govCloud, we always use VPC, ALB deploy, use OpenSearch Provisioned (serverless not available in GovCloud), and disable location service (currently not supported in GovCloud 08-29-2023)
    //Note: FIP not required for use in GovCloud. Some GovCloud endpoints are natively FIPS compliant regardless of this flag to use specific FIPS endpoints.
    //Note: FedRAMP best practices require all Lambdas/OpenSearch behind VPC but not required for GovCloud
    if (config.app.govCloud.enabled) {
        if (
            !config.app.useGlobalVpc.enabled ||
            !config.app.useAlb.enabled ||
            config.app.openSearch.useServerless.enabled ||
            config.app.useLocationService.enabled
        ) {
            console.warn(
                "Configuration Warning: Due to GovCloud being enabled, auto-enabling Use Global VPC, Use ALB, Use OpenSearch Provisioned, and disable Use Location Services"
            );
        }
        config.app.useGlobalVpc.enabled = true;
        config.app.useAlb.enabled = true;
        config.app.openSearch.useServerless.enabled = false;
        config.app.useLocationService.enabled = false;
    }

    //If using ALB, data pipelines , or opensearch provisioned, make sure Global VPC is on as this needs to be in a VPC
    if (
        config.app.useAlb.enabled ||
        config.app.pipelines.usePreviewPcPotreeViewer.enabled ||
        config.app.pipelines.useGenAiMetadata3dExtraction.enabled ||
        config.app.openSearch.useProvisioned.enabled
    ) {
        if (!config.app.useGlobalVpc.enabled) {
            console.warn(
                "Configuration Warning: Due to ALB, Data Pipelines, or OpenSearch Provisioned being enabled, auto-enabling Use Global VPC flag"
            );
        }

        config.app.useGlobalVpc.enabled = true;
    }

    //Any configuration warnings/errors checks
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
            !config.app.useGlobalVpc.optionalExternalPrivateSubnetIds ||
            config.app.useGlobalVpc.optionalExternalPrivateSubnetIds == "UNDEFINED" ||
            config.app.useGlobalVpc.optionalExternalPrivateSubnetIds == ""
        ) {
            throw new Error(
                "Configuration Error: Must define at least one private subnet ID when using an External VPC ID."
            );
        }
    }

    if (
        config.app.useGlobalVpc.enabled &&
        config.app.useAlb.enabled &&
        config.app.useAlb.usePublicSubnet &&
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
                "Configuration Error: Must define at least one public subnet ID when using an External VPC ID and Public ALB configuration."
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

    //Error check when implementing openSearch
    if (
        config.app.openSearch.useServerless.enabled &&
        config.app.openSearch.useProvisioned.enabled
    ) {
        throw new Error("Configuration Error: Must specify either none or one openSearch method!");
    }

    //Check when implementing auth providers
    if (
        config.app.authProvider.useCognito.enabled &&
        config.app.authProvider.useExternalOathIdp.enabled
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
    config.app.authProvider.useCognito.useUserPasswordAuthFlow;

    if (
        config.app.authProvider.useExternalOathIdp.enabled &&
        (config.app.authProvider.useExternalOathIdp.idpAuthProviderUrl == "UNDEFINED" ||
            config.app.authProvider.useExternalOathIdp.idpAuthProviderUrl == "")
    ) {
        throw new Error(
            "Configuration Error: Must specify a external IDP auth URL when using an external OATH provider!"
        );
    }

    return config;
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
        bucketMigrationStaging: {
            assetBucketName: string;
        };
        adminEmailAddress: string;
        useFips: boolean;
        useWaf: boolean;
        useKmsCmkEncryption: {
            enabled: boolean;
            optionalExternalCmkArn: string;
        };
        govCloud: {
            enabled: boolean;
        };
        useGlobalVpc: {
            enabled: boolean;
            useForAllLambdas: boolean;
            addVpcEndpoints: boolean;
            optionalExternalVpcId: string;
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
        };
        useLocationService: {
            enabled: boolean;
        };
        useAlb: {
            enabled: boolean;
            usePublicSubnet: boolean;
            domainHost: string;
            certificateArn: string;
            optionalHostedZoneId: string;
        };
        pipelines: {
            usePreviewPcPotreeViewer: {
                enabled: boolean;
            };
            useGenAiMetadata3dExtraction: {
                enabled: boolean;
            };
        };
        authProvider: {
            useCognito: {
                enabled: boolean;
                useSaml: boolean;
                useUserPasswordAuthFlow: boolean;
            };
            useExternalOathIdp: {
                enabled: boolean;
                idpAuthProviderUrl: string;
            };
        };
    };
}

//Internal variables to add to config that should not go into a normal config file (debugging only)
export interface Config extends ConfigPublic {
    enableCdkNag: boolean;
    dockerDefaultPlatform: string;
    s3AdditionalBucketPolicyJSON: any | undefined;
    openSearchIndexName: string;
    openSearchIndexNameSSMParam: string;
    openSearchDomainEndpointSSMParam: string;
}
