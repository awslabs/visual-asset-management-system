/*
 * Populates the Physna credentials secret with values from config, without ever
 * placing the secret value in the CloudFormation template.
 *
 * The credential values are written into the custom-resource Lambda's CODE ASSET
 * (a JSON file bundled alongside the handler). CDK references code assets by content
 * hash and uploads them to the CDK assets bucket, so the values never appear in the
 * synthesized template, the template properties, or environment variables. The handler
 * reads its bundled credentials file at run time and calls PutSecretValue on the
 * (empty) secret created by the caller.
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
import * as kms from "aws-cdk-lib/aws-kms";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import { suppressCdkNagLambda } from "../../../../helper/security";

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
    physical_id = "PhysnaSecretPopulate:" + (secret_arn or "unknown")

    # Nothing to clean up on delete — the secret itself is owned by CloudFormation.
    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id}

    # Credentials are delivered via the bundled code asset (not the template).
    creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")
    with open(creds_path, "r", encoding="utf-8") as f:
        creds = json.load(f)

    _sm.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(creds))
    return {"PhysicalResourceId": physical_id}
`;

/**
 * Build the credentials asset directory (handler + credentials.json) in a temp
 * location. Content-hashed by CDK, so the values never enter the template.
 */
function buildCredentialsAsset(clientId: string, clientSecret: string): string {
    const assetDir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-physna-secret-"));
    fs.writeFileSync(path.join(assetDir, "index.py"), HANDLER_SOURCE, { encoding: "utf-8" });
    fs.writeFileSync(
        path.join(assetDir, "credentials.json"),
        JSON.stringify({ clientId, clientSecret }),
        { encoding: "utf-8" }
    );
    return assetDir;
}

/**
 * Create a custom resource that populates `secret` with the given Physna credentials
 * at deploy time. The secret value is carried in the Lambda code asset, so it is not
 * written into the CloudFormation template.
 */
export function populatePhysnaSecret(
    scope: Construct,
    id: string,
    secret: secretsmanager.Secret,
    clientId: string,
    clientSecret: string,
    encryptionKey?: kms.IKey
): cdk.CustomResource {
    const assetDir = buildCredentialsAsset(clientId, clientSecret);

    const populateLambda = new lambda.Function(scope, `${id}Lambda`, {
        runtime: LAMBDA_PYTHON_RUNTIME,
        handler: "index.handler",
        code: lambda.Code.fromAsset(assetDir),
        timeout: Duration.minutes(2),
    });

    // Grant only PutSecretValue on the single target secret.
    secret.grantWrite(populateLambda);

    // When the secret is encrypted with the shared VAMS CMK, PutSecretValue also needs
    // to encrypt under that key. secret.grantWrite does not cover the customer-managed
    // key, so grant encrypt explicitly.
    if (encryptionKey) {
        encryptionKey.grantEncryptDecrypt(populateLambda);
    }

    suppressCdkNagLambda(populateLambda);

    const provider = new cr.Provider(scope, `${id}Provider`, {
        onEventHandler: populateLambda,
    });

    const customResource = new cdk.CustomResource(scope, id, {
        serviceToken: provider.serviceToken,
        properties: {
            SecretArn: secret.secretArn,
            // Re-run when the credential content changes so rotations in config apply.
            // This is a one-way SHA-256 digest, not the secret value, so it is safe to
            // place in the template.
            credentialVersion: crypto
                .createHash("sha256")
                .update(`${clientId}:${clientSecret}`)
                .digest("hex"),
        },
    });

    customResource.node.addDependency(secret);
    return customResource;
}
