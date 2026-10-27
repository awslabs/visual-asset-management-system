/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as Config from "../../config/config";

/**
 * The deployment's origin verification secret, as the stacks that consume it receive it.
 *
 * The secret is created by `ApiOriginVerifyStack` (`lib/apiOriginVerify-stack.ts`) on CloudFront
 * deployments only, and is read by the CloudFront origins, the regional web ACL, the REST authorizer
 * and the rotation function. `kmsKeyArn` is the customer managed key the secret is encrypted with, or
 * undefined when it uses the AWS managed `aws/secretsmanager` key.
 */
export interface ApiOriginVerifySecretRef {
    readonly secretArn: string;
    readonly kmsKeyArn?: string;
}

/**
 * IAM conditions that limit a grant on the secret's customer managed key to this secret.
 *
 * Secrets Manager calls AWS KMS with the encryption context `SecretARN` = the secret's full ARN, so a
 * principal granted the key with this condition can use it only for this secret, even when the key
 * (for example an external `useKmsCmkEncryption.optionalExternalCmkArn` key) encrypts other data.
 */
export function apiOriginVerifySecretKmsConditions(secret: ApiOriginVerifySecretRef): {
    [operator: string]: { [key: string]: string };
} {
    return { StringEquals: { "kms:EncryptionContext:SecretARN": secret.secretArn } };
}

/**
 * A CloudFormation dynamic reference to the value CloudFront sends (the secret's `current` key).
 *
 * The template carries only `{{resolve:secretsmanager:...}}`; AWS CloudFormation resolves it when it
 * creates or updates the resource that holds it, so the value is in no template, output or
 * environment variable. The reference names no version stage, so it always resolves `AWSCURRENT`,
 * which is the version the rotation function leaves CloudFront and the web ACL on.
 */
export function apiOriginVerifyCurrentValueReference(secretArn: string): string {
    return cdk.SecretValue.secretsManager(secretArn, {
        jsonField: Config.API_ORIGIN_VERIFY_CURRENT_KEY,
    }).unsafeUnwrap();
}
