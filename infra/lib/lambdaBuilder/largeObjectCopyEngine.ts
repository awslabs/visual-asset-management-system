/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The large object copy engine: one Standard Step Functions state machine per deployment that copies
 * a large object into its asset as a multipart copy made of short Lambda tasks, and the grants of the
 * functions that start, wait on or stop its jobs.
 *
 *   Prepare -> Map(chunks, one at a time) { child execution: Map(batches) { CopyBatch } }
 *           -> Complete -> NotifySucceeded
 *   Catch (any state) -> Fail -> NotifyFailed -> Refused (succeeds) | CopyFailed (fails)
 *
 * The batches run in child executions of a second state machine (LargeObjectCopyParts-*) so that no
 * execution's history can reach the Step Functions quota (see "History budget" below). A copy
 * execution that ends outside its states is finalized by a status change rule (reconcile).
 *
 * Starters: fileIngestion (workflow outputs over 1 GiB) and the large-file queue consumer (API uploads
 * over app.largeFileIngestion.apiUploadEngineThresholdBytes). Waiters: the workflow output step (task
 * token). Stoppers: the abort route and the workflow error handler.
 *
 * The state machine has a fixed name so the starters in ApiBuilder can be given its ARN without a
 * cross-stack reference: it is built in ApiBuilder2 (next to the output step it reports to), and
 * ApiBuilder2 already depends on ApiBuilder. The name does not start with "vams-", the prefix of the
 * runtime-created workflow state machines, so the workflow status change rule does not match it.
 * Backend: backend/backend/common/largeObjectCopy.py, backend/backend/handlers/assets/largeObjectCopy.py.
 */

import * as cdk from "aws-cdk-lib";
import { Duration, Names } from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";
import * as path from "path";
import * as Config from "../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import {
    generateUniqueNameHash,
    globalLambdaEnvironmentsAndPermissions,
    grantReadWritePermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../helper/security";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import { IAMArn, Service } from "../helper/service-helper";
import {
    LARGE_OBJECT_COPY_JOBS_EXECUTION_INDEX,
    storageResources,
} from "../nestedStacks/storage/storageBuilder-nestedStack";

/** Backend-generated workflow state machine names (workflowAsl._generate_state_machine_name). */
const WORKFLOW_STATE_MACHINE_NAME_PATTERN = "vams-*";
/** The asset buckets table's index by bucket name (storageBuilder-nestedStack.ts). */
export const S3_ASSET_BUCKETS_NAME_INDEX = "bucketNameGSI";

/** Fixed name of the copy state machine (<= 80 characters). */
export function largeObjectCopyStateMachineName(config: Config.Config): string {
    return `LargeObjectCopy-${config.env.coreStackName}`.substring(0, 80);
}

/** ARN of the copy state machine, from its fixed name (no cross-stack reference). */
export function largeObjectCopyStateMachineArn(config: Config.Config): string {
    return IAMArn(largeObjectCopyStateMachineName(config)).statemachine;
}

/** ARN pattern of the copy state machine's executions. */
export function largeObjectCopyExecutionArnPattern(config: Config.Config): string {
    return IAMArn(largeObjectCopyStateMachineName(config) + ":*").statemachineExecution;
}

/**
 * Environment every function that starts, runs or waits on copy jobs reads (backend
 * common/largeObjectCopy.settings). The table name resolves from SSM.
 */
export function largeObjectCopyEnvironment(config: Config.Config): Record<string, string> {
    const tuning = config.app.largeFileIngestion;
    return {
        LARGE_OBJECT_COPY_STATE_MACHINE_ARN: largeObjectCopyStateMachineArn(config),
        LARGE_OBJECT_COPY_API_UPLOAD_THRESHOLD_BYTES: String(tuning.apiUploadEngineThresholdBytes),
        LARGE_OBJECT_COPY_MIN_PART_SIZE_BYTES: String(tuning.minPartSizeBytes),
        LARGE_OBJECT_COPY_PARTS_PER_BATCH: String(tuning.partsPerBatch),
        LARGE_OBJECT_COPY_THREADS_PER_BATCH: String(tuning.threadsPerBatch),
        LARGE_OBJECT_COPY_MAX_WAIT_HOURS: String(tuning.maxOutputIngestionWaitHours),
        LARGE_OBJECT_COPY_BATCHES_PER_CHUNK: String(BATCHES_PER_CHUNK),
    };
}

function addEnvironment(fun: lambda.Function, environment: Record<string, string>) {
    for (const [key, value] of Object.entries(environment)) {
        fun.addEnvironment(key, value);
    }
}

