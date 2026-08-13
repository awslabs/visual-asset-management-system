/*
 * Populates an NVIDIA pipeline's HuggingFace token secret with the value from config,
 * without ever placing that value in the CloudFormation template.
 *
 * The token is written into the custom-resource Lambda's CODE ASSET (a JSON file bundled
 * alongside the handler). CDK references code assets by content hash and uploads them to
 * the CDK assets bucket, so the value never appears in the synthesized template, the
 * template properties, or environment variables. The handler reads its bundled file at
 * run time and calls PutSecretValue on the (empty) secret created by the caller.
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
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import { suppressCdkNagLambda } from "../../../../../helper/security";

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
    physical_id = "HuggingFaceTokenSecretPopulate:" + (secret_arn or "unknown")

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
 * Content-hashed by CDK, so the value never enters the template.
 */
function buildTokenAsset(token: string): string {
    const assetDir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-hf-token-"));
    fs.writeFileSync(path.join(assetDir, "index.py"), HANDLER_SOURCE, { encoding: "utf-8" });
    fs.writeFileSync(path.join(assetDir, "token.json"), JSON.stringify({ token }), {
        encoding: "utf-8",
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

    // Grant only PutSecretValue on the single target secret.
    secret.grantWrite(populateLambda);

    suppressCdkNagLambda(populateLambda);

    const provider = new cr.Provider(scope, `${id}Provider`, {
        onEventHandler: populateLambda,
    });

    const customResource = new cdk.CustomResource(scope, id, {
        serviceToken: provider.serviceToken,
        properties: {
            SecretArn: secret.secretArn,
            // Re-run when the token changes so a rotation in config applies. This is a
            // one-way SHA-256 digest, not the token, so it is safe to place in the template.
            tokenVersion: crypto.createHash("sha256").update(token).digest("hex"),
        },
    });

    customResource.node.addDependency(secret);
    return customResource;
}
