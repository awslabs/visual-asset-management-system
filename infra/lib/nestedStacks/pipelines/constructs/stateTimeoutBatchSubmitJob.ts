/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Duration, Fn, Stack } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as ServiceHelper from "../../../helper/service-helper";
import { TaggedBatchSubmitJob, TaggedBatchSubmitJobProps } from "./pipelineJobTags";

export interface StateTimeoutBatchSubmitJobProps extends TaggedBatchSubmitJobProps {
    /**
     * Step Functions bound on the whole state: the time the job waits in the AWS Batch queue plus
     * its attempts. Keep it below the state machine timeout, with room for the states after it, so
     * a `States.Timeout` here is caught by the state's Catch rather than ending the execution.
     * `taskTimeout`, when given, still renders as the SubmitJob attempt-duration override.
     */
    readonly stateTimeout: Duration;
}

/**
 * A BatchSubmitJob whose state carries its own Step Functions `TimeoutSeconds`.
 *
 * aws-cdk-lib renders BatchSubmitJob's `taskTimeout` as the SubmitJob attempt-duration override
 * (`Parameters.Timeout.AttemptDurationSeconds`) and sets the state's `TimeoutSeconds` to undefined.
 * The attempt clock starts only when the job leaves the queue, so without a state bound the time a
 * job waits RUNNABLE is limited only by the execution timeout, and an execution-level
 * `States.Timeout` skips every Catch, so the state that reports the outcome never runs. This adds
 * `TimeoutSeconds` to the rendered state, which the state's Catch handles.
 *
 * Step Functions cancels a `.sync` job when the execution is aborted, not when the state times out,
 * so the state's pipelineEnd ends the job itself: give that function
 * `grantBatchStateTimeoutCleanup` on every queue the state can submit to.
 *
 * It is a {@link TaggedBatchSubmitJob}, so the job carries the pipeline's job tags (`jobTags`).
 */
export class StateTimeoutBatchSubmitJob extends TaggedBatchSubmitJob {
    private readonly stateTimeoutSeconds: number;

    constructor(scope: Construct, id: string, props: StateTimeoutBatchSubmitJobProps) {
        const { stateTimeout, ...submitJobProps } = props;
        super(scope, id, submitJobProps);
        this.stateTimeoutSeconds = stateTimeout.toSeconds();
        if (this.stateTimeoutSeconds <= 0) {
            throw new Error(`${id}: stateTimeout must be a positive whole number of seconds`);
        }
    }

    public toStateJson(topLevelQueryLanguage?: sfn.QueryLanguage): object {
        return {
            ...super.toStateJson(topLevelQueryLanguage),
            TimeoutSeconds: this.stateTimeoutSeconds,
        };
    }
}

/**
 * Lets a pipelineEnd function end the run's AWS Batch job after a Batch state's own bound fires:
 * `BATCH_JOB_QUEUES` (the queues the state submits to, comma-separated), `batch:ListJobs` to find
 * the job by its run-unique job name (the action supports no resource-level permission) and
 * `batch:TerminateJob` on jobs in this account and Region (job ids are generated at submit, so
 * `job/*` is the narrowest ARN).
 */
export function grantBatchStateTimeoutCleanup(
    pipelineEnd: lambda.Function,
    jobQueueArns: string[]
): void {
    const stack = Stack.of(pipelineEnd);
    pipelineEnd.addEnvironment("BATCH_JOB_QUEUES", Fn.join(",", jobQueueArns));
    pipelineEnd.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["batch:ListJobs"],
            resources: ["*"],
        })
    );
    pipelineEnd.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["batch:TerminateJob"],
            resources: [
                `arn:${ServiceHelper.Partition()}:batch:${stack.region}:${stack.account}:job/*`,
            ],
        })
    );
    NagSuppressions.addResourceSuppressions(
        pipelineEnd,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "pipelineEnd ends the run's AWS Batch job after its Batch state's own bound fires: batch:ListJobs supports no resource-level permission, and batch:TerminateJob names a job id AWS Batch generates at submit, so job/* in this account and Region is the narrowest ARN. The job is found by the run's unique job name on the pipeline's own queues.",
                appliesTo: [
                    "Resource::*",
                    {
                        regex: "/^Resource::arn:(?:aws(?:-[a-z-]+)?|<AWS::Partition>):batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job\\/\\*$/g",
                    },
                ],
            },
        ],
        true
    );
}