function jobsTableStatement(storage: storageResources, actions: string[], withIndex: boolean) {
    const table = storage.dynamo.largeObjectCopyJobsStorageTable;
    const resources = [table.tableArn];
    if (withIndex) {
        resources.push(`${table.tableArn}/index/${LARGE_OBJECT_COPY_JOBS_EXECUTION_INDEX}`);
    }
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: actions.map((a) => `dynamodb:${a}`),
        resources,
    });
}

/**
 * Attach a customer managed policy to a function's role (IAM does not count it against the role's
 * inline policy quota, which several of these roles are close to). The role names the policy, so it
 * is attached before the role's default policy updates, and the function waits for it.
 */
function attachManagedPolicy(fun: lambda.Function, id: string, statements: iam.PolicyStatement[]) {
    const policy = new iam.ManagedPolicy(fun, id, { statements });
    fun.role!.addManagedPolicy(policy);
    fun.node.defaultChild!.node.addDependency(policy);
    NagSuppressions.addResourceSuppressions(policy, [
        {
            id: "AwsSolutions-IAM5",
            reason:
                "Scoped to the copy state machine's executions, the jobs table's one index, the " +
                "backend-generated workflow state machines (vams-*) and AbortMultipartUpload on " +
                "asset bucket objects.",
        },
    ]);
}

/**
 * Grants of a STARTER (fileIngestion, the large-file queue consumer): StartExecution on the copy
 * state machine, the job row writes start_copy_job makes (conditional PutItem, UpdateItem), and the
 * job row read of a redelivered message (GetItem).
 */
export function grantLargeObjectCopyStart(
    fun: lambda.Function,
    storage: storageResources,
    config: Config.Config
) {
    addEnvironment(fun, largeObjectCopyEnvironment(config));
    attachManagedPolicy(fun, "LargeObjectCopyStartPolicy", [
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StartExecution"],
            resources: [largeObjectCopyStateMachineArn(config)],
        }),
        // GetItem: a redelivered queue message finds the job it already handed off.
        jobsTableStatement(storage, ["PutItem", "UpdateItem", "GetItem"], false),
    ]);
}

/**
 * Grants of the workflow OUTPUT STEP (ingest / await / finalize modes) and of the STOPPERS (abort
 * route, error handler): the batch and job rows, the execution index, StopExecution on copy
 * executions (cancel), and, for the output step, SendTaskSuccess on the workflow state machines (it
 * answers its own task token when the copies finished before the wait registered).
 */
export function grantLargeObjectCopyCoordination(
    fun: lambda.Function,
    storage: storageResources,
    config: Config.Config,
    options: {
        answersTaskToken: boolean;
        reconcileWith?: lambda.IFunction;
        handsOffTo?: lambda.IFunction;
    }
) {
    addEnvironment(fun, largeObjectCopyEnvironment(config));
    const statements = [
        jobsTableStatement(storage, ["GetItem", "UpdateItem", "Query"], true),
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StopExecution"],
            resources: [largeObjectCopyExecutionArnPattern(config)],
        }),
    ];
    // A stopped copy execution runs no Catch state, so the stopper aborts its multipart upload in
    // the asset bucket (cancel_execution_copy_jobs). Only that action: it reads and writes no object.
    const assetObjectArns = s3AssetBuckets
        .getS3AssetBucketRecords()
        .map((record) => record.bucket.arnForObjects("*"));
    if (assetObjectArns.length > 0) {
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:AbortMultipartUpload"],
                resources: assetObjectArns,
            })
        );
    }
    // The abort is signed for the asset bucket's Region. A job row carries destRegion; a row written
    // without it resolves the Region through the asset buckets table's bucketNameGSI (backend
    // common/s3.py _lookup_bucket_region), so every stopper may query that index (#1123 D6).
    statements.push(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:Query"],
            resources: [
                `${storage.dynamo.s3AssetBucketsStorageTable.tableArn}/index/${S3_ASSET_BUCKETS_NAME_INDEX}`,
            ],
        })
    );
    if (options.answersTaskToken) {
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:SendTaskSuccess"],
                resources: [IAMArn(WORKFLOW_STATE_MACHINE_NAME_PATTERN).statemachine],
            })
        );
    }
    if (options.reconcileWith) {
        // The execution details read hands the copy engine an unfinished job whose execution ended
        // outside its states (a missed status change event): it describes the job's execution
        // (backend executionService._reconcile_ended_copies).
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:DescribeExecution"],
                resources: [largeObjectCopyExecutionArnPattern(config)],
            })
        );
    }
    const engine = options.reconcileWith ?? options.handsOffTo;
    if (engine) {
        // The cancel path hands each copy it stopped and finalized as landed, or left to the engine
        // to decide, to the engine's reconcile with an asynchronous invoke (backend
        // largeObjectCopy.request_reconcile), next to the stop's status change event.
        fun.addEnvironment("LARGE_OBJECT_COPY_FUNCTION_NAME", engine.functionName);
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [engine.functionArn],
            })
        );
    }
    attachManagedPolicy(fun, "LargeObjectCopyCoordinationPolicy", statements);
}

