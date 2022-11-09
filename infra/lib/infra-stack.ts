/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from 'aws-cdk-lib';
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudTrail from 'aws-cdk-lib/aws-cloudtrail';
import {apiBuilder} from "./api-builder";
import {storageResourcesBuilder} from "./storage-builder";
import {AmplifyConfigLambdaConstruct} from "./constructs/amplify-config-lambda-construct";
import {CloudFrontS3WebSiteConstruct} from "./constructs/cloudfront-s3-website-construct";
import {CognitoWebNativeConstruct} from "./constructs/cognito-web-native-construct";
import {ApiGatewayV2CloudFrontConstruct} from "./constructs/apigatewayv2-cloudfront-construct";
import { Wafv2BasicConstruct, WAFScope } from "./constructs/wafv2-basic-construct";
import { Construct } from "constructs";
import { NagSuppressions } from 'cdk-nag';

interface EnvProps {
    prod: boolean, //ToDo: replace with env
    env?: cdk.Environment;
    stackName: string;
}

export class VAMS extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id);

        const adminEmailAddress = new cdk.CfnParameter(this, "adminEmailAddress", {
            type: "String",
            description: "Email address for login and where your password is sent to. You wil be sent a temporary password for the turbine to authenticate to Cognito."
        });

        const webAppBuildPath = "../web/build";

        const storageResources = storageResourcesBuilder(this);
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
            username: adminEmailAddress.valueAsString,
            userPoolId: cognitoResources.userPoolId,
            desiredDeliveryMediums: ["EMAIL"],
            userAttributes: [{
                name: "email",
                value: adminEmailAddress.valueAsString
            }]
        });

        const wafv2CF = new Wafv2BasicConstruct(this, "Wafv2CF", {
            ...props,
            wafScope: WAFScope.CLOUDFRONT,
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
            webAcl: wafv2CF.webacl.attrArn,
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
            env: {region: cdk.Stack.of(this).region || process.env.AWS_REGION }
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


        NagSuppressions.addResourceSuppressionsByPath(this, "/dev/WebApp/WebAppDistribution/Resource", [
            {
                id: "AwsSolutions-CFR4",
                reason: "This requires use of a custom viewer certificate which should be provided by customers."
            }
        ],true);

        const refactorPaths = [
            "/dev/VAMSWorkflowIAMRole/Resource", 
            "/dev/lambdaPipelineRole", 
            "/dev/pipelineService", 
            "/dev/workflowService", 
            "/dev/listExecutions"
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
