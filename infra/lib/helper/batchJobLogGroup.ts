/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as logs from "aws-cdk-lib/aws-logs";
import { IAMArn } from "./service-helper";

// The CloudWatch Logs group AWS Batch writes a container's output to when its job definition sets
// no log configuration. The GPU Batch pipelines (NVIDIA Cosmos, GR00T, Isaac Lab, Splat Toolbox)
// set none, so their container streams land here as `<jobDefinitionName>/default/<ecs-task-id>`.
// The five Fargate pipelines (coordinate transform, Blender renderer, 3D thumbnail, PDAL, Potree)
// do NOT: `BatchFargatePipelineConstruct` routes their output through the `awslogs` driver to a
// VAMS-owned `/aws/vendedlogs/Pipelines/<Name><hash>` group, and they register that group instead
// (`vendedBatchJobLogGroupEnvironment`).
export const BATCH_JOB_LOG_GROUP_NAME = "/aws/batch/job";

/**
 * Environment entries for a lambda that registers its pipeline's Batch container log group when
 * that pipeline's job definition sets no log configuration (the GPU pipelines). The ARN is the
 * colon-separated `log-group:` form the backend's CLOUDWATCH_LOG_GROUP_ARN validator accepts.
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
 * The same two entries for a pipeline whose job definition writes to a VAMS-owned group through the
 * `awslogs` driver (the Fargate pipelines): the registration must name the group the container
 * actually writes to, or the execution log view resolves a stream in a group that holds nothing.
 * `logGroupArn` renders with a trailing `:*`, which the validator accepts and the reader strips.
 */
export function vendedBatchJobLogGroupEnvironment(logGroup: logs.ILogGroup): {
    BATCH_JOB_LOG_GROUP_NAME: string;
    BATCH_JOB_LOG_GROUP_ARN: string;
} {
    return {
        BATCH_JOB_LOG_GROUP_NAME: logGroup.logGroupName,
        BATCH_JOB_LOG_GROUP_ARN: logGroup.logGroupArn,
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