/** Fixed name of the SSM parameter holding the copy engine function name. */
export function largeObjectCopyEngineFunctionNameParameterName(config: Config.Config): string {
    return (
        "/" +
        [
            `${config.name}-${config.app.baseStackName}`,
            "largeObjectCopy",
            "engineFunctionName",
        ].join("/")
    );
}

/**
 * The cancel path hand-over (see grantLargeObjectCopyCoordination) for the workflow OUTPUT STEP. The
 * engine already references the output step (it finalizes a run whose definition does not wait), so
 * the output step cannot take the engine's name as an environment variable or wait for a policy
 * naming it: either would be a CloudFormation dependency cycle. Instead the engine's name is in an
 * SSM parameter of fixed name (the environment names it as a plain string), and one managed policy
 * that names the output step's role (not attached through the role, so the role and the function do
 * not depend on it) grants reading that parameter and invoking the engine.
 */
export function grantLargeObjectCopyHandOffByParameter(
    scope: Construct,
    fun: lambda.Function,
    engine: lambda.IFunction,
    config: Config.Config
) {
    const role = fun.role;
    if (!role) {
        throw new Error("The workflow output step needs an execution role for the copy hand-over");
    }
    const parameterName = largeObjectCopyEngineFunctionNameParameterName(config);
    const parameter = new ssm.StringParameter(scope, "LargeObjectCopyEngineFunctionNameParameter", {
        parameterName,
        stringValue: engine.functionName,
        description:
            "Name of the VAMS large object copy engine function (read by the workflow output step).",
    });
    fun.addEnvironment("LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER", parameterName);
    new iam.ManagedPolicy(fun, "LargeObjectCopyHandOffPolicy", {
        roles: [role],
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:GetParameter"],
                resources: [parameter.parameterArn],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [engine.functionArn],
            }),
        ],
    });
}

export interface LargeObjectCopyEngine {
    stateMachine: sfn.StateMachine;
    engineFunction: lambda.Function;
}

