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
import { Service, IAMArn } from "../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import { Stack } from "aws-cdk-lib";
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
    frameSrc?: string[];
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

/**
 * Module-level set to guard against duplicate SSM lookup stack suppressions.
 * Used by globalLambdaEnvironmentsAndPermissions() to apply the SSM parameter
 * wildcard suppression only once per stack, since many Lambda functions in the
 * same stack share the SSM policy grant.
 */
const ssmLookupSuppressedStacks = new Set<string>();

/**
 * Module-level set to guard against duplicate audit log group stack suppressions.
 * Used by setupSecurityAndLoggingEnvironmentAndPermissions() to apply the audit
 * log group wildcard suppression only once per stack, since many Lambda functions
 * in the same stack share the audit logging policy grant.
 */
const auditLogSuppressedStacks = new Set<string>();

/**
 * Whether the API Gateway authorizer Lambda can reach Amazon Cognito to perform the
 * MFA-preference check. The check runs only in the authorizer (its result is passed to
 * handler Lambdas through the authorizer context), so only the authorizer's own VPC
 * placement matters.
 *
 * VAMS does not create Cognito VPC interface endpoints, so an authorizer running inside
 * the VPC has no in-VPC path to Amazon Cognito. The check is therefore enabled only when
 * the authorizer runs outside the VPC — that is, when Lambda functions are not placed in
 * the VPC (`useForAllLambdas`). When the authorizer runs in the VPC the check is disabled
 * regardless of partition, and `mfaRequired` on a role has no effect.
 */
export function isCognitoMfaCheckEnabled(config: Config.Config): boolean {
    const authorizerInVpc =
        config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    return config.app.authProvider.useCognito.enabled && !authorizerInVpc;
}

export function globalLambdaEnvironmentsAndPermissions(
    lambdaFunction: lambda.Function,
    config: Config.Config
) {
    // Resource-name resolution: prefix for SSM Parameter Store lookups
    lambdaFunction.addEnvironment("VAMS_RESOURCE_PARAM_PREFIX", config.resourceNamesSSMParamPrefix);
    const resourceParamPathNoSlash = config.resourceNamesSSMParamPrefix.replace(/^\//, "");
    lambdaFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
            resources: [
                IAMArn(resourceParamPathNoSlash).ssm,
                IAMArn(`${resourceParamPathNoSlash}/*`).ssm,
            ],
        })
    );

    // Apply stack-level CDK Nag suppression for the SSM parameter grant. Stack suppressions
    // are evaluated at check time against every finding in the stack, so they cover synthesis-
    // time resources (OverflowPolicy1, OverflowPolicy2, etc.) that do not exist when per-construct
    // suppressions are applied. Only add once per stack (many Lambdas in the same stack share
    // the SSM policy grant).
    const lambdaStack = Stack.of(lambdaFunction);
    if (!ssmLookupSuppressedStacks.has(lambdaStack.node.addr)) {
        ssmLookupSuppressedStacks.add(lambdaStack.node.addr);
        NagSuppressions.addStackSuppressions(lambdaStack, [
            {
                id: "AwsSolutions-IAM5",
                reason: "Wildcard is scoped to the deployment-specific SSM resource-name parameter prefix (/{name}-{baseStackName}/resourceNames/*) that Lambda functions read at cold start to resolve DynamoDB table, S3 bucket, and CloudWatch log group names.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:ssm:.*:parameter\\/.*\\/resourceNames(\\/\\*)?$/g",
                    },
                ],
            },
        ]);
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
    // Grant read permissions to authentication and authorization tables
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

    // Apply stack-level CDK Nag suppression for the audit log group grant. Stack
    // suppressions are evaluated at check time against every finding in the stack, so
    // they cover synthesis-time resources (OverflowPolicy1, OverflowPolicy2, etc. created
    // when a role's policy document is split) that do not exist when per-construct
    // suppressions are applied. Only add once per stack (many Lambdas in the same stack
    // share the audit logging policy grant).
    const lambdaStack = Stack.of(lambdaFunction);
    if (!auditLogSuppressedStacks.has(lambdaStack.node.addr)) {
        auditLogSuppressedStacks.add(lambdaStack.node.addr);
        NagSuppressions.addStackSuppressions(lambdaStack, [
            {
                id: "AwsSolutions-IAM5",
                reason: "Wildcard is scoped to log streams (:*) within the nine deployment-specific VAMS audit CloudWatch log groups that every Lambda function writes audit events to.",
                appliesTo: [
                    {
                        regex: "/^Resource::<.*AuditLogGroup.*\\.Arn>:\\*$/g",
                    },
                ],
            },
        ]);
    }
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

