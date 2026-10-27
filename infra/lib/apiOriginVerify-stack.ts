/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as kms from "aws-cdk-lib/aws-kms";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../config/config";
import { ApiOriginVerifySecretRef } from "./helper/apiOriginVerify";

export interface ApiOriginVerifyStackProps {
    env: cdk.Environment;
    stackName: string;
    description: string;
    synthesizer?: cdk.IStackSynthesizer;
    config: Config.Config;
}

/**
 * The per-deployment origin verification secret of a CloudFront deployment.
 *
 * CloudFront adds the secret's `current` value to every request it forwards to the REST API
 * (`Config.API_ORIGIN_VERIFY_HEADER_NAME`). The regional web ACL's rate-based rules leave requests
 * carrying a valid value to the CloudFront-scoped web ACL, and the REST authorizer reads the
 * CloudFront forwarding headers only from such requests.
 *
 * The secret has a stack of its own because both of its readers that resolve it at deploy time need it
 * first: the regional WAF stack, which deploys before the core stack, and the CloudFront distribution
 * in the core stack. It is created with a generated `current` key; the rotation function in the core
 * stack's StaticWeb nested stack writes `{current, previous}` on every rotation.
 *
 * Encryption: the deployment's external customer managed key when
 * `useKmsCmkEncryption.optionalExternalCmkArn` names one; otherwise, with `useKmsCmkEncryption`
 * enabled, a key of this stack, because the key VAMS generates lives in the core stack, which deploys
 * after this one; otherwise the AWS managed `aws/secretsmanager` key.
 */
export class ApiOriginVerifyStack extends cdk.Stack {
    public readonly secret: secretsmanager.Secret;
    public readonly secretRef: ApiOriginVerifySecretRef;

    constructor(scope: Construct, id: string, props: ApiOriginVerifyStackProps) {
        super(scope, id, {
            env: props.env,
            stackName: props.stackName,
            description: props.description,
            synthesizer: props.synthesizer,
        });

        const encryptionKey = resolveSecretKmsKey(this, props.config);

        // Auto-named, so a secret left in its recovery window after a teardown never collides with
        // the one a redeploy creates. Removed with the stack.
        this.secret = new secretsmanager.Secret(this, "ApiOriginVerifySecret", {
            description:
                "Origin verification value Amazon CloudFront adds to the API requests it forwards",
            encryptionKey,
            generateSecretString: {
                secretStringTemplate: "{}",
                generateStringKey: Config.API_ORIGIN_VERIFY_CURRENT_KEY,
                excludePunctuation: true,
                passwordLength: 64,
            },
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        NagSuppressions.addResourceSuppressions(this.secret, [
            {
                id: "AwsSolutions-SMG4",
                reason: `Rotated every ${Config.API_ORIGIN_VERIFY_ROTATION_DAYS} days by the rotation schedule the core stack's StaticWeb nested stack attaches, next to the distribution and web ACL the rotation function updates.`,
            },
        ]);

        this.secretRef = {
            secretArn: this.secret.secretArn,
            kmsKeyArn: encryptionKey?.keyArn,
        };
    }
}

function normalizeOptional(value: string | undefined): string | undefined {
    return value && value != "" && value != "UNDEFINED" ? value : undefined;
}

function resolveSecretKmsKey(scope: Construct, config: Config.Config): kms.IKey | undefined {
    if (!config.app.useKmsCmkEncryption.enabled) {
        return undefined;
    }
    const externalCmkArn = normalizeOptional(config.app.useKmsCmkEncryption.optionalExternalCmkArn);
    if (externalCmkArn) {
        return kms.Key.fromKeyArn(scope, "ApiOriginVerifySecretKey", externalCmkArn);
    }
    // RETAIN so the key outlives a stack teardown; it carries no alias, so a retained key never
    // collides with the one a redeploy creates.
    return new kms.Key(scope, "ApiOriginVerifySecretKey", {
        description: "VAMS Generated KMS Encryption key for the API origin verification secret",
        enableKeyRotation: true,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
}

/**
 * Creates the origin verification stack for a CloudFront deployment, in the deployment's account and
 * Region, and returns undefined for every other deployment. Both the CDK app entry point and the synth
 * test harness build the app through this, so they agree on what is emitted.
 */
export function buildApiOriginVerifyStack(
    scope: Construct,
    config: Config.Config,
    synthesizer?: cdk.IStackSynthesizer
): ApiOriginVerifyStack | undefined {
    if (!config.app.useCloudFront.enabled) {
        return undefined;
    }
    const stackName = `${config.name}-origin-verify-${config.app.baseStackName}`;
    return new ApiOriginVerifyStack(scope, stackName, {
        stackName,
        env: { account: config.env.account, region: config.env.region },
        description: Config.STACK_ORIGIN_VERIFY_DESCRIPTION,
        synthesizer,
        config,
    });
}
