/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Duration } from "aws-cdk-lib";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";

export interface StateTimeoutBatchSubmitJobProps
    extends Omit<tasks.BatchSubmitJobProps, "taskTimeout" | "timeout"> {
    /**
     * Step Functions bound on the whole state: the time the job waits in the AWS Batch queue plus
     * its attempts. Keep it below the state machine timeout, with room for the states after it, so
     * a `States.Timeout` here is caught by the state's Catch rather than ending the execution.
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
 * `TimeoutSeconds` to the rendered state, which the state's Catch handles. The attempt bound stays
 * on the job definition.
 */
export class StateTimeoutBatchSubmitJob extends tasks.BatchSubmitJob {
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