/**
 * Adds a bucket policy Deny statement restricting where S3 presigned URLs for the
 * bucket may be used from (allowed IP CIDR ranges and/or VPC endpoint IDs). The
 * statement is scoped to query-string-authenticated requests (s3:authType =
 * REST-QUERY-STRING), so SDK header-authenticated calls from Lambda functions and
 * pipeline containers are never affected. No-op when no restrictions are configured.
 * Only applies to VAMS-owned buckets; imported buckets do not receive resource
 * policies from VAMS (the bucket owner applies an equivalent policy — see the
 * external S3 setup documentation).
 */
export function addPresignedUrlNetworkRestrictionsToBucketPolicy(
    bucket: s3.IBucket,
    restrictions: Config.ConfigPresignedUrlNetworkRestrictions | undefined
): void {
    const allowedIpRanges = restrictions?.allowedIpRanges || [];
    const allowedVpceIds = restrictions?.allowedVpceIds || [];
    if (allowedIpRanges.length == 0 && allowedVpceIds.length == 0) {
        return;
    }

    // Conditions AND together: the Deny fires only for a presigned request outside
    // every allowed CIDR AND not through an allowed VPC endpoint (interface or
    // gateway), exempting AWS-service forwarded calls. IP and VPCE conditions are
    // included only when configured so an unconfigured dimension does not deny its
    // entire request class.
    const conditions: { [operator: string]: { [key: string]: string | string[] } } = {
        StringEquals: { "s3:authType": "REST-QUERY-STRING" },
        BoolIfExists: { "aws:ViaAWSService": "false" },
    };
    if (allowedIpRanges.length > 0) {
        conditions["NotIpAddressIfExists"] = { "aws:SourceIp": allowedIpRanges };
    }
    if (allowedVpceIds.length > 0) {
        conditions["StringNotEqualsIfExists"] = { "aws:SourceVpce": allowedVpceIds };
    }

    bucket.addToResourcePolicy(
        new iam.PolicyStatement({
            sid: "DenyPresignedUrlOutsideAllowedNetworks",
            effect: iam.Effect.DENY,
            principals: [new iam.AnyPrincipal()],
            actions: ["s3:*"],
            resources: [`${bucket.bucketArn}/*`],
            conditions: conditions,
        })
    );
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
            Service("EVENTS").Principal,
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
    let connectSrc = [
        "'self'",
        "blob:",
        "data:",
        `https://${apiUrl}`,
        `https://${Service("S3", false).Endpoint}/`,
    ];

    // `'unsafe-inline'` is used intentionally here instead of per-script SHA
    // hashes or a nonce. External viewer plugins (Physna's hosted viewer,
    // etc.) embed inline `<script>` blocks whose contents we do not control
    // and which rev frequently. Maintaining a rolling SHA allowlist for
    // those blocks is unsustainable, and a CSP nonce requires propagating a
    // new value on every page render (which the viewers cannot cooperate
    // with). Note that modern browsers ignore `'unsafe-inline'` whenever a
    // hash or nonce source is present, so this directive only takes effect
    // when neither is used.
    let scriptSrc = ["'self'", "'unsafe-hashes'", "'unsafe-inline'"];

    let workerSrc = ["'self'", "blob:", "data:"];

    let imgSrc = ["'self'", "blob:", "data:", `https://${Service("S3", false).Endpoint}/`];

    let mediaSrc = ["'self'", "blob:", "data:", `https://${Service("S3", false).Endpoint}/`];

    let fontSrc = ["'self'"];
    let styleSrc = ["'self'", "'unsafe-inline'"];

    // frame-src controls what URLs can be loaded into <iframe>s. Without an
    // explicit directive the browser falls back to default-src ('none'),
    // which blocks blob:-URL iframes used by add-on viewers (e.g., the
    // Physna Viewer wraps the Physna-hosted HTML in a sandboxed Blob URL).
    // 'self' plus blob: covers both same-origin iframes and blob-URL iframes.
    let frameSrc = ["'self'", "blob:"];

    //Add cognito
    if (config.app.authProvider.useCognito.enabled) {
        connectSrc.push(`https://${Service("COGNITO_IDP", false).Endpoint}/`);
        connectSrc.push(`https://${Service("COGNITO_IDENTITY", false).Endpoint}/`);
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
        connectSrc.push(`https://maps.${Service("GEO", false).Endpoint}/`);
    }

    // When the Physna add-on is enabled the viewer plugin embeds Physna's
    // hosted viewer URL directly in an `<iframe src>`. Allow that origin
    // in `frame-src` so the iframe isn't blocked by CSP, and in
    // `connect-src` so any auxiliary fetches the VAMS frontend makes
    // against Physna also pass. We add only the origin portion (not the
    // full config URL, which may include a path) because CSP source
    // expressions match on scheme + host + port.
    if (
        config.app.addons?.usePhysnaSync?.enabled &&
        config.app.addons.usePhysnaSync.apiBaseEndpoint
    ) {
        try {
            const physnaUrl = new URL(config.app.addons.usePhysnaSync.apiBaseEndpoint);
            const origin = `${physnaUrl.protocol}//${physnaUrl.host}`;
            connectSrc.push(origin);
            frameSrc.push(origin);
        } catch {
            // Config validation in getConfig() already rejects invalid URLs,
            // so this is defensive — never raise during CSP generation.
        }
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
        frameSrc = mergeCSPSources(frameSrc, additionalCSPConfig.frameSrc);
    }

    const csp =
        `base-uri 'none';` +
        `default-src 'none'; style-src ${styleSrc.join(" ")}; upgrade-insecure-requests;` +
        `connect-src ${connectSrc.join(" ")}; ` +
        `script-src ${scriptSrc.join(" ")}; ` +
        `worker-src ${workerSrc.join(" ")}; ` +
        `img-src ${imgSrc.join(" ")}; ` +
        `media-src ${mediaSrc.join(" ")}; ` +
        `frame-src ${frameSrc.join(" ")}; ` +
        `object-src 'none'; ` +
        // frame-ancestors controls who may embed VAMS pages in a frame. 'self'
        // permits same-origin framing only, which is required by iframe-embedded
        // viewers that load a VAMS-hosted document (e.g. the SuperSplat editor under
        // /viewers/supersplat/). External sites still cannot frame VAMS, so this does
        // not reintroduce clickjacking exposure. Keep in sync with the X-Frame-Options
        // SAMEORIGIN setting on the CloudFront ResponseHeadersPolicy.
        `frame-ancestors 'self'; font-src ${fontSrc.join(" ")}; ` +
        `manifest-src 'self'`;

    return csp;
}

