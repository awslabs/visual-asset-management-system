/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers the add-on nested stacks share. Upstream-owned.
 */

import { RemovalPolicy } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as kms from "aws-cdk-lib/aws-kms";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { publishLaneResourceName } from "../resourceNames/laneResourceNames";

/**
 * Publish an add-on resource name as an SSM parameter under the deployment's resource-name prefix,
 * where an add-on handler resolves it with get_resource_name(). `key` is a suffix declared in
 * ADDON_RESOURCE_PARAM_KEYS (common/addonResourceParamKeys.ts) and mirrored in
 * backend/backend/common/addonResourceNames.py. A key equal to a core key or to a custom key
 * (CUSTOM_RESOURCE_PARAM_KEYS) throws, as publishCustomResourceName() does for the custom lane.
 */
export function publishAddonResourceName(
    scope: Construct,
    config: Config.Config,
    key: string,
    value: string
): ssm.StringParameter {
    return publishLaneResourceName("addon", scope, config, key, value);
}

/**
 * The DynamoDB table properties an add-on table shares with the core tables in the storage stack:
 * contributor insights off, point-in-time recovery on, RETAIN, on-demand billing, and the shared key
 * when one is passed, else the AWS managed key. Pass the storage key imported by ARN into the add-on
 * stack (kms.Key.fromKeyArn), so a grant on the table writes no statement into the storage key policy.
 */
export function addonDynamoDbTableDefaults(
    kmsKey: kms.IKey | undefined
): Pick<
    dynamodb.TableProps,
    | "contributorInsightsSpecification"
    | "pointInTimeRecoverySpecification"
    | "removalPolicy"
    | "billingMode"
    | "encryption"
    | "encryptionKey"
> {
    return {
        contributorInsightsSpecification: { enabled: false },
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
        removalPolicy: RemovalPolicy.RETAIN,
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: kmsKey
            ? dynamodb.TableEncryption.CUSTOMER_MANAGED
            : dynamodb.TableEncryption.AWS_MANAGED,
        encryptionKey: kmsKey,
    };
}
