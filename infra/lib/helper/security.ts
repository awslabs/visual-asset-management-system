/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as crypto from "crypto";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as Config from "../../config/config";
import { Construct } from "constructs";
import { Service } from "../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

export function requireTLSAndAdditionalPolicyAddToResourcePolicy(
    bucket: s3.Bucket,
    config: Config.Config
) {
    bucket.addToResourcePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.DENY,
            principals: [new iam.AnyPrincipal()],
            actions: ["s3:*"],
            resources: [`${bucket.bucketArn}/*`, bucket.bucketArn],
            conditions: {
                Bool: { "aws:SecureTransport": "false" },
            },
        })
    );

    if (config.s3AdditionalBucketPolicyJSON && config.s3AdditionalBucketPolicyJSON != undefined) {
        //Update policy statement for the current bucket resources
        const policyStatementJSON = config.s3AdditionalBucketPolicyJSON;
        policyStatementJSON.Resource = [`${bucket.bucketArn}/*`, bucket.bucketArn];

        //console.log(policyStatementJSON)
        //console.log(iam.PolicyStatement.fromJson(policyStatementJSON).toJSON())

        bucket.addToResourcePolicy(iam.PolicyStatement.fromJson(policyStatementJSON));
    }
}

export function kmsKeyLambdaPermissionAddToResourcePolicy(
    lambdaFunction: lambda.Function,
    kmsKey?: kms.IKey
) {
    if (kmsKey) {
        lambdaFunction.addToRolePolicy(kmsKeyPolicyStatementGenerator(kmsKey));
    }
}

export function kmsKeyPolicyStatementGenerator(kmsKey?: kms.IKey): iam.PolicyStatement {
    if (!kmsKey) {
        throw new Error("Cannot generate policy statement for KMS key if no KMS key provided.");
    }

    return new iam.PolicyStatement({
        actions: [
            "kms:Decrypt",
            "kms:DescribeKey",
            "kms:Encrypt",
            "kms:GenerateDataKey*",
            "kms:ReEncrypt*",
            "kms:ListKeys",
            "kms:CreateGrant",
            "kms:ListAliases",
        ],
        effect: iam.Effect.ALLOW,
        resources: [kmsKey.keyArn],
    });
}

export function kmsKeyPolicyStatementPrincipalGenerator(
    config: Config.Config,
    kmsKey?: kms.IKey
): iam.PolicyStatement {
    if (!kmsKey) {
        throw new Error("Cannot generate policy statement for KMS key if no KMS key provided.");
    }

    const policyStatement = new iam.PolicyStatement({
        actions: [
            "kms:GenerateDataKey*",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:DescribeKey",
            "kms:ListKeys",
            "kms:CreateGrant",
            "kms:ListAliases",
        ],
        effect: iam.Effect.ALLOW,
        principals: [
            Service("S3").Principal,
            Service("DYNAMODB").Principal,
            Service("SQS").Principal,
            Service("SNS").Principal,
            Service("ECS").Principal,
            Service("ECS_TASKS").Principal,
            Service("LOGS").Principal,
            Service("LAMBDA").Principal,
            Service("STS").Principal,
        ],
    });

    if (!config.app.useAlb.enabled) {
        policyStatement.addPrincipals(Service("CLOUDFRONT").Principal);
    }

    if (config.app.openSearch.useProvisioned.enabled) {
        policyStatement.addPrincipals(Service("ES").Principal);
    }

    if (config.app.openSearch.useServerless.enabled) {
        policyStatement.addPrincipals(Service("AOSS").Principal);
    }

    return policyStatement;
}

export function generateUniqueNameHash(
    stackName: string,
    accountId: string,
    resourceIdentifier: string,
    maxLength = 32
) {
    const hash = crypto.getHashes();
    const hashPwd = crypto
        .createHash("sha1")
        .update(stackName + accountId + resourceIdentifier)
        .digest("hex")
        .toString()
        .toLowerCase();
    return hashPwd.substring(0, maxLength);
}

export function generateContentSecurityPolicy(
    storageResources: storageResources,
    authenticationDomain: string,
    apiUrl: string,
    config: Config.Config
): string {
    const connectSrc = [
        "'self'",
        "blob:",
        authenticationDomain,
        `https://${Service("COGNITO_IDP").Endpoint}/`,
        `https://${Service("COGNITO_IDENTITY").Endpoint}/`,
        `https://${apiUrl}`,
        //`https://${props.storageResources.s3.assetBucket.bucketRegionalDomainName}/`, //Virtual Host Format Connection
        //`https://${props.storageResources.s3.assetBucket.bucketDomainName}/`, //Virtual Host Format Connection
        `https://${Service("S3").PrincipalString}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
        `https://${Service("S3").Endpoint}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
    ];

    //Add GeoLocation service URL if feature turned on
    if (config.app.useLocationService.enabled) {
        connectSrc.push(`https://maps.${Service("GEO").Endpoint}/`);
    }

    const scriptSrc = [
        "'self'",
        "blob:",
        "'sha256-fUpTbA+CO0BMxLmoVHffhbh3ZTLkeobgwlFl5ICCQmg='", // script in index.html
        authenticationDomain,
        `https://${Service("COGNITO_IDP").Endpoint}/`,
        `https://${Service("COGNITO_IDENTITY").Endpoint}/`,
        `https://${apiUrl}`,
        //`https://${props.storageResources.s3.assetBucket.bucketRegionalDomainName}/`, //Virtual Host Format Connection
        `https://${Service("S3").PrincipalString}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
        `https://${Service("S3").Endpoint}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
    ];

    const imgMediaSrc = [
        "'self'",
        "blob:",
        "data:",
        //`https://${props.storageResources.s3.assetBucket.bucketRegionalDomainName}/`, //Virtual Host Format Connection
        `https://${Service("S3").PrincipalString}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
        `https://${Service("S3").Endpoint}/${storageResources.s3.assetBucket.bucketName}/`, //Path Addressable Format Connection
    ];

    const csp =
        `default-src 'none'; style-src 'self' 'unsafe-inline'; ` +
        `connect-src ${connectSrc.join(" ")}; ` +
        `script-src ${scriptSrc.join(" ")}; ` +
        `img-src ${imgMediaSrc.join(" ")}; ` +
        `media-src ${imgMediaSrc.join(" ")}; ` +
        `object-src 'none'; ` +
        `frame-ancestors 'none'; font-src 'self'; ` +
        `manifest-src 'self'`;

    return csp;
}

export function suppressCdkNagErrorsByGrantReadWrite(scope: Construct) {
    const reason =
        "This lambda owns the data in this bucket and should have full access to control its assets.";
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: reason,
                appliesTo: [
                    {
                        regex: "/Action::s3:.*/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason: reason,
                appliesTo: [
                    {
                        // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                        regex: "/^Resource::.*/g",
                    },
                ],
            },
        ],
        true
    );
}
