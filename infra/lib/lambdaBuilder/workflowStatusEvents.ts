/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as events from "aws-cdk-lib/aws-events";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Duration, Names } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { IAMArn, Service } from "../helper/service-helper";
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
export const WORKFLOW_STATUS_CHANGE_PERMISSION_ID = "WorkflowExecutionStatusChangeInvokePermission";

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
 * statement: a resource policy on the function scoped to this rule's ARN lets it invoke, and the
 * rule is created only after that policy and the dead-letter queue's policy exist.
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
    // The rule must not go live before it can deliver. It matches every VAMS deployment's workflow
    // state machines in the account and Region, so while it exists without the function's resource
    // policy (or the dead-letter queue's policy) it collects other deployments' events as failed
    // deliveries, some dropped and the rest in this deployment's dead-letter queue. Both policies
    // therefore name the rule by an ARN built from its fixed name rather than by a reference to the
    // rule (a reference would make the dependency below a cycle), and the rule depends on both:
    // CloudFormation creates the policies first and the rule last.
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
    const cfnRule = rule.node.defaultChild as events.CfnRule;
    cfnRule.name = workflowStatusChangeRuleName(rule);
    const ruleArn = IAMArn(cfnRule.name).eventRule;

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

    // What the aws-events-targets LambdaFunction target adds (the function's resource policy for
    // this rule, the queue policy statement, the target), with the rule named by its built ARN.
    const permission = new lambda.CfnPermission(scope, WORKFLOW_STATUS_CHANGE_PERMISSION_ID, {
        action: "lambda:InvokeFunction",
        functionName: executionServiceFunction.functionArn,
        principal: Service("EVENTS").PrincipalString,
        sourceArn: ruleArn,
    });
    dlq.addToResourcePolicy(
        new iam.PolicyStatement({
            sid: "AllowWorkflowStatusChangeRule",
            principals: [Service("EVENTS").Principal],
            effect: iam.Effect.ALLOW,
            actions: ["sqs:SendMessage"],
            resources: [dlq.queueArn],
            conditions: { ArnEquals: { "aws:SourceArn": ruleArn } },
        })
    );
    // The queue's policy construct (created with the queue for enforceSSL, holding the statement above).
    const queuePolicy = dlq.node.findChild("Policy") as sqs.QueuePolicy;

    rule.addTarget({
        bind: (): events.RuleTargetConfig => ({
            arn: executionServiceFunction.functionArn,
            deadLetterConfig: { arn: dlq.queueArn },
            retryPolicy: {
                maximumRetryAttempts: 3,
                maximumEventAgeInSeconds: Duration.hours(2).toSeconds(),
            },
        }),
    });
    cfnRule.addDependency(permission);
    cfnRule.node.addDependency(queuePolicy);
    return rule;
}

/**
 * The status change rule's name: unique per deployment (the top-level stack name and the construct
 * path, with a hash suffix) and within EventBridge's 64-character limit. Fixed rather than generated
 * by CloudFormation so its ARN is known without a reference to the rule.
 */
export function workflowStatusChangeRuleName(rule: Construct): string {
    return Names.uniqueResourceName(rule, {
        maxLength: 64,
        separator: "-",
        allowedSpecialCharacters: "-",
    });
}