/** Build the copy-engine Lambda and the copy state machine (in ApiBuilder2). */
export function buildLargeObjectCopyEngine(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storage: storageResources,
    sendEmailFunction: lambda.IFunction,
    processWorkflowExecutionOutputFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): LargeObjectCopyEngine {
    const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    const reservedConcurrency = config.app.largeFileIngestion.engineReservedConcurrency;
    // Reconcile requests that could not be processed: status change events the rule could not
    // deliver, and asynchronous reconcile invocations (the rule's, the details read's and the cancel
    // path's) that still failed after Lambda's retries.
    const reconcileDlq = new sqs.Queue(scope, LARGE_OBJECT_COPY_RECONCILE_DLQ_ID, {
        encryption: storage.encryption.kmsKey
            ? sqs.QueueEncryption.KMS
            : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: storage.encryption.kmsKey,
        enforceSSL: true,
        retentionPeriod: Duration.days(14),
    });
    NagSuppressions.addResourceSuppressions(reconcileDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target of the copy engine's reconcile requests, " +
                "so it takes no further dead-letter queue. A request left here names an ended copy " +
                "execution; an alarm on the queue signals it, and the operator re-invokes the engine's " +
                "reconcile with that execution ARN.",
        },
    ]);
    // A request in the reconcile DLQ is an ended copy whose job, or whose post-landing work, may be
    // unfinished: a cancelled stop, or an execution that ended outside its states. Both the status
    // change event and the cancel path's direct hand-over dead-letter here, and nothing retries a
    // request once it is here, so the alarm is the signal to act. The queue is a dead-letter target
    // with no source queue, so an SQS redrive cannot replay it; the remedy is to invoke the engine's
    // reconcile again (safe to repeat). No alarm action: VAMS has no notification topic, like the
    // other dead-letter queue alarms.
    new cloudwatch.Alarm(scope, LARGE_OBJECT_COPY_RECONCILE_DLQ_ALARM_ID, {
        alarmName: `${config.name}-${config.app.baseStackName}-largeObjectCopyReconcileDLQ`,
        alarmDescription:
            "Large object copy reconcile requests the copy engine could not process are in this " +
            "dead-letter queue. Each message names an ended copy execution (executionArn, or " +
            "detail.executionArn for a status change event). After fixing the cause, invoke the copy " +
            'engine function with {"action": "reconcile", "executionArn": "<that ARN>"} (safe to ' +
            "repeat), then delete the message.",
        metric: reconcileDlq.metricApproximateNumberOfMessagesVisible({
            period: Duration.minutes(5),
            statistic: cloudwatch.Stats.MAXIMUM,
        }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    // Checkov: like every VAMS function, reserved concurrency (CKV_AWS_115) is set only when
    // configured, the VPC (CKV_AWS_117) follows app.useGlobalVpc, and the environment holds no
    // secret, so it keeps Lambda's default encryption (CKV_AWS_173). CKV_AWS_116 is met (reconcileDlq).
    const fun = new lambda.Function(scope, "largeObjectCopy", {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.largeObjectCopy.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        // Each task is sized for about 5 minutes of copying; the 15-minute ceiling is headroom, and a
        // task that hits it is retried and resumes at the next missing part.
        timeout: Duration.minutes(15),
        // Server-side part copies hold no object data in memory. Only an in-VPC copy between
        // Regions streams parts through the function (two at a time, each up to a planned part).
        memorySize: inVpc ? Config.LAMBDA_MEMORY_SIZE : LARGE_OBJECT_COPY_SERVER_SIDE_MEMORY_MB,
        // Optional deployment-wide cap on concurrent copy tasks (unset: the unreserved pool). A task
        // over the cap is throttled, and throttling only slows a copy (see THROTTLE_RETRY).
        reservedConcurrentExecutions: reservedConcurrency > 0 ? reservedConcurrency : undefined,
        // Only asynchronous invocations (reconcile) use it; the state machines invoke synchronously.
        deadLetterQueue: reconcileDlq,
        vpc: inVpc ? vpc : undefined,
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,
        environment: {
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            ...largeObjectCopyEnvironment(config),
        },
    });

    // Job and batch rows (TransactWriteItems counts a job on its batch), the execution index
    // (finalize-side reads), the asset row (asset type after a file lands), the buckets table (the
    // Region-routing S3 client), the main execution row (display-only progress counter), the upload
    // record (an API upload's outcome).
    fun.addToRolePolicy(jobsTableStatement(storage, ["GetItem", "UpdateItem", "Query"], true));
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources: [storage.dynamo.assetStorageTable.tableArn],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:UpdateItem"],
            resources: [storage.dynamo.workflowExecutionsStorageTableV2.tableArn],
        })
    );
    // An API upload's copy records its file's outcome on the upload record.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:UpdateItem"],
            resources: [storage.dynamo.assetUploadsStorageTable.tableArn],
        })
    );
    storage.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Report a finished batch: answer the waiting workflow's task token, or finalize a run whose
    // definition does not wait.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess"],
            resources: [IAMArn(WORKFLOW_STATE_MACHINE_NAME_PATTERN).statemachine],
        })
    );
    processWorkflowExecutionOutputFunction.grantInvoke(fun);
    sendEmailFunction.grantInvoke(fun);
    // Reconcile: read an ended copy execution's status and input (its job).
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:DescribeExecution"],
            resources: [largeObjectCopyExecutionArnPattern(config)],
        })
    );

    // Read staged sources, write the destination multipart upload, delete the staged object.
    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storage.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storage);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    const stateMachine = buildCopyStateMachine(scope, fun, storage, config);
    buildCopyStatusChangeRule(scope, fun, config, reconcileDlq);
    return { stateMachine, engineFunction: fun };
}

/** The copy execution statuses that mean it may have ended outside its states. */
export const LARGE_OBJECT_COPY_RECONCILE_STATUSES = ["FAILED", "TIMED_OUT", "ABORTED"];

/**
 * Reconcile a copy execution that ended outside its states (H2): the history quota, a runtime
 * error, a timeout, or a stop in the console or the API runs no Catch, Fail or notify state. A rule
 * on the default event bus matches this deployment's copy state machine only (its exact ARN) and
 * invokes the engine, which finalizes the job once (backend largeObjectCopy.reconcile) and leaves a
 * job the cancel path stopped. The same pattern as the workflow status change rule
 * (workflowStatusEvents.ts), which matches only vams-* workflow state machines. A missed event is
 * picked up by the execution details read, which checks a run's unfinished copies.
 *
 * As with the workflow status change rule (#1122 D2), the rule is created only after the engine's
 * resource policy and the dead-letter queue's policy, so it never goes live unable to deliver. Both
 * policies name the rule by an ARN built from its fixed name rather than by a reference to the rule
 * (a reference would make that dependency a cycle).
 */
