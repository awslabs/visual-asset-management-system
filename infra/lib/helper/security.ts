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
import { readFileSync, rmSync } from "fs";
import { join } from "path";
import { INDEX_HTML_INLINE_SCRIPT_HASHES } from "./cspInlineScriptHashes";

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
 * `'unsafe-inline'` spellings refused in a `script-src` addition. Inline scripts are allowed
 * by SHA-256 hash, and a source list carrying a hash source never allows all inline script —
 * browsers ignore the keyword in that list — so the keyword grants nothing while making the
 * emitted policy read as though it did. Sources that widen `script-src` without touching the
 * hash sources are merged as given, including script origins and `'unsafe-eval'`, which
 * `app.webUi.allowUnsafeEvalFeatures` also controls.
 */
const SCRIPT_SRC_UNSAFE_INLINE_TOKENS = ["'unsafe-inline'", "unsafe-inline"];

/**
 * Returns the `'unsafe-inline'` token in a `script-src` addition, or undefined when it carries
 * none. An entry may hold a whole space-separated source list, each token of which the browser
 * parses on its own, so every token is checked; CSP keyword sources are ASCII case-insensitive.
 */
function findUnsafeInlineToken(entry: string): string | undefined {
    return entry
        .trim()
        .split(/\s+/)
        .find((token) => SCRIPT_SRC_UNSAFE_INLINE_TOKENS.includes(token.toLowerCase()));
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
                        const unsafeInline =
                            key === "scriptSrc" ? findUnsafeInlineToken(entry) : undefined;
                        if (unsafeInline) {
                            console.warn(
                                `CSP additional config: ${unsafeInline} in scriptSrc is not accepted, ` +
                                    `skipping entry "${entry}". Inline scripts are allowed by SHA-256 hash, ` +
                                    `and browsers ignore ${unsafeInline} in a source list that carries a hash, ` +
                                    `so the keyword permits nothing. Hash the script instead ` +
                                    `(web/scripts/cspInlineScriptHashes.js).`
                            );
                            return false;
                        }
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
 * Merges additional CSP sources with existing sources, avoiding duplicates.
 * Additional sources arrive already screened by loadCSPAdditionalConfig().
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

    // Optional default role granted to authenticated users with no assigned role
    // (used by the Casbin enforcer). Empty string disables the behavior.
    lambdaFunction.addEnvironment(
        "DEFAULT_ROLE_NAME",
        config.app.authProvider.authorizerOptions?.defaultUserRoleName || ""
    );
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

    // Inline scripts in index.html are allowed by SHA-256 hash rather than by
    // `'unsafe-inline'`, so an injected inline script is still blocked. The
    // hashes cover the exact bytes of each block's text content, including
    // indentation, so they are generated rather than hand-written:
    //
    //     cd web && npm run build && node scripts/cspInlineScriptHashes.js --ts
    //
    // Regenerate whenever an inline block in web/index.html changes — a
    // Prettier run over that file is enough to invalidate them. The value is
    // asserted against the built HTML by web/scripts/cspInlineScriptHashes.js
    // and by the CSP hash test, so drift fails a test rather than the browser.
    //
    // The list covers web/index.html only. This policy is a response header on
    // the whole distribution, so it governs every HTML document served from
    // web/public as well; an inline block in one of those (the SuperSplat
    // viewer's upstream service-worker registration) has no hash here and does
    // not run. A served document that needs its own inline script needs its own
    // hashes, taken from that document.
    //
    // `'wasm-unsafe-eval'` permits WebAssembly compilation for the WASM-based
    // viewer plugins. It is not an inline-script keyword, so it neither relies
    // on nor competes with the hash sources; the broader `'unsafe-eval'`, which
    // those viewers' JavaScript loaders still require, stays gated on
    // `allowUnsafeEvalFeatures` below.
    let scriptSrc = [
        "'self'",
        "'unsafe-hashes'",
        "'wasm-unsafe-eval'",
        ...INDEX_HTML_INLINE_SCRIPT_HASHES,
    ];

    let workerSrc = ["'self'", "blob:", "data:"];

    let imgSrc = ["'self'", "blob:", "data:", `https://${Service("S3", false).Endpoint}/`];

    let mediaSrc = ["'self'", "blob:", "data:", `https://${Service("S3", false).Endpoint}/`];

    let fontSrc = ["'self'"];
    let styleSrc = ["'self'", "'unsafe-inline'"];

    // frame-src controls what URLs can be loaded into <iframe>s. Without an
    // explicit directive the browser falls back to default-src ('none'), which
    // blocks every iframe. 'self' covers the VAMS-hosted iframe viewers (e.g.
    // the SuperSplat editor under /viewers/supersplat/) and blob: covers
    // blob-URL iframes. The Amazon S3 endpoint is here for the same reason it is
    // on img-src and media-src above: the HTML viewer frames an asset file by its
    // presigned S3 URL, and without the origin the frame is blocked and the panel
    // renders empty. A viewer that frames a third-party document needs that
    // document's origin added, as the Physna add-on branch below does.
    let frameSrc = ["'self'", "blob:", `https://${Service("S3", false).Endpoint}/`];

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

        // The add-on relaxes no script-src source. The viewer's `<iframe src>`
        // is Physna's own HTTPS origin, so that document loads under Physna's
        // CSP and its inline scripts are outside this policy's reach — the
        // frame-src and connect-src origins above are all it needs from here.
        // `'unsafe-inline'` would have no effect on it, and none on a VAMS page
        // either: a source list that carries a hash source never allows all
        // inline script, so browsers ignore the keyword wherever it appears.
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

/*
 * Shared CDK Nag suppression reasons.
 *
 * Rule 4 requires a reason to justify WHY a finding is acceptable in VAMS, not to restate the rule.
 * These replace a set of placeholders that did the latter — "Intend to use AWSLambdaBasicExecutionRole
 * as is at this stage of this project", "The IAM role for ECS Container execution uses AWS Managed
 * Policies" — which tell a reviewer nothing they could not read off the finding itself.
 *
 * They are CONSTANTS, and each value is deliberately ONE SHORT SENTENCE, because a reason is not free:
 * cdk-nag stamps it onto the metadata of every resource the suppression covers, and the shared grant
 * suppressions are applied with `applyToChildren` over whole stacks, so the text is multiplied by the
 * resource count. Replacing one catch-all entry with six verbose ones once moved the apiBuilder template
 * from ~0.40 MB to ~0.57 MB against a 1 MB per-template ceiling. The reasoning therefore lives in these
 * doc comments, which cost no template bytes, and only the one-sentence conclusion is emitted.
 */

/**
 * `AWSLambdaBasicExecutionRole` — acceptable because its entire content is CloudWatch Logs write.
 *
 * The policy grants exactly `logs:CreateLogGroup`, `logs:CreateLogStream` and `logs:PutLogEvents`. Every
 * VAMS handler writes logs, and the alternative is a hand-rolled per-function policy granting the same
 * three actions, which is strictly more code for the same access.
 */
export const NAG_REASON_LAMBDA_BASIC_EXECUTION =
    "Grants only CloudWatch Logs create/put, which every VAMS handler needs.";

/**
 * `AWSLambdaVPCAccessExecutionRole` — acceptable because its content is ENI management only.
 *
 * Adds `ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces` and `DeleteNetworkInterface`. A Lambda
 * attached to the VAMS VPC cannot start without them, and they act on the function's own ENIs.
 */
export const NAG_REASON_LAMBDA_VPC_ACCESS =
    "Grants only the ENI create/describe/delete a VPC-attached Lambda needs.";

/**
 * `AmazonECSTaskExecutionRolePolicy` + `AWSXrayWriteOnlyAccess` on a container role.
 *
 * The first grants the ECR image pull and the CloudWatch Logs write that the ECS agent performs before
 * the container runs — a task cannot start without it. The second grants X-Ray segment write only. This
 * is the ECS agent's own access, not the container's: a container's AWS calls are signed with the task
 * role, which carries the deployment's scoped bucket and Step Functions policies instead.
 */
export const NAG_REASON_ECS_TASK_EXECUTION_MANAGED =
    "Grants the ECR pull and Logs write the ECS agent needs to start a task; X-Ray is segment-write only.";

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
                reason: NAG_REASON_LAMBDA_VPC_ACCESS,
                appliesTo: [
                    {
                        regex: "/.*AWSLambdaVPCAccessExecutionRole$/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM4",
                reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
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
                    reason: NAG_REASON_LAMBDA_VPC_ACCESS,
                    appliesTo: [
                        {
                            regex: "/.*AWSLambdaVPCAccessExecutionRole$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
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

/**
 * Suppresses the `Resource::*` finding that `Table.grantStreamRead()` necessarily produces.
 *
 * CDK renders that grant as two statements: the stream ARN actions, scoped to the table's stream, and
 * `dynamodb:ListStreams` on `*`. ListStreams enumerates the streams in the account and publishes no
 * resource to scope to, so the wildcard is the only form Amazon DynamoDB accepts for it.
 *
 * Opt-in per function rather than folded into the shared grant suppression, because a bare
 * `Resource::*` covers every action on every resource — a handler that acquires an unrelated wildcard
 * later should surface at synth, not inherit this one.
 */
export function suppressCdkNagDynamoStreamListWildcard(lambdaFunction: lambda.IFunction) {
    NagSuppressions.addResourceSuppressions(
        lambdaFunction,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Table.grantStreamRead() adds dynamodb:ListStreams, which enumerates the account's " +
                    "streams and supports no resource-level permissions, so Resource must be '*'. The " +
                    "stream read actions in the same grant are scoped to the table's own stream ARN.",
                appliesTo: [{ regex: "/^Resource::\\*$/g" }],
            },
        ],
        true
    );
}

/**
 * Suppresses the `Resource::*` finding for `ecr:GetAuthorizationToken`.
 *
 * The action exchanges the caller's IAM identity for a registry credential. It acts on the account's
 * registry rather than on a repository, publishes no resource, and is documented as requiring `*`;
 * every task or build that pulls from Amazon ECR needs it. The repository-scoped pull actions that
 * accompany it are granted on the repository ARN.
 */
export function suppressCdkNagEcrAuthTokenWildcard(scope: Construct) {
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "ecr:GetAuthorizationToken acts on the account's registry, not on a repository, and " +
                    "supports no resource-level permissions, so Resource must be '*'. The pull actions " +
                    "granted alongside it are scoped to the repository ARN.",
                appliesTo: [{ regex: "/^Resource::\\*$/g" }],
            },
        ],
        true
    );
}

