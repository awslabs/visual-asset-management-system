/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { IAMArn } from "./service-helper";

// The CloudWatch Logs group AWS Batch writes a container's output to when its job definition sets
// no log configuration. No VAMS job definition sets one, so every Batch pipeline's container stream
// lands here as `<jobDefinitionName>/default/<ecs-task-id>`.
export const BATCH_JOB_LOG_GROUP_NAME = "/aws/batch/job";

/**
 * Environment entries for a lambda that registers its pipeline's Batch container log group. The ARN
 * is the colon-separated `log-group:` form the backend's CLOUDWATCH_LOG_GROUP_ARN validator accepts.
 */
export function batchJobLogGroupEnvironment(): {
    BATCH_JOB_LOG_GROUP_NAME: string;
    BATCH_JOB_LOG_GROUP_ARN: string;
} {
    return {
        BATCH_JOB_LOG_GROUP_NAME,
        BATCH_JOB_LOG_GROUP_ARN: IAMArn(BATCH_JOB_LOG_GROUP_NAME).loggroup,
    };
}

/**
 * The job definition name inside an `AWS::Batch::JobDefinition` Ref. The Ref is the ARN
 * `arn:<partition>:batch:<region>:<account>:job-definition/<name>:<revision>`: colon component 5 is
 * `job-definition/<name>`, and the revision (component 6) is dropped.
 */
export function jobDefinitionNameFromRef(jobDefinitionRef: string): string {
    return cdk.Fn.select(
        1,
        cdk.Fn.split("/", cdk.Fn.select(5, cdk.Fn.split(":", jobDefinitionRef)))
    );
}
