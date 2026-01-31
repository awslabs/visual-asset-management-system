/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as cloudTrail from "aws-cdk-lib/aws-cloudtrail";
import * as logs from "aws-cdk-lib/aws-logs";
import { ApiBuilderNestedStack } from "./nestedStacks/apiLambda/apiBuilder-nestedStack";
import { StorageResourcesBuilderNestedStack } from "./nestedStacks/storage/storageBuilder-nestedStack";
import { AuthBuilderNestedStack } from "./nestedStacks/auth/authBuilder-nestedStack";
import { ApiGatewayV2AmplifyNestedStack } from "./nestedStacks/apiLambda/apigatewayv2-amplify-nestedStack";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { CustomFeatureEnabledConfigNestedStack } from "./nestedStacks/featureEnabled/custom-featureEnabled-config-nestedStack";
import { LocationServiceNestedStack } from "./nestedStacks/locationService/location-service-nestedStack";
import { SearchBuilderNestedStack } from "./nestedStacks/searchAndIndexing/searchBuilder-nestedStack";
import { StaticWebBuilderNestedStack } from "./nestedStacks/staticWebApp/staticWebBuilder-nestedStack";
import * as Config from "../config/config";
import { VAMS_APP_FEATURES } from "../common/vamsAppFeatures";
import { PipelineBuilderNestedStack } from "./nestedStacks/pipelines/pipelineBuilder-nestedStack";
import { LambdaLayersBuilderNestedStack } from "./nestedStacks/apiLambda/lambdaLayersBuilder-nestedStack";
import { VPCBuilderNestedStack } from "./nestedStacks/vpc/vpcBuilder-nestedStack";
import { AddonBuilderNestedStack } from "./nestedStacks/addon/addonBuilder-nestedStack";
import { IamRoleTransform } from "./aspects/iam-role-transform.aspect";
import { LogRetentionAspect } from "./aspects/log-retention.aspect";
import * as s3AssetBuckets from "./helper/s3AssetBuckets";
import { Aspects } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { generateUniqueNameHash } from "./helper/security";
import { CfnStack } from "aws-cdk-lib";

export interface EnvProps {
    env: cdk.Environment;
    stackName: string;
    ssmWafArn: string;
    config: Config.Config;
    description: string;
}

export class CoreVAMSStack extends cdk.Stack {
    private enabledFeatures: string[] = [];
    private webAppBuildPath = "../web/build";

