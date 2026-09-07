/*
 * Populates an NVIDIA pipeline's HuggingFace token secret with the value from config,
 * without placing that value in the CloudFormation template.
 *
 * The token is written into the custom-resource Lambda's CODE ASSET (a JSON file bundled
 * alongside the handler). CDK references code assets by content hash and uploads them to
 * the CDK assets bucket, so the value does not appear in the synthesized template, the
 * template properties, or environment variables. The handler reads its bundled file at
 * run time and calls PutSecretValue on the (empty) secret created by the caller.
 *
 * WHAT THIS DOES NOT PROTECT, stated plainly because the shape invites the opposite reading:
 * the token is in the Lambda's deployment package, so `lambda:GetFunction` on this function
 * yields a download URL for a zip containing it in cleartext — a weaker permission than the
 * `secretsmanager:GetSecretValue` + `kms:Decrypt` the secret itself requires. The same
 * plaintext also lands in the CDK assets bucket, where content-addressed assets are never
 * garbage collected, so a rotated token stays readable there. An operator who needs the
 * credential to be reachable ONLY through Secrets Manager should create the secret out of
 * band and reference it, rather than passing a value through synth.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import * as iam from "aws-cdk-lib/aws-iam";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import { discardStagedSecretAsset, suppressCdkNagLambda } from "../../../../../helper/security";

const HANDLER_SOURCE = `# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import os
import boto3

_sm = boto3.client("secretsmanager")


def handler(event, context):
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})
    secret_arn = props.get("SecretArn")

    # ECHO the id CloudFormation already holds; only mint one on Create.
    #
    # When a CREATE fails before the handler returns an id, CloudFormation assigns its own default
    # (a stack-name-derived value). A later DELETE that returns a RECOMPUTED id is refused by the
    # CDK provider framework with "cannot change the physical resource ID", which leaves this
    # resource DELETE_FAILED and every stack above it undeletable. MEASURED: one failed create
    # cascaded into the whole core stack needing manual intervention to remove. Echoing turns a
    # recoverable failure back into a recoverable one.
    existing_id = event.get("PhysicalResourceId")
    physical_id = existing_id or (
        "HuggingFaceTokenSecretPopulate:" + (secret_arn or "unknown"))

    # Nothing to clean up on delete — the secret itself is owned by CloudFormation.
    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id}

    # The token is delivered via the bundled code asset (not the template). Batch reads the
    # secret as a plain container env var, so it is stored as the raw token string.
    token_path = os.path.join(os.path.dirname(__file__), "token.json")
    with open(token_path, "r", encoding="utf-8") as f:
        token = json.load(f)["token"]

    _sm.put_secret_value(SecretId=secret_arn, SecretString=token)
    return {"PhysicalResourceId": physical_id}
`;

/**
 * Build the token asset directory (handler + token.json) in a temp location.
 * Content-hashed by CDK, so the value does not enter the template.
 *
 * The caller must pass the directory to `discardStagedSecretAsset()` once CDK has staged it.
 */
function buildTokenAsset(token: string): string {
    const assetDir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-hf-token-"));
    fs.writeFileSync(path.join(assetDir, "index.py"), HANDLER_SOURCE, { encoding: "utf-8" });
    fs.writeFileSync(path.join(assetDir, "token.json"), JSON.stringify({ token }), {
        encoding: "utf-8",
        mode: 0o600,
    });
    return assetDir;
}

/**
 * Create a custom resource that populates `secret` with the given HuggingFace token at
 * deploy time. The token value is carried in the Lambda code asset, so it is not written
 * into the CloudFormation template.
 */
export function populateHuggingFaceTokenSecret(
    scope: Construct,
    id: string,
    secret: secretsmanager.Secret,
    token: string
): cdk.CustomResource {
    const assetDir = buildTokenAsset(token);

    const populateLambda = new lambda.Function(scope, `${id}Lambda`, {
        runtime: LAMBDA_PYTHON_RUNTIME,
        handler: "index.handler",
        code: lambda.Code.fromAsset(assetDir),
        timeout: Duration.minutes(2),
    });

    // The staged copy in the cloud assembly is what gets uploaded, so the source directory has
    // no further purpose. Left behind it accumulates one cleartext token per synth on the build
    // host — over a thousand were found on a single developer machine — and CI runners keep them
    // for the life of the workspace.
    discardStagedSecretAsset(assetDir);

    // Grant only PutSecretValue on the single target secret.
    secret.grantWrite(populateLambda);

    // The KMS grant that `grantWrite` does NOT add for a secret whose key was imported by ARN.
    //
    // MEASURED, on a fresh deployment with `app.useKmsCmkEncryption.enabled`: the populate lambda's
    // synthesized policy carried `secretsmanager:PutSecretValue`, `UpdateSecret` and
    // `UpdateSecretVersionStage` and no `kms:` action at all, so the custom resource failed with
    //   AccessDeniedException ... calling the PutSecretValue operation: Access to KMS is not allowed
    // and rolled the whole core stack back. Secrets Manager encrypts the value with the secret's key, so
    // writing needs `kms:GenerateDataKey*` on it regardless of the Secrets Manager permissions.
    //
    // The grant has to be written here rather than relied upon: `Secret.grantWrite` delegates to
    // `Key.grantEncryptDecrypt`, and for a key created with `Key.fromKeyArn` CDK cannot see the key's
    // policy and does not add the principal-side statement.
    //
    // This is a PRINCIPAL-side grant on the imported ARN, deliberately. Granting through the key object
    // would write this pipeline stack's role into the key's resource policy, and the key lives in the
    // storage nested stack — a circular dependency between the two stacks (infra/CLAUDE.md, KMS trap 2).
    // A principal-side grant is sufficient because the VAMS key policy delegates `kms:*` to the account
    // root, which is what makes IAM the deciding authority.
    if (secret.encryptionKey) {
        populateLambda.addToRolePolicy(
            new iam.PolicyStatement({
                actions: [
                    "kms:Decrypt",
                    "kms:Encrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources: [secret.encryptionKey.keyArn],
            })
        );
    }

    suppressCdkNagLambda(populateLambda);

    const provider = new cr.Provider(scope, `${id}Provider`, {
        onEventHandler: populateLambda,
    });

    const customResource = new cdk.CustomResource(scope, id, {
        serviceToken: provider.serviceToken,
        properties: {
            SecretArn: secret.secretArn,
            // Re-run when the token changes so a rotation in config applies. A property change
            // is the only thing that re-invokes a custom resource, so a version value is
            // required rather than optional. It is a one-way digest and not the token, and it
            // reveals nothing the template does not already carry: the Lambda's code S3 key is
            // itself a content hash of the file the token sits in. For a high-entropy token
            // neither is invertible; for a low-entropy secret both would let a guess be
            // confirmed offline by anyone holding cloudformation:GetTemplate.
            tokenVersion: crypto.createHash("sha256").update(token).digest("hex"),
        },
    });

    customResource.node.addDependency(secret);
    return customResource;
}