function buildCopyStatusChangeRule(
    scope: Construct,
    fun: lambda.Function,
    config: Config.Config,
    dlq: sqs.Queue
): events.Rule {
    const rule = new events.Rule(scope, LARGE_OBJECT_COPY_STATUS_RULE_ID, {
        description: "Finalize VAMS large object copies whose execution ended outside its states",
        eventPattern: {
            source: ["aws.states"],
            detailType: ["Step Functions Execution Status Change"],
            detail: {
                status: LARGE_OBJECT_COPY_RECONCILE_STATUSES,
                stateMachineArn: [largeObjectCopyStateMachineArn(config)],
            },
        },
    });
    const cfnRule = rule.node.defaultChild as events.CfnRule;
    cfnRule.name = largeObjectCopyStatusRuleName(rule);
    const ruleArn = IAMArn(cfnRule.name).eventRule;

    // What the aws-events-targets LambdaFunction target adds (the engine's resource policy for this
    // rule, the queue policy statement, the target), with the rule named by its built ARN.
    const permission = new lambda.CfnPermission(scope, LARGE_OBJECT_COPY_STATUS_PERMISSION_ID, {
        action: "lambda:InvokeFunction",
        functionName: fun.functionArn,
        principal: Service("EVENTS").PrincipalString,
        sourceArn: ruleArn,
    });
    dlq.addToResourcePolicy(
        new iam.PolicyStatement({
            sid: "AllowLargeObjectCopyStatusChangeRule",
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
            arn: fun.functionArn,
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
 * The copy status rule's name: unique per deployment (the top-level stack name and the construct
 * path, with a hash suffix) and within EventBridge's 64-character limit. Fixed rather than generated
 * by CloudFormation so its ARN is known without a reference to the rule.
 */
export function largeObjectCopyStatusRuleName(rule: Construct): string {
    return Names.uniqueResourceName(rule, {
        maxLength: 64,
        separator: "-",
        allowedSpecialCharacters: "-",
    });
}

/** Engine memory when every part is copied server side (UploadPartCopy holds no object data). */
export const LARGE_OBJECT_COPY_SERVER_SIDE_MEMORY_MB = 1024;

// Raised by the engine for a copy no retry can complete (backend common/largeObjectCopy.py).
const NON_RETRYABLE_ERRORS = ["CopyRefused", "SourceChanged"];

/**
 * Lambda throttling (the account or the engine's reserved concurrency is exhausted) is retried
 * separately from faults, with long jittered backoff, so a throttled copy waits for capacity instead
 * of failing and discarding the parts it has copied. The delays double from 10 s to a 300 s cap; with
 * full jitter each delay is uniform between 0 and that value, so 99 attempts wait about 4 hours on
 * average and at most about 7.9 hours. Exported for the tests; the workflow definitions use the same
 * values (backend workflowAslBuilder THROTTLE_RETRIER).
 */
export const THROTTLE_RETRY = {
    errors: ["Lambda.TooManyRequestsException"],
    intervalSeconds: 10,
    backoffRate: 2,
    maxAttempts: 99,
    maxDelaySeconds: 300,
};

/** Fault retrier of an engine task (anything but a named refusal or throttling). */
export const FAULT_RETRY = {
    intervalSeconds: 5,
    backoffRate: 2,
    maxAttempts: 6,
    maxDelaySeconds: 120,
};

function engineTask(
    scope: Construct,
    id: string,
    fun: lambda.IFunction,
    payload: Record<string, unknown>,
    options: {
        resultSelector?: Record<string, unknown>;
        resultPath?: string;
        faultRetry?: typeof FAULT_RETRY;
    }
): tasks.LambdaInvoke {
    const task = new tasks.LambdaInvoke(scope, id, {
        lambdaFunction: fun,
        payload: sfn.TaskInput.fromObject(payload),
        resultSelector: options.resultSelector,
        resultPath: options.resultPath ?? sfn.JsonPath.DISCARD,
        retryOnServiceExceptions: false,
        taskTimeout: sfn.Timeout.duration(Duration.minutes(15)),
    });
    // A named refusal is final; throttling waits for capacity; anything else (a Lambda fault, a
    // task timeout, a failed S3 call) is retried, and a retried copy resumes where it stopped.
    // Step Functions uses the first retrier whose errors match, so States.ALL comes last.
    task.addRetry({ errors: NON_RETRYABLE_ERRORS, maxAttempts: 0 });
    task.addRetry({
        errors: THROTTLE_RETRY.errors,
        interval: Duration.seconds(THROTTLE_RETRY.intervalSeconds),
        backoffRate: THROTTLE_RETRY.backoffRate,
        maxAttempts: THROTTLE_RETRY.maxAttempts,
        maxDelay: Duration.seconds(THROTTLE_RETRY.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    const fault = options.faultRetry ?? FAULT_RETRY;
    task.addRetry({
        errors: [sfn.Errors.ALL],
        interval: Duration.seconds(fault.intervalSeconds),
        backoffRate: fault.backoffRate,
        maxAttempts: fault.maxAttempts,
        maxDelay: Duration.seconds(fault.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    return task;
}

/**
 * Fault retrier of the last notify attempt: a notify that failed its normal retries (DynamoDB or
 * Step Functions unavailable) tries again, about 1.4 hours on average and at most about 2.8 hours
 * (full jitter), so a landed file still finalizes its run long before the run's maximum wait.
 */
export const NOTIFY_LAST_ATTEMPT_RETRY = {
    intervalSeconds: 30,
    backoffRate: 2,
    maxAttempts: 20,
    maxDelaySeconds: 600,
};

/*
 * History budget (H2). A Standard execution fails, outside its own states, once its history reaches
 * HISTORY_EVENT_QUOTA events. Throttling retries land in the history of the execution that runs the
 * task, so the part batches run in child executions of at most BATCHES_PER_CHUNK batches each, and
 * no execution of the copy can approach the quota however long its batches are throttled:
 *
 * - a Task state adds at most 5 events (entered, scheduled, started, succeeded or failed, exited)
 *   plus 3 per retried attempt (scheduled, started, failed); a Map iteration adds 2 around it;
 * - a CopyBatch iteration in a child execution: 2 + 5 + 3 x (0 + 99 + 6) = 322 events at most
 *   (CopyRefused retries 0 times, throttling 99, faults 6);
 * - a child execution: 50 x 322 = 16,100, plus its own start, Map and end events: about 16,110, 64%
 *   of the quota;
 * - the job's own execution: at most MAX_BATCHES / BATCHES_PER_CHUNK = 20 chunk iterations of
 *   2 + 6 (startExecution.sync adds a submitted event) + 3 x 6 retries = 26 events (520), plus
 *   Prepare, Complete, Fail and the four notify tasks, each run once with every retrier exhausted
 *   (at most 5 + 3 x (99 + 20) = 362 each, about 2,550): about 3,100, 12% of the quota.
 *
 * infra/test/workflows/largeObjectCopyEngine.test.ts recomputes these bounds from the synthesized
 * definitions. A copy execution that still ends outside its states (stopped in the console, a
 * runtime error) is finalized by the status change rule below (backend largeObjectCopy.reconcile).
 */

/** Events a Standard execution's history may hold (Step Functions quota, not adjustable). */
export const HISTORY_EVENT_QUOTA = 25000;
/** Most batches a job is planned into (backend common/largeObjectCopy.py MAX_BATCHES). */
export const MAX_BATCHES = 1000;
/** Batches each child execution of a copy runs (see the history budget above). */
export const BATCHES_PER_CHUNK = 50;
/** Upper bound of the events one Task state adds, from its retriers' MaxAttempts. */
export function worstCaseTaskEvents(retriers: { MaxAttempts?: number }[], submitted = false) {
    const attempts = retriers.reduce((sum, r) => sum + (r.MaxAttempts ?? 3), 0);
    return 5 + (submitted ? 1 : 0) + 3 * attempts;
}
/** Events a Map iteration adds around its item processor. */
export const MAP_ITERATION_EVENTS = 2;

/** Fixed name of the child state machine that copies one chunk of batches (<= 80 characters). */
export function largeObjectCopyPartsStateMachineName(config: Config.Config): string {
    return `LargeObjectCopyParts-${config.env.coreStackName}`.substring(0, 80);
}

/** Retrier of the chunk task: only a failed start; a failed child execution is final. */
export const CHUNK_START_RETRY = {
    errors: ["StepFunctions.ExecutionLimitExceeded", "StepFunctions.ThrottlingException"],
    intervalSeconds: 5,
    backoffRate: 2,
    maxAttempts: 6,
    maxDelaySeconds: 120,
};

/** Construct ids, for the tests and the resource list in documentation. */
export const LARGE_OBJECT_COPY_STATUS_RULE_ID = "LargeObjectCopyStatusChangeRule";
export const LARGE_OBJECT_COPY_STATUS_PERMISSION_ID = "LargeObjectCopyStatusChangeInvokePermission";
export const LARGE_OBJECT_COPY_RECONCILE_DLQ_ID = "LargeObjectCopyReconcileDLQ";
export const LARGE_OBJECT_COPY_RECONCILE_DLQ_ALARM_ID = "LargeObjectCopyReconcileDLQAlarm";

function stateMachineLogGroup(
    scope: Construct,
    id: string,
    prefix: string,
    storage: storageResources,
    config: Config.Config
): logs.LogGroup {
    return new logs.LogGroup(scope, id, {
        logGroupName:
            prefix + generateUniqueNameHash(config.env.coreStackName, config.env.account, id, 10),
        // The deployment key when one is configured, as every VAMS log group (CKV_AWS_158 on a
        // deployment without a key matches the other log groups of the stack).
        encryptionKey: storage.encryption.kmsKey,
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
}

/** The child state machine: copy the batches of one chunk, at most maxConcurrentBatches at once. */
function buildCopyPartsStateMachine(
    scope: Construct,
    fun: lambda.IFunction,
    storage: storageResources,
    config: Config.Config
): sfn.StateMachine {
    const copyBatch = engineTask(
        scope,
        "LargeObjectCopyBatch",
        fun,
        {
            action: "copyBatch",
            job: sfn.JsonPath.objectAt("$.job"),
            batch: sfn.JsonPath.objectAt("$.batch"),
        },
        {}
    );
    const copyBatches = new sfn.Map(scope, "LargeObjectCopyBatches", {
        itemsPath: "$.batches",
        maxConcurrency: config.app.largeFileIngestion.maxConcurrentBatches,
        itemSelector: {
            job: sfn.JsonPath.objectAt("$.job"),
            batch: sfn.JsonPath.objectAt("$$.Map.Item.Value"),
        },
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyBatches.itemProcessor(copyBatch);
    const machine = new sfn.StateMachine(scope, "LargeObjectCopyPartsStateMachine", {
        stateMachineName: largeObjectCopyPartsStateMachineName(config),
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(copyBatches),
        logs: {
            destination: stateMachineLogGroup(
                scope,
                "LargeObjectCopyPartsStateMachineLogGroup",
                "/aws/vendedlogs/VAMSStateMachine-LargeObjectCopyParts",
                storage,
                config
            ),
            includeExecutionData: false,
            level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(
        machine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Invokes the copy-engine function and its versions; writes its own log group.",
            },
        ],
        true
    );
    return machine;
}

function buildCopyStateMachine(
    scope: Construct,
    fun: lambda.IFunction,
    storage: storageResources,
    config: Config.Config
): sfn.StateMachine {
    const job = sfn.JsonPath.objectAt("$.job");
    const prepare = engineTask(
        scope,
        "LargeObjectCopyPrepare",
        fun,
        { action: "prepare", job },
        {
            resultSelector: {
                job: sfn.JsonPath.objectAt("$.Payload.job"),
                chunks: sfn.JsonPath.listAt("$.Payload.chunks"),
            },
            resultPath: "$",
        }
    );
    const partsMachine = buildCopyPartsStateMachine(scope, fun, storage, config);
    const copyChunk = new tasks.StepFunctionsStartExecution(scope, "LargeObjectCopyChunk", {
        stateMachine: partsMachine,
        integrationPattern: sfn.IntegrationPattern.RUN_JOB,
        input: sfn.TaskInput.fromObject({
            job: sfn.JsonPath.objectAt("$.job"),
            batches: sfn.JsonPath.listAt("$.batches"),
        }),
        associateWithParent: true,
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyChunk.addRetry({
        errors: CHUNK_START_RETRY.errors,
        interval: Duration.seconds(CHUNK_START_RETRY.intervalSeconds),
        backoffRate: CHUNK_START_RETRY.backoffRate,
        maxAttempts: CHUNK_START_RETRY.maxAttempts,
        maxDelay: Duration.seconds(CHUNK_START_RETRY.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    // One chunk at a time: the child runs maxConcurrentBatches batches at once, so the copy's
    // concurrency stays what maxConcurrentBatches sets.
    const copyChunks = new sfn.Map(scope, "LargeObjectCopyChunks", {
        itemsPath: "$.chunks",
        maxConcurrency: 1,
        itemSelector: {
            job: sfn.JsonPath.objectAt("$.job"),
            batches: sfn.JsonPath.listAt("$$.Map.Item.Value.batches"),
        },
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyChunks.itemProcessor(copyChunk);
    const complete = engineTask(
        scope,
        "LargeObjectCopyComplete",
        fun,
        { action: "complete", job },
        { resultSelector: { job: sfn.JsonPath.objectAt("$.Payload.job") }, resultPath: "$" }
    );
    const notifySucceeded = engineTask(
        scope,
        "LargeObjectCopyNotifySucceeded",
        fun,
        { action: "notify", job, status: "SUCCEEDED" },
        {}
    );
    const fail = engineTask(
        scope,
        "LargeObjectCopyFail",
        fun,
        { action: "fail", job, error: sfn.JsonPath.objectAt("$.error") },
        {
            resultSelector: { refused: sfn.JsonPath.objectAt("$.Payload.refused") },
            resultPath: "$.outcome",
        }
    );
    const notifyFailed = engineTask(
        scope,
        "LargeObjectCopyNotifyFailed",
        fun,
        { action: "notify", job, status: "FAILED" },
        {}
    );
    const failed = new sfn.Fail(scope, "LargeObjectCopyFailed", {
        error: "LargeObjectCopyFailed",
        cause: "The copy did not complete; the job row records the reason.",
    });
    const notifyLost = new sfn.Fail(scope, "LargeObjectCopyNotifyLost", {
        error: "LargeObjectCopyNotifyFailed",
        cause: "The copy's outcome could not be reported to its workflow execution.",
    });
    // A notify that exhausted its retries gets a last, longer attempt (one per outcome, so each
    // keeps the status it reports), so a landed file still counts on its batch and finalizes the
    // run instead of the run waiting out its maximum wait. The notify action counts a job exactly
    // once, so a repeated attempt never double counts.
    const notifySucceededLastAttempt = engineTask(
        scope,
        "LargeObjectCopyNotifySucceededLastAttempt",
        fun,
        { action: "notify", job, status: "SUCCEEDED" },
        { faultRetry: NOTIFY_LAST_ATTEMPT_RETRY }
    );
    const notifyFailedLastAttempt = engineTask(
        scope,
        "LargeObjectCopyNotifyFailedLastAttempt",
        fun,
        { action: "notify", job, status: "FAILED" },
        { faultRetry: NOTIFY_LAST_ATTEMPT_RETRY }
    );
    notifySucceeded.addCatch(notifySucceededLastAttempt, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifyFailed.addCatch(notifyFailedLastAttempt, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifySucceededLastAttempt.addCatch(notifyLost, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifyFailedLastAttempt.addCatch(notifyLost, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    // A refused file (blocked type, over 5 TiB, source changed, cancelled) ends in its own state, so
    // the failed-copy alarm counts only copies that broke. The job row and the upload status or the
    // workflow execution still record the refusal as a failed file.
    const refused = new sfn.Succeed(scope, "LargeObjectCopyRefused", {
        comment: "The file was refused; the job row records the reason.",
    });
    const refusedOrFailed = new sfn.Choice(scope, "LargeObjectCopyRefusedOrFailed")
        .when(
            sfn.Condition.and(
                sfn.Condition.isPresent("$.outcome.refused"),
                sfn.Condition.booleanEquals("$.outcome.refused", true)
            ),
            refused
        )
        .otherwise(failed);
    notifyFailedLastAttempt.next(refusedOrFailed);
    // The failure path always reports, so a waiting workflow is told even when its cleanup fails.
    fail.addCatch(notifyFailed, { errors: [sfn.Errors.ALL], resultPath: "$.cleanupError" });
    const failurePath = fail.next(notifyFailed).next(refusedOrFailed);
    for (const state of [prepare, copyChunks, complete]) {
        state.addCatch(failurePath, { errors: [sfn.Errors.ALL], resultPath: "$.error" });
    }

    const logGroup = stateMachineLogGroup(
        scope,
        "LargeObjectCopyStateMachineLogGroup",
        "/aws/vendedlogs/VAMSStateMachine-LargeObjectCopy",
        storage,
        config
    );

    const stateMachine = new sfn.StateMachine(scope, "LargeObjectCopyStateMachine", {
        stateMachineName: largeObjectCopyStateMachineName(config),
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(
            prepare.next(copyChunks).next(complete).next(notifySucceeded)
        ),
        logs: {
            destination: logGroup,
            // Execution data carries bucket and key names only; level ALL satisfies AwsSolutions-SF1.
            includeExecutionData: false,
            level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(
        stateMachine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Invokes the copy-engine function and its versions; writes its own log group; " +
                    "describes and stops the executions of its child state machine; manages the " +
                    "Step Functions-owned rule startExecution.sync uses to wait on them.",
            },
        ],
        true
    );

    // A failed copy execution is a copy that broke (its job row and the upload or execution record
    // name the file), an outcome that could not be reported, or an execution that ended outside its
    // states. Refused files end in LargeObjectCopyRefused and do not count. No alarm action: VAMS has
    // no notification topic, so operators attach their own.
    new cloudwatch.Alarm(scope, "LargeObjectCopyExecutionsFailedAlarm", {
        alarmName: `${config.name}-${config.app.baseStackName}-largeObjectCopyExecutionsFailed`,
        alarmDescription:
            "A large object copy did not complete or could not report its outcome. The copy job " +
            "row, the upload status or the workflow execution names the file.",
        metric: stateMachine.metricFailed({
            period: Duration.minutes(5),
            statistic: cloudwatch.Stats.SUM,
        }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    return stateMachine;
}
