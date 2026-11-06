/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { IAMArn } from "../helper/service-helper";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

/**
 * Prefix of the workflow state machine names the backend generates
 * (backend/backend/common/workflows/workflowAsl.py `_generate_state_machine_name`). It does not embed
 * the deployment name, so the rule below matches every VAMS deployment's workflow state machines in
 * this account and Region; the handler acts only on an execution with a main row in its own table
 * whose recorded execution ARN is the event's.
 */
export const WORKFLOW_STATE_MACHINE_NAME_PREFIX = "vams-";

/** The terminal statuses Step Functions reports for a Standard workflow execution. */
export const WORKFLOW_STATUS_EVENT_STATUSES = ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"];

/** Construct ids, for the tests and the resource list in documentation. */
export const WORKFLOW_STATUS_CHANGE_RULE_ID = "WorkflowExecutionStatusChangeRule";
export const WORKFLOW_STATUS_CHANGE_DLQ_ID = "WorkflowExecutionStatusChangeDLQ";

/**
 * Pushes the end of every workflow execution to the execution service, so a run that reached a
 * terminal state outside VAMS (stopped from the console or the Step Functions API, timed out or
 * failed at the state machine level) is reconciled, and a system pipeline's terminal callback is
 * sent, without anyone opening the run.
 *
 * A rule on the default event bus matches Step Functions "Execution Status Change" events for the
 * terminal statuses, on state machines whose ARN starts with this account and Region's workflow
 * state machine prefix (no account-wide match), and invokes the execution service function, which
 * already holds every permission the read-path reconcile uses. The rule adds no role and no IAM
 * statement: the target adds a resource policy on the function scoped to this rule's ARN.
 *
 * Two retry layers apply. The target's retry policy (3 attempts within 2 hours) covers delivery to
 * Lambda, and events it cannot deliver go to a dead-letter queue (encrypted with the deployment key
 * when one is configured), the same treatment as the pipeline-execution registration rule. Once
 * Lambda accepts an event, the function's asynchronous invocation defaults (2 retries with backoff,
 * 6 hours) apply: the handler raises when it cannot reach Step Functions or DynamoDB, so a throttled
 * DescribeExecution is retried rather than dropped. A retried or duplicate event finds the row
 * terminal and does nothing more.
 *
 * An event that is never processed falls back to the read paths: the list and details views
 * reconcile the run's status when it is next read, and the details view sends a held ABORTED
 * callback (an abort that could not confirm the stop) once Step Functions shows the run ended.
 */
export function buildWorkflowStatusChangeRule(
    scope: Construct,
    executionServiceFunction: lambda.Function,
    storageResources: storageResources
): events.Rule {
    const rule = new events.Rule(scope, WORKFLOW_STATUS_CHANGE_RULE_ID, {
        description: "Reconcile VAMS workflow executions that end outside VAMS",
        eventPattern: {
            source: ["aws.states"],
            detailType: ["Step Functions Execution Status Change"],
            detail: {
                status: WORKFLOW_STATUS_EVENT_STATUSES,
                stateMachineArn: events.Match.prefix(
                    IAMArn(WORKFLOW_STATE_MACHINE_NAME_PREFIX).statemachine
                ),
            },
        },
    });

    const dlq = new sqs.Queue(scope, WORKFLOW_STATUS_CHANGE_DLQ_ID, {
        encryption: storageResources.encryption.kmsKey
            ? sqs.QueueEncryption.KMS
            : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: storageResources.encryption.kmsKey,
        enforceSSL: true,
        retentionPeriod: Duration.days(14),
    });
    NagSuppressions.addResourceSuppressions(dlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target for the workflow execution status change " +
                "rule, so it takes no further dead-letter queue. Its messages are status events that " +
                "could not be delivered; the read paths still reconcile those executions when read.",
        },
    ]);

    rule.addTarget(
        new eventsTargets.LambdaFunction(executionServiceFunction, {
            deadLetterQueue: dlq,
            retryAttempts: 3,
            maxEventAge: Duration.hours(2),
        })
    );
    return rule;
}
