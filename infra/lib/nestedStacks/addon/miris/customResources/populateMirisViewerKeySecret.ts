/*
 * Populates the Miris add-on's static-mode viewer key secret with the value from config,
 * without placing that value in the CloudFormation template.
 *
 * The key is written into the custom-resource Lambda's CODE ASSET (a JSON file bundled
 * alongside the handler). CDK references code assets by content hash and uploads them to
 * the CDK assets bucket, so the value does not appear in the synthesized template, the
 * template properties, or environment variables. The handler reads its bundled file at
 * run time and calls PutSecretValue on the (empty) secret created by the caller.
 *
 * WHAT THIS DOES NOT PROTECT: the key is in the Lambda's deployment package, so
 * `lambda:GetFunction` on this function yields a download URL for a zip containing it in
 * cleartext, a weaker permission than the `secretsmanager:GetSecretValue` + `kms:Decrypt` the
 * secret itself requires. The same plaintext also lands in the CDK assets bucket, where
 * content-addressed assets are never garbage collected, so a rotated key stays readable there.
 * A viewer key only streams, and it reaches every browser that opens a bound asset, so this
 * exposure is narrower than the one the key already has.
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
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import { discardStagedSecretAsset, suppressCdkNagLambda } from "../../../../helper/security";

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

    # Echo the id CloudFormation already holds and mint one only on Create. A create that fails
    # before returning an id gets CloudFormation's default; a delete that returns a different id
    # is refused by the provider framework and leaves the resource DELETE_FAILED.
    existing_id = event.get("PhysicalResourceId")
    physical_id = existing_id or (
        "MirisViewerKeySecretPopulate:" + (secret_arn or "unknown"))

    # Nothing to clean up on delete: the secret itself is owned by CloudFormation.
    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id}

    # The key is delivered via the bundled code asset (not the template) and stored as the
    # raw key string.
    key_path = os.path.join(os.path.dirname(__file__), "viewerKey.json")
    with open(key_path, "r", encoding="utf-8") as f:
        viewer_key = json.load(f)["viewerKey"]

    _sm.put_secret_value(SecretId=secret_arn, SecretString=viewer_key)
    return {"PhysicalResourceId": physical_id}
`;

/**
 * Build the asset directory (handler + viewerKey.json) in a temp location.
 * Content-hashed by CDK, so the value does not enter the template.
 *
 * The caller must pass the directory to `discardStagedSecretAsset()` once CDK has staged it.
 */
function buildViewerKeyAsset(viewerKey: string): string {
    const assetDir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-miris-viewer-key-"));
    fs.writeFileSync(path.join(assetDir, "index.py"), HANDLER_SOURCE, { encoding: "utf-8" });
    fs.writeFileSync(path.join(assetDir, "viewerKey.json"), JSON.stringify({ viewerKey }), {
        encoding: "utf-8",
        mode: 0o600,
    });
    return assetDir;
}

/**
 * Create a custom resource that populates `secret` with the given Miris viewer key at
 * deploy time. The key is carried in the Lambda code asset, so it is not written into the
 * CloudFormation template.
 */
export function populateMirisViewerKeySecret(
    scope: Construct,
    id: string,
    secret: secretsmanager.Secret,
    viewerKey: string
): cdk.CustomResource {
    const assetDir = buildViewerKeyAsset(viewerKey);

    const populateLambda = new lambda.Function(scope, `${id}Lambda`, {
        runtime: LAMBDA_PYTHON_RUNTIME,
        handler: "index.handler",
        code: lambda.Code.fromAsset(assetDir),
        timeout: Duration.minutes(2),
    });

    // The staged copy in the cloud assembly is what gets uploaded, so the source directory has
    // no further purpose; left behind, it keeps one cleartext key per synth on the build host.
    discardStagedSecretAsset(assetDir);

    // Grant only PutSecretValue on the single target secret.
    secret.grantWrite(populateLambda);

    // `grantWrite` adds no principal-side KMS statement for a key imported by ARN, and writing the
    // value needs `kms:GenerateDataKey*` on the secret's key. The grant is principal-side so the
    // add-on stack's role is not written into the key policy of the storage stack's key; the VAMS
    // key policy delegates `kms:*` to the account root, so IAM decides.
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
            // Re-run when the key changes so a rotation in config applies; a property change is
            // the only thing that re-invokes a custom resource. It is a one-way digest and reveals
            // nothing the template does not already carry: the Lambda's code S3 key is itself a
            // content hash of the file the key sits in.
            viewerKeyVersion: crypto.createHash("sha256").update(viewerKey).digest("hex"),
        },
    });

    customResource.node.addDependency(secret);
    return customResource;
}
