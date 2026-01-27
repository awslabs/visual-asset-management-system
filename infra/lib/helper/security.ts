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
import * as s3AssetBuckets from "./s3AssetBuckets";
import { readFileSync } from "fs";
import { join } from "path";

/**
 * Interface for additional CSP configuration
 */
interface CSPAdditionalConfig {
    connectSrc?: string[];
    scriptSrc?: string[];
    workerSrc?: string[];
    imgSrc?: string[];
    mediaSrc?: string[];
    fontSrc?: string[];
    styleSrc?: string[];
}

/**
 * Loads additional CSP configuration from JSON file with proper error handling
 * @returns CSPAdditionalConfig object or undefined if file doesn't exist or is invalid
 */
function loadCSPAdditionalConfig(): CSPAdditionalConfig | undefined {
    try {
        const configPath = join(__dirname, "../../config/csp/cspAdditionalConfig.json");
        const fileContent = readFileSync(configPath, { encoding: "utf8", flag: "r" });

        if (!fileContent || fileContent.trim().length === 0) {
            console.log(
                "CSP additional config file is empty, using default CSP configuration only"
            );
            return undefined;
        }

        const config: CSPAdditionalConfig = JSON.parse(fileContent);

        // Validate that the config is an object
        if (typeof config !== "object" || config === null) {
            console.warn(
                "CSP additional config is not a valid object, using default CSP configuration only"
            );
            return undefined;
        }

        // Filter out invalid entries (non-strings) and log warnings
        const validatedConfig: CSPAdditionalConfig = {};

        for (const [key, value] of Object.entries(config)) {
            if (Array.isArray(value)) {
                const validEntries = value.filter((entry) => {
                    if (typeof entry === "string" && entry.trim().length > 0) {
                        return true;
                    } else {
                        console.warn(
                            `CSP additional config: Invalid entry "${entry}" in ${key}, skipping`
                        );
                        return false;
                    }
                });

                if (validEntries.length > 0) {
                    validatedConfig[key as keyof CSPAdditionalConfig] = validEntries;
                }
            } else if (value !== undefined && value !== null) {
                console.warn(`CSP additional config: ${key} should be an array, skipping`);
            }
        }

        console.log("CSP additional config loaded successfully");
        return validatedConfig;
    } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") {
            console.log(
                "CSP additional config file not found, using default CSP configuration only"
            );
        } else if (error instanceof SyntaxError) {
            console.warn(
                "CSP additional config file contains invalid JSON, using default CSP configuration only:",
                error.message
            );
        } else {
            console.warn(
                "Error loading CSP additional config, using default CSP configuration only:",
                error
            );
        }
        return undefined;
    }
}

/**
 * Merges additional CSP sources with existing sources, avoiding duplicates
 * @param existingSources Current CSP sources array
 * @param additionalSources Additional sources to merge
 * @returns Merged array without duplicates
 */
function mergeCSPSources(existingSources: string[], additionalSources?: string[]): string[] {
    if (!additionalSources || additionalSources.length === 0) {
        return existingSources;
    }

    const merged = [...existingSources];

    for (const source of additionalSources) {
        if (!merged.includes(source)) {
            merged.push(source);
        }
    }

    return merged;
}

export function globalLambdaEnvironmentsAndPermissions(
    lambdaFunction: lambda.Function,
    config: Config.Config
) {
    //We don't want to enable cognito for lambdas as any lambda behind a VPC isolated subnet won't be able to conduct cognito auth calls
    //This will disable MFA check capabilities for these scenarios
    if (
        config.app.authProvider.useCognito.enabled &&
        !(
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) ||
            config.app.openSearch.useProvisioned.enabled
        )
    ) {
        lambdaFunction.addEnvironment("COGNITO_AUTH_ENABLED", "TRUE");
    } else {
        lambdaFunction.addEnvironment("COGNITO_AUTH_ENABLED", "FALSE");
    }
}

/**
 * Sets up common security and logging environment variables and permissions for Lambda functions.
 * This includes authentication and authorization tables required for all Lambda functions to perform
 * global authorization and authentication operations.
 *
 * @param lambdaFunction The Lambda function to configure
 * @param storageResources The storage resources object containing DynamoDB table references
 */
