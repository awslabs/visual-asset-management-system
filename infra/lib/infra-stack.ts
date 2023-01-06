/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from 'aws-cdk-lib';
import * as cloudTrail from 'aws-cdk-lib/aws-cloudtrail';
import { ApiBuilderNestedStack } from "./api-builder";
import { nestedStorageResourcesBuilder } from "./constructs/storage-builder-construct";
import { AmplifyConfigLambdaConstruct } from "./constructs/amplify-config-lambda-construct";
import { CloudFrontS3WebSiteConstruct } from "./constructs/cloudfront-s3-website-construct";
import { CognitoStack, CognitoUser } from "./constructs/cognito-web-native-construct";
import { ApiGatewayV2CloudFrontConstruct } from "./constructs/apigatewayv2-cloudfront-construct";
import { Wafv2BasicConstruct, WAFScope } from "./constructs/wafv2-basic-construct";
import { Construct } from "constructs";
import { IamRoleTransform} from "./aspects/iam-role-transform.aspect";
import { StackNagSuppression } from './constructs/nag-suppresions-construct';
import { Aspects } from 'aws-cdk-lib';

interface EnvProps {
    prod: boolean, //ToDo: replace with env
    env?: cdk.Environment;
    stackName: string;
}

export class VAMS extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id);
        const environments = this.node.tryGetContext("environments");
        const commonEnv = environments["common"] || undefined;
        const awsEnv = environments["aws"] || undefined;
        if (commonEnv) {
            Object.keys(commonEnv).forEach(function (key) {
                if(commonEnv[key]!=""){
                    cdk.Tags.of(scope).add(key, commonEnv[key])
                }
            });
        }
        if (awsEnv) {
            Aspects.of(this).add(new IamRoleTransform(this, awsEnv["IamRoleNamePrefix"], awsEnv["PermissionBoundaryArn"]))
        }

        const webAppBuildPath = "../web/build";

        const storageResources = new nestedStorageResourcesBuilder(this, "nestedStorageBuilder");
        const trail = new cloudTrail.Trail(this, 'CloudTrail-VAMS', {
            isMultiRegionTrail: false,
            bucket: storageResources.s3.accessLogsBucket,
            s3KeyPrefix: 'cloudtrail-logs'
        })
        trail.logAllLambdaDataEvents()
        trail.logAllS3DataEvents()

        const cognitoResources = CognitoStack(this, "Cognito", { ...props, storageResources })
        const cognitoUser = CognitoUser(this, cognitoResources);

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

        const apiBuilder = new ApiBuilderNestedStack(this, "NestedAPIBuilder", api, storageResources)
        StackNagSuppression(this)
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
    }
}
