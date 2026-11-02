/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Duration } from "aws-cdk-lib";

/**
 * How long AWS CloudFormation waits for a response from a deploy-time seed `AwsCustomResource` (the
 * DynamoDB rows written at deploy time) before it fails the resource. Longer than the provider
 * function's own timeout (two minutes by default, five for the feature-switch rows), so a slow call
 * still answers in time, and far shorter than the CloudFormation default of one hour, so a lost
 * response fails the stack operation within minutes.
 */
export const SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT = Duration.minutes(10);