export function setupSecurityAndLoggingEnvironmentAndPermissions(
    lambdaFunction: lambda.Function,
    storageResources: storageResources
): void {
    // Add authentication and authorization environment variables
    lambdaFunction.addEnvironment(
        "AUTH_TABLE_NAME",
        storageResources.dynamo.authEntitiesStorageTable.tableName
    );
    lambdaFunction.addEnvironment(
        "CONSTRAINTS_TABLE_NAME",
        storageResources.dynamo.constraintsStorageTable.tableName
    );
    lambdaFunction.addEnvironment(
        "USER_ROLES_TABLE_NAME",
        storageResources.dynamo.userRolesStorageTable.tableName
    );
    lambdaFunction.addEnvironment(
        "ROLES_TABLE_NAME",
        storageResources.dynamo.rolesStorageTable.tableName
    );

    // Add CloudWatch audit log group environment variables
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_AUTHENTICATION",
        storageResources.cloudWatchAuditLogGroups.authentication.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_AUTHORIZATION",
        storageResources.cloudWatchAuditLogGroups.authorization.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_FILEUPLOAD",
        storageResources.cloudWatchAuditLogGroups.fileUpload.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_FILEDOWNLOAD",
        storageResources.cloudWatchAuditLogGroups.fileDownload.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_FILEDOWNLOAD_STREAMED",
        storageResources.cloudWatchAuditLogGroups.fileDownloadStreamed.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_AUTHOTHER",
        storageResources.cloudWatchAuditLogGroups.authOther.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_AUTHCHANGES",
        storageResources.cloudWatchAuditLogGroups.authChanges.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_ACTIONS",
        storageResources.cloudWatchAuditLogGroups.actions.logGroupName
    );
    lambdaFunction.addEnvironment(
        "AUDIT_LOG_ERRORS",
        storageResources.cloudWatchAuditLogGroups.errors.logGroupName
    );

    // Grant read permissions to authentication and authorization tables
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(lambdaFunction);
    storageResources.dynamo.constraintsStorageTable.grantReadData(lambdaFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(lambdaFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(lambdaFunction);

    // Grant CloudWatch Logs permissions for audit logging
    lambdaFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
            resources: [
                `${storageResources.cloudWatchAuditLogGroups.authentication.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.authorization.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.fileUpload.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.fileDownload.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.fileDownloadStreamed.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.authOther.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.authChanges.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.actions.logGroupArn}:*`,
                `${storageResources.cloudWatchAuditLogGroups.errors.logGroupArn}:*`,
            ],
        })
    );
}