/**
 * Applies the standard CDK Nag suppressions required by every VAMS Lambda function,
 * scoped to the individual function (and its execution role) rather than the whole stack.
 *
 * Previously these three suppressions were applied once at the CoreVAMSStack level with
 * `applyToChildren=true`. Because cdk-nag recurses across nested-stack boundaries, that
 * stamped the suppression metadata onto every resource in every nested stack (S3 buckets,
 * DynamoDB tables, API routes, etc.), bloating the synthesized CloudFormation templates as
 * resources were added. Scoping the suppressions to each Lambda keeps the metadata on only
 * the resources that actually need it, which is the recommended cdk-nag practice.
 *
 * Suppresses:
 * - AwsSolutions-IAM5: wildcard KMS actions (kms:*) for VAMS-generated keys
 * - AwsSolutions-IAM4: AWSLambdaVPCAccessExecutionRole managed policy
 * - AwsSolutions-IAM4: AWSLambdaBasicExecutionRole managed policy
 *
 * @param lambdaFunction The Lambda function to suppress findings for
 */
export function suppressCdkNagLambda(lambdaFunction: lambda.IFunction) {
    NagSuppressions.addResourceSuppressions(
        lambdaFunction,
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
            {
                id: "AwsSolutions-IAM4",
                reason: "Intend to use AWSLambdaVPCAccessExecutionRole as is at this stage of this project.",
                appliesTo: [
                    {
                        regex: "/.*AWSLambdaVPCAccessExecutionRole$/g",
                    },
                ],
            },
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

/**
 * Applies the standard Lambda IAM4/IAM5 suppressions to IAM roles and policies that are
 * generated by CDK framework constructs (custom-resource providers, bucket deployments, bucket
 * notification handlers, AwsCustomResource) or by VAMS custom-resource roles that intentionally
 * use AWS managed execution policies.
 *
 * These resources are not created by the VAMS Lambda builders, so suppressCdkNagLambda() cannot
 * reach them. Previously they were covered by the stack-wide applyToChildren suppression in
 * CoreVAMSStack. To keep the metadata footprint small, this walks the construct tree and applies
 * the suppressions only to the IAM CfnRole/CfnPolicy resources that match well-known framework
 * markers — never to non-IAM resources, and never to the authored-function roles already handled
 * by suppressCdkNagLambda() (which live under a Function construct, not these markers).
 *
 * @param scope The stack (or construct) whose tree should be walked. findAll() recurses into
 *              nested stacks, so calling this once on the core stack covers every nested stack.
 */
export function suppressCdkNagLambdaFrameworkResources(scope: Construct) {
    // Stable path fragments for CDK-generated framework constructs and VAMS custom-resource roles
    // whose execution roles use AWS managed policies (AWSLambdaBasicExecutionRole /
    // AWSLambdaVPCAccessExecutionRole) and/or wildcard KMS actions for VAMS-owned keys.
    const frameworkMarkers = [
        "framework-onEvent",
        "BucketNotificationsHandler",
        "CDKBucketDeployment",
        "AWS679f53fac002430cb0da5b7982bd2287", // CDK AwsCustomResource provider singleton
        "CustomResourcePolicy",
        "lambdaPipelineRole",
        "MetadataSchemaDefaultCustomResourceRole",
        "AuthDefaultCustomResourceRole",
        "CRAuthKmsPolicy",
        "OpensearchProvisionedDeploySchema",
    ];

    scope.node.findAll().forEach((node) => {
        const isRoleOrPolicy =
            node instanceof iam.CfnRole ||
            node instanceof iam.CfnPolicy ||
            node instanceof iam.Role ||
            node instanceof iam.Policy;
        if (!isRoleOrPolicy) {
            return;
        }

        if (!frameworkMarkers.some((marker) => node.node.path.includes(marker))) {
            return;
        }

        NagSuppressions.addResourceSuppressions(
            node,
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
                {
                    id: "AwsSolutions-IAM5",
                    reason: "CDK framework roles (BucketDeployment uploader, custom-resource providers) require wildcard S3 object access on the CDK staging + destination buckets to stage assets, and a function-qualifier ':*' wildcard to invoke their target Lambda. Scope is the framework resource's own generated policy.",
                    appliesTo: [
                        {
                            regex: "/^Action::s3:(.*)\\*$/g",
                        },
                        {
                            regex: "/^Resource::.*\\*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Intend to use AWSLambdaVPCAccessExecutionRole as is at this stage of this project.",
                    appliesTo: [
                        {
                            regex: "/.*AWSLambdaVPCAccessExecutionRole$/g",
                        },
                    ],
                },
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
    });
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
 * Grants a principal access to the customer managed KMS keys of any external asset
 * buckets that declare one. The bucket grant covers S3 object actions, but a bucket
 * encrypted with a key the VAMS account does not own additionally requires explicit
 * KMS permissions on that external key (the VAMS-owned key grant does not cover it).
 * No-op for buckets without an external key, so same-account/SSE-managed setups are
 * unaffected.
 * @param grantable The IAM grantable (lambda function or role) to grant permissions to
 */
export function grantExternalAssetBucketKmsKeys(grantable: iam.IGrantable): void {
    const externalKeyArns = Array.from(
        new Set(
            s3AssetBuckets
                .getS3AssetBucketRecords()
                .map((record) => record.kmsKeyArn)
                .filter((keyArn): keyArn is string => !!keyArn)
        )
    );

    if (externalKeyArns.length > 0) {
        grantable.grantPrincipal.addToPrincipalPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"],
                resources: externalKeyArns,
            })
        );
    }
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

    // Grant external bucket KMS keys (no-op when no external keys are configured)
    grantExternalAssetBucketKmsKeys(lambdaFunction);

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

    // Grant external bucket KMS keys (no-op when no external keys are configured)
    grantExternalAssetBucketKmsKeys(lambdaFunction);

    // Add CDK Nag suppressions
    //suppressCdkNagErrorsByGrantReadWrite(lambdaFunction);
}
