/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from 'aws-cdk-lib';
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as cloudTrail from 'aws-cdk-lib/aws-cloudtrail';
import {apiBuilder} from "./api-builder";
import {storageResourcesBuilder} from "./storage-builder";
import {AmplifyConfigLambdaConstruct} from "./constructs/amplify-config-lambda-construct";
import {CloudFrontS3WebSiteConstruct} from "./constructs/cloudfront-s3-website-construct";
import {CognitoWebNativeConstruct} from "./constructs/cognito-web-native-construct";
import {ApiGatewayV2CloudFrontConstruct} from "./constructs/apigatewayv2-cloudfront-construct";
import { Construct } from "constructs";
import { NagSuppressions } from 'cdk-nag';

interface EnvProps {
    prod: boolean, //ToDo: replace with env
    env?: cdk.Environment;
    stackName: string;
    ssmWafArnParameterName: string;
    ssmWafArnParameterRegion: string;
    ssmWafArn: string;
    stagingBucket?: string;
}

export class VAMS extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, {...props, crossRegionReferences: true});

        const providedAdminEmailAddress = process.env.VAMS_ADMIN_EMAIL || scope.node.tryGetContext("adminEmailAddress")
        const adminEmailAddress = new cdk.CfnParameter(this, "adminEmailAddress", {
            type: "String",
            description: "Email address for login and where your password is sent to. You wil be sent a temporary password for the turbine to authenticate to Cognito.",
            default: providedAdminEmailAddress,
        });

        const webAppBuildPath = "../web/build";
        
        const storageResources = storageResourcesBuilder(this, props.stagingBucket);
        const trail = new cloudTrail.Trail(this, 'CloudTrail-VAMS', {
            isMultiRegionTrail: false,
            bucket: storageResources.s3.accessLogsBucket, 
            s3KeyPrefix: 'cloudtrail-logs'
        })
        trail.logAllLambdaDataEvents()
        trail.logAllS3DataEvents()

        const cognitoResources = new CognitoWebNativeConstruct(this, "Cognito", {
            ...props,
            storageResources: storageResources,
        });

        const congitoUser = new cognito.CfnUserPoolUser(this, "AdminUser", {
            username: providedAdminEmailAddress,
            userPoolId: cognitoResources.userPoolId,
            desiredDeliveryMediums: ["EMAIL"],
            userAttributes: [{
                name: "email",
                value: providedAdminEmailAddress
            }]
        });

        // initialize api gateway and bind it to /api route of cloudfront
        const api = new ApiGatewayV2CloudFrontConstruct(this, "api", {
            ...props,
            userPool: cognitoResources.userPool,
            userPoolClient: cognitoResources.webClientUserPool
        });

        const website = new CloudFrontS3WebSiteConstruct(this, "WebApp", {
            ...props,
            webSiteBuildPath: webAppBuildPath,
            webAcl: props.ssmWafArn,
            apiUrl: api.apiUrl,
            assetBucketUrl: storageResources.s3.assetBucket.bucketRegionalDomainName,
        });

        api.addBehaviorToCloudFrontDistribution(website.cloudFrontDistribution);

        apiBuilder(this, api, storageResources);

        // required by AWS internal accounts.  Can be removed in customer Accounts
        // const wafv2Regional = new Wafv2BasicConstruct(this, "Wafv2Regional", {
        //     ...props,
        //     wafScope: WAFScope.REGIONAL,
        // });

        const amplifyConfigFn = new AmplifyConfigLambdaConstruct(this, "AmplifyConfig", {
            ...props,
            api: api.apiGatewayV2,
            appClientId: cognitoResources.webClientId,
            identityPoolId: cognitoResources.identityPoolId,
            userPoolId: cognitoResources.userPoolId,
        });

        const assetBucketOutput = new cdk.CfnOutput(this, "AssetBucketNameOutput", {
            value: storageResources.s3.assetBucket.bucketName,
            description: "S3 bucket for asset storage"
        })

        const artefactsBucketOutput = new cdk.CfnOutput(this, "artefactsBucketOutput", {
            value: storageResources.s3.artefactsBucket.bucketName,
            description: "S3 bucket for template notebooks"
        })


        NagSuppressions.addResourceSuppressions(this, [
            {
                id: "AwsSolutions-IAM4",
                reason: "Intend to use AWSLambdaBasicExecutionRole as is at this stage of this project.",
                appliesTo: [
                    {
                        regex: "/.*AWSLambdaBasicExecutionRole$/g",
                    }
                ]
            }
        ], true);


        NagSuppressions.addResourceSuppressionsByPath(this, `/${props.stackName}/WebApp/WebAppDistribution/Resource`, [
            {
                id: "AwsSolutions-CFR4",
                reason: "This requires use of a custom viewer certificate which should be provided by customers."
            }
        ],true);

        const refactorPaths = [
            `/${props.stackName}/VAMSWorkflowIAMRole/Resource`, 
            `/${props.stackName}/lambdaPipelineRole`, 
            `/${props.stackName}/pipelineService`, 
            `/${props.stackName}/workflowService`, 
            `/${props.stackName}/listExecutions`
        ]
        for(let path of refactorPaths) {
            const reason = 
                `Intention is to refactor this model away moving forward 
                 so that this type of access is not required within the stack.
                 Customers are advised to isolate VAMS to its own account in test and prod
                 as a substitute to tighter resource access.`;
            NagSuppressions.addResourceSuppressionsByPath(this, path, [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: reason,
                }
            ], true);

        }


    }
}