/**
 * Suppresses the resource wildcards that CDK's own `grant*` helpers produce, each shape justified on
 * its own terms.
 *
 * This used to carry a single catch-all entry — a regex matching every `Resource::` finding — which
 * suppressed every IAM5 resource-wildcard finding under the scope it was given. 58 of its 76 call
 * sites passed the nested stack, so the
 * repository's primary IAM guardrail was off for most of the Lambda roles in the deployment. Measured
 * against a real `cdk synth` of the commercial configuration, that one entry was hiding 148 findings.
 *
 * The four shapes below are the ones a CDK grant emits and cannot avoid emitting: the caller asks for
 * access to a bucket, a table, a function or a callback and CDK renders the wildcard the API requires.
 * Listing them separately is what makes the suppression a statement rather than a blanket — a wildcard
 * of any OTHER shape now surfaces at synth, which is the property the blanket removed.
 *
 * Measured counts, commercial configuration: 65 S3 object ARNs, 34 Lambda version ARNs, 17 Step
 * Functions execution ARNs, 14 DynamoDB index ARNs.
 */
export function suppressCdkNagErrorsByGrantReadWrite(scope: Construct) {
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "This lambda needs access to the data in this bucket and should have full access to control its assets.",
                appliesTo: [
                    {
                        regex: "/Action::s3:.*/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Object ARN of one named bucket: a bucket grant covers its objects, and Amazon S3 " +
                    "expresses that only as the bucket ARN plus a key wildcard.",
                appliesTo: [
                    {
                        // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                        regex: "/^Resource::<.*Bucket.*\\.Arn>/\\*$/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Index ARNs of one named table: a table grant covers its global secondary indexes, " +
                    "and a GSI query is denied without them.",
                appliesTo: [
                    {
                        regex: "/^Resource::<.*Table.*\\.Arn>/index/\\*$/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Qualifier ARNs of one named function: grantInvoke covers its versions and aliases, " +
                    "which reaches no other function.",
                appliesTo: [
                    {
                        regex: "/^Resource::<.*\\.Arn>:\\*$/g",
                    },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Per-asset topic AssetTopic<assetId>, created at runtime. Account and Region are " +
                    "pinned; the wildcard is over the asset id only.",
                appliesTo: [{ regex: "/^Resource::arn:.*:sns:.*:AssetTopic\\*$/g" }],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Task-token callback: the execution holding the token is created per run, so its " +
                    "ARN is unknown at synthesis. Scoped to this account and Region.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:states:.*:\\*$/g",
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

/**
 * Remove a temporary directory that carried a secret into a CDK code asset.
 *
 * `lambda.Code.fromAsset()` stages its source into the cloud assembly while the construct is being
 * created, so by the time the Function exists the staged copy is what will be uploaded and the source
 * directory has no further purpose. Left behind it is a cleartext credential on the build host, one
 * per synth: over a thousand such directories accumulated on a single developer machine, and a CI
 * runner keeps them for the life of its workspace.
 *
 * Failure to remove it is deliberately NOT fatal. This runs during synth of a stack that is otherwise
 * complete and correct, and on Windows a directory can be transiently locked by an indexer or a virus
 * scanner; aborting the deployment over a temp-file cleanup would trade a small exposure for a total
 * outage. The warning names the path so it can be removed by hand.
 */
export function discardStagedSecretAsset(assetDir: string) {
    try {
        rmSync(assetDir, { recursive: true, force: true });
    } catch (err) {
        console.warn(
            `Warning: could not remove the temporary secret asset directory ${assetDir}. It holds ` +
                `the credential in cleartext — delete it by hand. (${err})`
        );
    }
}

/**
 * The SSM parameter ARNs the OpenSearch schema-deploy custom resource is allowed to write.
 *
 * Exactly the three parameters `schemaDeploy/deployschema.ts` issues `PutParameterCommand` for — the
 * domain endpoint and the two index names. It reads nothing, so no Get action is granted.
 *
 * Two details this encodes so neither construct has to:
 *
 *  - The configured names carry a LEADING SLASH and `IAMArn(...).ssm` already supplies the separator
 *    after `parameter`, so the slash is stripped. `parameter//name` matches nothing, which would leave
 *    a grant that authorizes no parameter at all and a schema deploy that fails at the first write.
 *  - A name that is unset is SKIPPED rather than dereferenced. `getConfig()` always sets all three, but
 *    a construct that throws a TypeError on a partial config breaks every test harness that builds one
 *    by hand — and a grant for a parameter the handler was not given is not needed anyway, because the
 *    handler only writes the names passed into its resource properties.
 */
export function schemaDeploySsmParameterArns(config: Config.Config): string[] {
    return [
        config.openSearchDomainEndpointSSMParam,
        config.openSearchAssetIndexNameSSMParam,
        config.openSearchFileIndexNameSSMParam,
    ]
        .filter((param): param is string => typeof param === "string" && param.length > 0)
        .map((param) => IAMArn(param.replace(/^\/+/, "")).ssm);
}