export function requireTLSAndAdditionalPolicyAddToResourcePolicy(
    bucket: s3.IBucket,
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
    lambdaFunction: lambda.IFunction,
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
            "kms:Encrypt",
        ],
        effect: iam.Effect.ALLOW,
        principals: [
            Service("S3").Principal,
            Service("DYNAMODB").Principal,
            Service("SQS").Principal,
            Service("SNS").Principal,
            Service("ECS").Principal,
            Service("EKS").Principal,
            Service("ECS_TASKS").Principal,
            Service("LOGS").Principal,
            Service("LAMBDA").Principal,
            Service("STS").Principal,
            Service("CLOUDFORMATION").Principal,
        ],
        resources: ["*"],
    });

    // Add account root principal for custom resource Lambda roles and CloudFormation
    policyStatement.addPrincipals(new iam.AccountRootPrincipal());

    if (config.app.useCloudFront.enabled) {
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
    // Load additional CSP configuration
    const additionalCSPConfig = loadCSPAdditionalConfig();

    // Base CSP sources
    let connectSrc = ["'self'", "blob:", `https://${apiUrl}`, `https://${Service("S3").Endpoint}/`];

    let scriptSrc = [
        "'self'",
        "'unsafe-hashes'",
        "'sha256-fUpTbA+CO0BMxLmoVHffhbh3ZTLkeobgwlFl5ICCQmg='", // script in index.html
        "'sha256-6oQux02QVJA9KvFQfSp/V7vUxwoN+61rtrKSUpL3rjM='", // script in index.html
    ];

    let workerSrc = ["'self'", "blob:", "data:"];

    let imgSrc = ["'self'", "blob:", "data:", `https://${Service("S3").Endpoint}/`];

    let mediaSrc = ["'self'", "blob:", "data:", `https://${Service("S3").Endpoint}/`];

    let fontSrc = ["'self'"];
    let styleSrc = ["'self'", "'unsafe-inline'"];

    //Add cognito
    if (config.app.authProvider.useCognito.enabled) {
        connectSrc.push(`https://${Service("COGNITO_IDP").Endpoint}/`);
        connectSrc.push(`https://${Service("COGNITO_IDENTITY").Endpoint}/`);
    }

    //If authDomain is non-null and not empty string, add to connectSrc
    if (authenticationDomain && authenticationDomain != "") {
        connectSrc.push(authenticationDomain);
    }

    //Add unsafe eval when enabled
    if (config.app.webUi.allowUnsafeEvalFeatures) {
        scriptSrc.push(`'unsafe-eval'`);
    }

    //Add GeoLocation service URL if feature turned on
    if (config.app.useLocationService.enabled) {
        connectSrc.push(`https://maps.${Service("GEO").Endpoint}/`);
    }

    // Merge additional CSP sources if configuration is loaded
    if (additionalCSPConfig) {
        connectSrc = mergeCSPSources(connectSrc, additionalCSPConfig.connectSrc);
        scriptSrc = mergeCSPSources(scriptSrc, additionalCSPConfig.scriptSrc);
        workerSrc = mergeCSPSources(workerSrc, additionalCSPConfig.workerSrc);
        imgSrc = mergeCSPSources(imgSrc, additionalCSPConfig.imgSrc);
        mediaSrc = mergeCSPSources(mediaSrc, additionalCSPConfig.mediaSrc);
        fontSrc = mergeCSPSources(fontSrc, additionalCSPConfig.fontSrc);
        styleSrc = mergeCSPSources(styleSrc, additionalCSPConfig.styleSrc);
    }

    const csp =
        `base-uri 'none';` +
        `default-src 'none'; style-src ${styleSrc.join(" ")}; upgrade-insecure-requests;` +
        `connect-src ${connectSrc.join(" ")}; ` +
        `script-src ${scriptSrc.join(" ")}; ` +
        `worker-src ${workerSrc.join(" ")}; ` +
        `img-src ${imgSrc.join(" ")}; ` +
        `media-src ${mediaSrc.join(" ")}; ` +
        `object-src 'none'; ` +
        `frame-ancestors 'none'; font-src ${fontSrc.join(" ")}; ` +
        `manifest-src 'self'`;

    return csp;
}

export function suppressCdkNagErrorsByGrantReadWrite(scope: Construct) {
    const reason =
        "This lambda needs access to the data in this bucket and should have full access to control its assets.";
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

/**
 * Grants read permissions to a lambda function for all asset buckets defined in s3AssetBuckets
 * @param lambdaFunction The lambda function to grant permissions to
 */
export function grantReadPermissionsToAllAssetBuckets(lambdaFunction: lambda.Function): void {
    const bucketRecords = s3AssetBuckets.getS3AssetBucketRecords();

    for (const record of bucketRecords) {
        record.bucket.grantRead(lambdaFunction);
    }

    // // Add CDK Nag suppressions
    // const reason = "Lambda needs read access to all asset buckets to perform its operations";
    // NagSuppressions.addResourceSuppressions(
    //     lambdaFunction,
    //     [
    //         {
    //             id: "AwsSolutions-IAM5",
    //             reason: reason,
    //             appliesTo: [
    //                 {
    //                     regex: "/Action::s3:Get.*/g",
    //                 },
    //                 {
    //                     regex: "/Action::s3:List.*/g",
    //                 },
    //             ],
    //         },
    //     ],
    //     true
    // );
}

/**
 * Grants read/write permissions to a lambda function for all asset buckets defined in s3AssetBuckets
 * @param lambdaFunction The lambda function to grant permissions to
 */
export function grantReadWritePermissionsToAllAssetBuckets(lambdaFunction: lambda.Function): void {
    const bucketRecords = s3AssetBuckets.getS3AssetBucketRecords();

    for (const record of bucketRecords) {
        record.bucket.grantReadWrite(lambdaFunction);
    }

    // Add CDK Nag suppressions
    //suppressCdkNagErrorsByGrantReadWrite(lambdaFunction);
}