    private vpc: ec2.IVpc;
    private subnetsIsolated: ec2.ISubnet[];
    private subnetsPrivate: ec2.ISubnet[];
    private subnetsPublic: ec2.ISubnet[];
    private vpceSecurityGroup: ec2.ISecurityGroup;

    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, { ...props, crossRegionReferences: true });

        const adminUserId = new cdk.CfnParameter(this, "adminUserId", {
            type: "String",
            description: "Admin User ID for login",
            default: props.config.app.adminUserId,
        });

        const adminEmailAddress = new cdk.CfnParameter(this, "adminEmailAddress", {
            type: "String",
            description:
                "Admin Email address for login and where your password is sent to. You will be sent a temporary password to authenticate to Cognito.",
            default: props.config.app.adminEmailAddress,
        });

        //Add tags to stack with cdk.json "environment" settings (if defined)
        //Modify roles with cdk.json "aws" settings (if defined)
        const environments = this.node.tryGetContext("environments");
        const commonEnv = environments["common"] || undefined;
        const awsEnv = environments["aws"] || undefined;
        if (commonEnv) {
            Object.keys(commonEnv).forEach(function (key) {
                if (commonEnv[key] != "") {
                    cdk.Tags.of(scope).add(key, commonEnv[key]);
                }
            });
        }
        if (awsEnv) {
            Aspects.of(this).add(
                new IamRoleTransform(
                    this,
                    awsEnv["IamRoleNamePrefix"],
                    awsEnv["PermissionBoundaryArn"]
                )
            );
        }

        // Apply the aspect to set log retention for all Log Groups in this stack
        Aspects.of(this).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));

        //Setup GovCloud Feature Enabled
        if (props.config.app.govCloud.enabled) {
            this.enabledFeatures.push(VAMS_APP_FEATURES.GOVCLOUD);
        }

        if (props.config.app.webUi.allowUnsafeEvalFeatures) {
            this.enabledFeatures.push(VAMS_APP_FEATURES.ALLOWUNSAFEEVAL);
        }

        //Deploy VPC (nested stack)
        if (props.config.app.useGlobalVpc.enabled) {
            const vpcBuilderNestedStack = new VPCBuilderNestedStack(this, "VPCBuilder", {
                config: props.config,
            });

            this.vpc = vpcBuilderNestedStack.vpc;
            this.vpceSecurityGroup = vpcBuilderNestedStack.vpceSecurityGroup;
            this.subnetsIsolated = vpcBuilderNestedStack.isolatedSubnets;
            this.subnetsPrivate = vpcBuilderNestedStack.privateSubnets;
            this.subnetsPublic = vpcBuilderNestedStack.publicSubnets;

            const vpcIdOutput = new cdk.CfnOutput(this, "VpcIdOutput", {
                value: this.vpc.vpcId,
                description: "VPC ID created or used by VAMS deployment",
            });
        }

        //Deploy Lambda Layers (nested stack)
        const lambdaLayers = new LambdaLayersBuilderNestedStack(this, "LambdaLayers", {});

        //Deploy Storage Resources (nested stack)
        const storageResourcesNestedStack = new StorageResourcesBuilderNestedStack(
            this,
            "StorageResourcesBuilder",
            props.config,
            lambdaLayers.lambdaCommonBaseLayer,
            this.vpc,
            this.subnetsIsolated
        );

        //Setup cloud trail and log groups (if enabled)
        if (props.config.app.addStackCloudTrailLogs) {
            const trailLogGroup = new logs.LogGroup(this, "CloudTrailLogGroup", {
                logGroupName:
                    "/aws/vendedlogs/VAMSCloudTrailLogs" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "VAMSCloudTrailLogs",
                        10
                    ),
                retention: logs.RetentionDays.TEN_YEARS,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            const trail = new cloudTrail.Trail(this, "CloudTrail-VAMS", {
                isMultiRegionTrail: false,
                bucket: storageResourcesNestedStack.storageResources.s3.accessLogsBucket,
                s3KeyPrefix: "cloudtrail-logs",
                sendToCloudWatchLogs: true, //AppSec requirements
                cloudWatchLogGroup: trailLogGroup, //AppSec requirements
            });
            trail.logAllLambdaDataEvents();
            trail.logAllS3DataEvents();
        }

        //Select auth provider
        if (props.config.app.authProvider.useCognito.enabled) {
            this.enabledFeatures.push(VAMS_APP_FEATURES.AUTHPROVIDER_COGNITO);
        } else if (props.config.app.authProvider.useExternalOAuthIdp.enabled) {
            this.enabledFeatures.push(VAMS_APP_FEATURES.AUTHPROVIDER_EXTERNALOAUTHIDP);
        }

        //See if we have enabled SAML settings
        if (props.config.app.authProvider.useCognito.useSaml) {
            this.enabledFeatures.push(VAMS_APP_FEATURES.AUTHPROVIDER_COGNITO_SAML);
        }

        //Setup Auth (Nested Stack)
        const authBuilderNestedStack = new AuthBuilderNestedStack(this, "AuthBuilder", {
            lambdaCommonBaseLayer: lambdaLayers.lambdaCommonBaseLayer,
            storageResources: storageResourcesNestedStack.storageResources,
            config: props.config,
            vpc: this.vpc,
            subnets: this.subnetsIsolated,
        });
        authBuilderNestedStack.addDependency(storageResourcesNestedStack);

        //Ignore stacks if we are only loading context (mostly for Imported VPC)
        if (!props.config.env.loadContextIgnoreVPCStacks) {
            // Deploy api gateway + amplify configuration endpoints (nested stack)
            const apiNestedStack = new ApiGatewayV2AmplifyNestedStack(this, "Api", {
                ...props,
                authResources: authBuilderNestedStack.authResources,
                storageResources: storageResourcesNestedStack.storageResources,
                config: props.config,
                lambdaCommonBaseLayer: lambdaLayers.lambdaCommonBaseLayer,
                lambdaAuthorizerLayer: lambdaLayers.lambdaAuthorizerLayer,
                vpc: this.vpc,
                subnets: this.subnetsIsolated,
            });
            apiNestedStack.addDependency(storageResourcesNestedStack);

            //Deploy Static Website and any API proxies (nested stack)
            if (props.config.app.useAlb.enabled || props.config.app.useCloudFront.enabled) {
                const staticWebBuilderNestedStack = new StaticWebBuilderNestedStack(
                    this,
                    "StaticWeb",
                    {
                        config: props.config,
                        vpc: this.vpc,
                        subnetsIsolated: this.subnetsIsolated,
                        subnetsPublic: this.subnetsPublic,
                        webAppBuildPath: this.webAppBuildPath,
                        apiUrl: apiNestedStack.apiEndpoint,
                        storageResources: storageResourcesNestedStack.storageResources,
                        ssmWafArn: props.ssmWafArn,
                        authResources: authBuilderNestedStack.authResources,
                    }
                );
                staticWebBuilderNestedStack.addDependency(storageResourcesNestedStack);

                //Set features
                if (props.config.app.useCloudFront.enabled) {
                    this.enabledFeatures.push(VAMS_APP_FEATURES.CLOUDFRONTDEPLOY);
                }

                if (props.config.app.useAlb.enabled) {
                    this.enabledFeatures.push(VAMS_APP_FEATURES.ALBDEPLOY);
                }

                //Write final output configurations (pulling forward from static web nested stacks)
                const endPointURLParamsOutput = new cdk.CfnOutput(
                    this,
                    "WebsiteEndpointURLOutput",
                    {
                        value: staticWebBuilderNestedStack.endpointURL,
                        description: "Website endpoint URL",
                    }
                );

                const webAppS3BucketNameParamsOutput = new cdk.CfnOutput(
                    this,
                    "WebAppS3BucketNameOutput",
                    {
                        value: staticWebBuilderNestedStack.webAppBucket.bucketName,
                        description: "S3 Bucket for static web app files",
                    }
                );

                if (props.config.app.useAlb.enabled) {
                    const albEndpointOutput = new cdk.CfnOutput(this, "AlbEndpointOutput", {
                        value: staticWebBuilderNestedStack.albEndpoint,
                        description:
                            "ALB DNS Endpoint to use for primary domain host DNS routing to static web site",
                    });
                }
            }

            //Deploy Backend API framework (nested stack)
            const apiBuilderNestedStack = new ApiBuilderNestedStack(
                this,
                "ApiBuilder",
                props.config,
                apiNestedStack.apiGatewayV2,
                storageResourcesNestedStack.storageResources,
                authBuilderNestedStack.authResources,
                lambdaLayers.lambdaCommonBaseLayer,
                this.vpc,
                this.subnetsIsolated
            );
            apiBuilderNestedStack.addDependency(storageResourcesNestedStack);

            //Deploy OpenSearch Serverless (nested stack)
            const searchBuilderNestedStack = new SearchBuilderNestedStack(
                this,
                "SearchBuilder",
                props.config,
                apiNestedStack.apiGatewayV2,
                storageResourcesNestedStack.storageResources,
                lambdaLayers.lambdaCommonBaseLayer,
                this.vpc,
                this.subnetsIsolated
            );
            storageResourcesNestedStack.addDependency(storageResourcesNestedStack);

            //Set feature for no opensearch in neither provisioned or serverless selected
            if (
                !props.config.app.openSearch.useProvisioned.enabled &&
                !props.config.app.openSearch.useServerless.enabled
            ) {
                this.enabledFeatures.push(VAMS_APP_FEATURES.NOOPENSEARCH);
            }

            ///Optional Pipelines (Nested Stack)
            const pipelineBuilderNestedStack = new PipelineBuilderNestedStack(
                this,
                "PipelineBuilder",
                {
                    ...props,
                    config: props.config,
                    storageResources: storageResourcesNestedStack.storageResources,
                    lambdaCommonBaseLayer: lambdaLayers.lambdaCommonBaseLayer,
                    vpc: this.vpc,
                    vpceSecurityGroup: this.vpceSecurityGroup,
                    isolatedSubnets: this.subnetsIsolated,
                    privateSubnets: this.subnetsPrivate,
                    importGlobalPipelineWorkflowFunctionName:
                        apiBuilderNestedStack.importGlobalPipelineWorkflowFunctionName,
                }
            );
            pipelineBuilderNestedStack.addDependency(storageResourcesNestedStack);

            ///Optional Addons (Nested Stack)
            const addonBuilderNestedStack = new AddonBuilderNestedStack(this, "AddonBuilder", {
                ...props,
                config: props.config,
                storageResources: storageResourcesNestedStack.storageResources,
                lambdaCommonBaseLayer: lambdaLayers.lambdaCommonBaseLayer,
                vpc: this.vpc,
                isolatedSubnets: this.subnetsIsolated,
                privateSubnets: this.subnetsPrivate,
            });
            addonBuilderNestedStack.addDependency(storageResourcesNestedStack);

            //Write final output configurations (pulling forward from nested stacks)
            const gatewayURLParamsOutput = new cdk.CfnOutput(this, "APIGatewayEndpointOutput", {
                value: `${apiNestedStack.apiEndpoint}`,
                description: "API Gateway endpoint",
            });

            const importGlobalPipelineWorkflowFunctionNameOutput = new cdk.CfnOutput(
                this,
                "ImportGlobalPipelineWorkflowFunctionNameOutput",
                {
                    value: apiBuilderNestedStack.importGlobalPipelineWorkflowFunctionName,
                    description:
                        "Lambda function name for importing global pipelines and workflows from IaC deployments",
                }
            );

            let useCasefunctionNumber = 1;
            for (const pipelineVamsExecuteLambdaFunction of pipelineBuilderNestedStack.pipelineVamsLambdaFunctionNames) {
                const pipelineVamsExecuteLambdaFunctionOutput = new cdk.CfnOutput(
                    this,
                    `VamsPipelineExecuteLambdaFunctionOutput_${useCasefunctionNumber}`,
                    {
                        value: pipelineVamsExecuteLambdaFunction,
                        description: "Use-case Pipeline - VAMS Execute Lambda Function to Register",
                    }
                );
                useCasefunctionNumber = useCasefunctionNumber + 1;
            }

            //Nag supressions
            const refactorPaths = [`/${props.stackName}/ApiBuilder/VAMSWorkflowIAMRole/Resource`];

            for (const path of refactorPaths) {
                const reason = `Intention is to refactor this model away moving forward 
                so that this type of access is not required within the stack.
                Customers are advised to isolate VAMS to its own account in test and prod
                as a substitute to tighter resource access.`;
                NagSuppressions.addResourceSuppressionsByPath(
                    this,
                    path,
                    [
                        {
                            id: "AwsSolutions-IAM5",
                            reason: reason,
                        },
                        {
                            id: "AwsSolutions-IAM4",
                            reason: reason,
                        },
                    ],
                    true
                );
            }
        }

        //Deploy Location Services (Nested Stack) and setup feature enabled
        if (props.config.app.useLocationService.enabled) {
            const locationServiceNestedStack = new LocationServiceNestedStack(
                this,
                "LocationService",
                {
                    config: props.config,
                }
            );

            this.enabledFeatures.push(VAMS_APP_FEATURES.LOCATIONSERVICES);
        }

        //Deploy Enabled Feature Tracking (Nested Stack)
        const customFeatureEnabledConfigNestedStack = new CustomFeatureEnabledConfigNestedStack(
            this,
            "CustomFeatureEnabledConfig",
            {
                appFeatureEnabledTable:
                    storageResourcesNestedStack.storageResources.dynamo
                        .appFeatureEnabledStorageTable,
                featuresEnabled: this.enabledFeatures,
                kmsKey: storageResourcesNestedStack.storageResources.encryption.kmsKey,
            }
        );

        //Write final output configurations (pulling forward from nested stacks)
        if (storageResourcesNestedStack.storageResources.encryption.kmsKey) {
            const kmsEncryptionKeyOutput = new cdk.CfnOutput(this, "KMSEncryptionKeyARNOutput", {
                value: storageResourcesNestedStack.storageResources.encryption.kmsKey!.keyArn,
                description: "VAMS KMS Encryption key ARN used",
            });
        }

        if (props.config.app.authProvider.useCognito.enabled) {
            const authCognitoUserPoolIdParamsOutput = new cdk.CfnOutput(
                this,
                "AuthCognito_UserPoolId",
                {
                    value: authBuilderNestedStack.authResources.cognito.userPoolId,
                }
            );
            const authCognitoIdentityPoolIdParamsOutput = new cdk.CfnOutput(
                this,
                "AuthCognito_IdentityPoolId",
                {
                    value: authBuilderNestedStack.authResources.cognito.identityPoolId,
                }
            );
            const authCognitoUserWebClientIdParamsOutput = new cdk.CfnOutput(
                this,
                "AuthCognito_WebClientId",
                {
                    value: authBuilderNestedStack.authResources.cognito.webClientId,
                }
            );
        }

        const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
        let assetBucketIndex = 0;
        for (const record of assetBucketRecords) {
            const assetBucketOutput = new cdk.CfnOutput(
                this,
                "AssetS3BucketNameOutput" + assetBucketIndex,
                {
                    value: record.bucket.bucketName,
                    description: "S3 bucket for asset storage - IndexCount:" + assetBucketIndex,
                }
            );
            assetBucketIndex = assetBucketIndex + 1;
        }

        const assetAuxiliaryBucketOutput = new cdk.CfnOutput(
            this,
            "AssetAuxiliaryS3BucketNameOutput",
            {
                value: storageResourcesNestedStack.storageResources.s3.assetAuxiliaryBucket
                    .bucketName,
                description:
                    "S3 bucket for auto-generated auxiliary working objects associated with asset storage to include auto-generated previews, visualizer files, temporary storage for pipelines",
            }
        );

        const artefactsBucketOutput = new cdk.CfnOutput(this, "ArtefactsS3BucketNameOutput", {
            value: storageResourcesNestedStack.storageResources.s3.artefactsBucket.bucketName,
            description: "S3 bucket for template notebooks",
        });

        //Add tags to stack
        cdk.Tags.of(this).add("vams:stackname", props.stackName);

        //Add for Systems Manager->Application Manager Cost Tracking for main VAMS Stack
        //TODO: Figure out why tag is not getting added to stack
        cdk.Tags.of(this).add("AppManagerCFNStackKey", this.stackId, {
            includeResourceTypes: ["AWS::CloudFormation::Stack"],
        });

        //Global Nag Supressions
        this.node.findAll().forEach((item) => {
            if (item instanceof cdk.aws_lambda.Function) {
                const fn = item as cdk.aws_lambda.Function;
                // python3.11 suppressed for CDK Bucket Deployment which is fixed to python 3.11 (https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_s3_deployment/README.html)
                // python3.12 suppressed for all other lambdas. Latest version for non-breaking changes as of 10/2024.
                // nodejs18.x suppressed for use of custom resource to deploy saml in CustomCognitoConfigConstruct
                // nodejs20.x suppressed for use of custom resource to deploy saml in CustomCognitoConfigConstruct
                if (
                    fn.runtime.name === "python3.11" ||
                    fn.runtime.name === "python3.12" ||
                    fn.runtime.name === "nodejs18.x" ||
                    fn.runtime.name === "nodejs20.x"
                ) {
                    //console.log(item.node.path,fn.runtime.name)
                    NagSuppressions.addResourceSuppressions(fn, [
                        {
                            id: "AwsSolutions-L1",
                            reason: "The lambda function is configured with the appropriate runtime version",
                        },
                    ]);
                }
                return;
            }
            return;
        });

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Allow permissions for KMS unencryption/re-encryption for keys generated within VAMS. Policy statements additions on imported keys are No-Op statements and must be set externally to the deployment.",
                    appliesTo: [
                        {
                            regex: "/^Action::kms:(.*)\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Intend to use AWSLambdaVPCAccessExecutionRole as is at this stage of this project.",
                    appliesTo: [
                        {
                            regex: "/.*AWSLambdaVPCAccessExecutionRole$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Intend to use AWSLambdaBasicExecutionRole as is at this stage of this project.",
                    appliesTo: [
                        {
                            regex: "/.*AWSLambdaBasicExecutionRole$/g",
                        },
                    ],
                },
            ],
            true
        );
    }
}
