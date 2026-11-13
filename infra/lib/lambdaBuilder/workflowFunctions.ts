/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import {
    storageResources,
    METADATA_SCHEMA_DATABASE_ENTITY_TYPE_INDEX,
    WORKFLOW_EXECUTION_LOCKS_HOLDER_INDEX,
} from "../nestedStacks/storage/storageBuilder-nestedStack";
import { Service, IAMArn, Partition } from "../helper/service-helper";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    kmsKeyPolicyStatementGenerator,
} from "../helper/security";
import {
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
    grantExternalAssetBucketKmsKeys,
} from "../helper/security";
import * as logs from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";

// Workflow state machines and auto-provisioned pipeline Lambdas are named at runtime by the
// backend with a fixed lowercase 'vams-' prefix that does not embed config.name, so both the
// config-name pattern and this prefix are granted.
const BACKEND_GENERATED_NAME_PATTERN = "vams-*";

// Use-case pipeline sub-state-machines are named by CDK from their construct ids
// pragma: allowlist nextline secret
// (e.g. 'CosmosReasonPipelineCosmosReasonreason2BStateMachineB865922C-YV2FQMz87VHM',
// pragma: allowlist nextline secret
// 'PcPotreeViewerProcessingStateMachine92DD7A15-90NUO0WAz4Ct'), so they carry neither the config
// name nor the 'vams-' prefix. Aborting a workflow execution has to stop the registered sub-execution
// as well, or the parent is marked ABORTED while the pipeline state machine — and the AWS Batch job
// it is waiting on — keeps running. IAM offers no name pattern that covers these, so the abort and
// log-read scope for sub-executions is account/region-wide on Step Functions executions.
const PIPELINE_SUB_STATE_MACHINE_PATTERN = "*";

// One sentence, shared by the abort and failure paths: cdk-nag stamps a reason onto every resource it
// applies to, so its length is a template-body cost (see infra/CLAUDE.md, API stack ceilings).
const SAGEMAKER_SUB_PROCESS_NAG_REASON =
    "SageMaker job and pipeline-execution names are recorded on execution registration rows at run " +
    "time rather than known at deploy time, so the Stop (and Describe) actions are scoped to this " +
    "account and Region's training-job and pipeline-execution namespaces and the handler only ever " +
    "passes an ARN read from a registration row on the execution being aborted or read (ARCC BSC6 " +
    "cnt_aYRj0iNeXVuaiu).";

/**
 * The WorkflowExecutionLocksStorageTable actions the lock code issues, and nothing more: a launch takes
 * its locks with a conditional PutItem and rolls them back with a conditional DeleteItem; every release
 * site deletes the rows the execution holds with a conditional DeleteItem, after listing them with a
 * Query on the holder index. Each is granted on the table ARN (or the one index ARN) alone, not on
 * `index/*` as Table.grant() would add. The customer managed key permissions these calls need come
 * from kmsKeyLambdaPermissionAddToResourcePolicy, which each of the four callers already applies.
 */
export const EXECUTION_LOCK_ACQUIRE_ACTIONS = ["dynamodb:PutItem", "dynamodb:DeleteItem"];
export const EXECUTION_LOCK_RELEASE_ACTIONS = ["dynamodb:DeleteItem"];
/** Construct id of the policy that carries a handler's lock statements. */
export const EXECUTION_LOCK_POLICY_ID = "ExecutionLockPolicy";

function onExecutionLocksTable(storageResources: storageResources, actions: string[]) {
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions,
        resources: [storageResources.dynamo.workflowExecutionLocksStorageTable.tableArn],
    });
}

/** DeleteItem on the lock table and Query on its holder index: what every release site calls. */
function executionLockDeleteStatements(storageResources: storageResources) {
    const locks = storageResources.dynamo.workflowExecutionLocksStorageTable;
    return [
        onExecutionLocksTable(storageResources, EXECUTION_LOCK_RELEASE_ACTIONS),
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:Query"],
            resources: [`${locks.tableArn}/index/${WORKFLOW_EXECUTION_LOCKS_HOLDER_INDEX}`],
        }),
    ];
}

/**
 * A terminal handler that releases an execution's locks finds them two ways: it rebuilds the keys from
 * the workflow's stored concurrencyRestriction (one GetItem on the workflow row) and the execution's
 * input rows (a primary-key Query), and it lists the rows the execution holds through the lock table's
 * holder index. It then deletes those rows from the lock table.
 */
function executionLockReleaseStatements(storageResources: storageResources) {
    return [
        ...executionLockDeleteStatements(storageResources),
        // On the two tables' own ARNs: the release reads the workflow row by key and the execution's
        // input rows by partition key, and neither table's indexes.
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem"],
            resources: [storageResources.dynamo.workflowStorageTableV2.tableArn],
        }),
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:Query"],
            resources: [storageResources.dynamo.workflowExecutionInputsStorageTable.tableArn],
        }),
    ];
}

/**
 * Attaches a handler's lock statements to its role as a policy of their own, so the role's default
 * policy, and the statements CDK splits from it into overflow policies, are the same with or without
 * them. Under VAMS stack role customization the statements are recorded on the pre-created role
 * instead. The function waits for the policy, since its code calls the lock table once deployed.
 */
export function attachExecutionLockPolicy(fun: lambda.Function, statements: iam.PolicyStatement[]) {
    attachHandlerPolicy(fun, EXECUTION_LOCK_POLICY_ID, statements);
}

/** Attaches statements to a handler's role as a policy of their own (see attachExecutionLockPolicy). */
function attachHandlerPolicy(fun: lambda.Function, id: string, statements: iam.PolicyStatement[]) {
    const policy = new iam.Policy(fun, id, { statements });
    fun.role!.attachInlinePolicy(policy);
    fun.node.defaultChild!.node.addDependency(policy);
}

/**
 * handlers/workflows/executionService.py runs as two functions with two roles. EXECUTION_SERVICE_ROUTES
 * names the routes each serves (the handler answers 404 for any other): the execution service serves
 * every execution route but permanent delete, and the permanent-delete function serves that one route
 * and is the only role that can delete execution rows.
 */
export const EXECUTION_SERVICE_ROUTES_ENV = "EXECUTION_SERVICE_ROUTES";
export const EXECUTION_SERVICE_ROUTE_SET_SERVICE = "service";
export const EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE = "permanentDelete";

/** Index of PipelineExecutionsStorageTable on workflowExecutionId: an execution's pipeline rows. */
export const PIPELINE_EXECUTIONS_BY_WORKFLOW_EXECUTION_INDEX = "PipelineExecByWorkflowExecGSI";
/** Index of PipelineTemplateTagSchemaStorageTable on the owning template. */
export const TAG_SCHEMA_BY_TEMPLATE_INDEX = "TagSchemaByTemplateGSI";

/** The seven per-pipeline-execution tables, keyed by pipelineExecutionId. */
function pipelineExecutionSubTables(storageResources: storageResources): dynamodb.Table[] {
    const dynamo = storageResources.dynamo;
    return [
        dynamo.pipelineExecutionInputFilesStorageTable,
        dynamo.pipelineExecutionInputMetadataStorageTable,
        dynamo.pipelineExecutionInputConfigurationStorageTable,
        dynamo.pipelineExecutionOutputFilesStorageTable,
        dynamo.pipelineExecutionOutputMetadataStorageTable,
        dynamo.pipelineExecutionOutputResultsStorageTable,
        dynamo.pipelineExecutionLogsStorageTable,
    ];
}

/** One statement allowing `actions` on `resources` (table ARNs or index ARNs). */
function dynamoStatement(actions: string[], resources: string[]): iam.PolicyStatement {
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: actions.map((action) => `dynamodb:${action}`),
        resources,
    });
}

/**
 * The indexes the execution service queries, by table: the asset and global listings (the main table's
 * by-workflow, by-group and by-date indexes, the inputs' by-asset and the configuration rows'
 * by-output-asset index), an execution's pipeline rows, and the file versions an execution produced.
 */
export const EXECUTION_SERVICE_QUERIED_INDEXES = {
    workflowExecutionsStorageTableV2: [
        "WorkflowExecutionsByWorkflowGSI",
        "WorkflowExecutionsByGroupGSI",
        "WorkflowExecutionsByDateGSI",
    ],
    workflowExecutionInputsStorageTable: ["WorkflowExecInputsByAssetGSI"],
    workflowExecutionConfigurationStorageTable: [
        "WorkflowExecConfigByOutputAssetGSI", // pragma: allowlist secret
    ],
    pipelineExecutionsStorageTable: [PIPELINE_EXECUTIONS_BY_WORKFLOW_EXECUTION_INDEX],
    assetFileVersionHistoryStorageTable: ["WorkflowExecutionIdIndex"],
} as const;

/** Construct id of the customer managed policy that carries the execution service's table reads. */
export const EXECUTION_SERVICE_TABLE_READ_POLICY_ID = "TableReadPolicy";

/**
 * Construct id of the customer managed policy that carries the end-state handler's PutEvents on the
 * orchestration bus (the workflow.execution.completed event).
 */
export const PROCESS_OUTPUT_EVENTS_POLICY_ID = "OrchestrationEventsPolicy";

/**
 * The execution service's table reads: GetItem, BatchGetItem and Query, the only read calls it makes,
 * on every table it reads and on the indexes it queries by exact ARN (not the `index/*`
 * Table.grantReadData() adds). Carried by a customer managed policy (see buildExecutionServiceFunction).
 */
export function executionServiceTableReadStatements(
    storageResources: storageResources
): iam.PolicyStatement[] {
    const dynamo = storageResources.dynamo;
    const indexes = EXECUTION_SERVICE_QUERIED_INDEXES as Record<string, readonly string[]>;
    const readTables: [string, dynamodb.Table][] = [
        ["assetStorageTable", dynamo.assetStorageTable],
        ["workflowExecutionsStorageTableV2", dynamo.workflowExecutionsStorageTableV2],
        ["workflowExecutionInputsStorageTable", dynamo.workflowExecutionInputsStorageTable],
        [
            "workflowExecutionConfigurationStorageTable",
            dynamo.workflowExecutionConfigurationStorageTable,
        ],
        ["pipelineExecutionsStorageTable", dynamo.pipelineExecutionsStorageTable],
        // Every per-pipeline table but the input files, which only permanent delete reads.
        ...pipelineExecutionSubTables(storageResources)
            .filter((t) => t !== dynamo.pipelineExecutionInputFilesStorageTable)
            .map((t) => ["", t] as [string, dynamodb.Table]),
        // Definition tables (V2) are read only to cross-fetch human-readable workflow/pipeline
        // names + descriptions for the detail view.
        ["workflowStorageTableV2", dynamo.workflowStorageTableV2],
        ["pipelineStorageTableV2", dynamo.pipelineStorageTableV2],
        ["assetFileVersionHistoryStorageTable", dynamo.assetFileVersionHistoryStorageTable],
    ];
    return [
        dynamoStatement(
            ["BatchGetItem", "GetItem", "Query"],
            readTables.flatMap(([field, table]) => [
                table.tableArn,
                ...(indexes[field] ?? []).map((name) => `${table.tableArn}/index/${name}`),
            ])
        ),
    ];
}

/**
 * The only rows the execution service writes: the lazy status reconcile and the abort update the main
 * row and mark the pipeline rows. Permanent delete has its own function. The lock table's calls are in
 * the role's lock policy. backend/tests/handlers/workflows/test_execution_service_route_split.py records
 * every write the service's routes make.
 */
export function executionServiceTableWriteStatements(
    storageResources: storageResources
): iam.PolicyStatement[] {
    const dynamo = storageResources.dynamo;
    return [
        dynamoStatement(
            ["UpdateItem"],
            [
                dynamo.workflowExecutionsStorageTableV2.tableArn,
                dynamo.pipelineExecutionsStorageTable.tableArn,
            ]
        ),
    ];
}

/**
 * Attaches statements to a handler's role as a customer managed policy, which IAM does not count against
 * the role's 10,240-character inline policy quota (each managed policy has its own 6,144). The role's
 * ManagedPolicyArns names the policy, so CloudFormation creates and attaches it before the role and its
 * default policy update; statements moving out of the default policy are therefore never missing on
 * upgrade. The function waits for it as well.
 */
function attachManagedHandlerPolicy(
    fun: lambda.Function,
    id: string,
    statements: iam.PolicyStatement[]
) {
    const policy = new iam.ManagedPolicy(fun, id, { statements });
    fun.role!.addManagedPolicy(policy);
    fun.node.defaultChild!.node.addDependency(policy);
}

/**
 * Every DynamoDB call the permanent-delete route makes, each table named once with only the actions it
 * receives, on table ARNs and the one index the route queries:
 * - the main row: read by key and deleted; the configuration row: read and deleted;
 * - the workflow input rows and the seven per-pipeline tables: queried by partition key and deleted
 *   through a batch writer (IAM's BatchWriteItem does not separate puts from deletes);
 * - the pipeline rows: listed through their by-execution index and deleted one by one;
 * - the authorization reads: the workflow definition by key and the run's assets by key.
 * backend/tests/handlers/workflows/test_execution_service_route_split.py records the route's calls
 * against this same list.
 */
export function executionPermanentDeleteDynamoStatements(
    storageResources: storageResources
): iam.PolicyStatement[] {
    const dynamo = storageResources.dynamo;
    return [
        dynamoStatement(
            ["Query", "DeleteItem"],
            [dynamo.workflowExecutionsStorageTableV2.tableArn]
        ),
        dynamoStatement(
            ["GetItem", "DeleteItem"],
            [dynamo.workflowExecutionConfigurationStorageTable.tableArn]
        ),
        dynamoStatement(
            ["Query", "BatchWriteItem"],
            [
                dynamo.workflowExecutionInputsStorageTable.tableArn,
                ...pipelineExecutionSubTables(storageResources).map((t) => t.tableArn),
            ]
        ),
        dynamoStatement(["DeleteItem"], [dynamo.pipelineExecutionsStorageTable.tableArn]),
        dynamoStatement(
            ["Query"],
            [
                `${dynamo.pipelineExecutionsStorageTable.tableArn}/index/${PIPELINE_EXECUTIONS_BY_WORKFLOW_EXECUTION_INDEX}`,
                dynamo.assetStorageTable.tableArn,
            ]
        ),
        dynamoStatement(["GetItem"], [dynamo.workflowStorageTableV2.tableArn]),
    ];
}

/**
 * Every DynamoDB call the execute handler makes, each table named once with only the actions it
 * receives, on table ARNs and the one index the handler queries:
 * - the launch inputs: the workflow and pipeline definitions by key, the assets and their bucket rows
 *   by key, the default bucket by a filtered scan, the template rows by key and by pipeline, and the
 *   tag schema by its template index;
 * - the execution records: the main row (and its UpdateItem, which the skip record of a launch refused
 *   by a concurrency restriction is written with), the workflow input and configuration rows, the
 *   pipeline rows and their configuration snapshots, and the input-metadata rows through a batch writer.
 * The lock table's calls are in the role's lock policy, and the metadata schema reads in a policy of
 * their own. backend/tests/handlers/workflows/test_execute_workflow_dynamodb_calls.py records the
 * handler's calls against this same list.
 */
export function executeWorkflowDynamoStatements(
    storageResources: storageResources
): iam.PolicyStatement[] {
    const dynamo = storageResources.dynamo;
    return [
        dynamoStatement(
            ["GetItem"],
            [dynamo.workflowStorageTableV2.tableArn, dynamo.pipelineStorageTableV2.tableArn]
        ),
        dynamoStatement(
            ["Query"],
            [
                dynamo.assetStorageTable.tableArn,
                `${dynamo.pipelineTemplateTagSchemaStorageTable.tableArn}/index/${TAG_SCHEMA_BY_TEMPLATE_INDEX}`,
            ]
        ),
        dynamoStatement(["Query", "Scan"], [dynamo.s3AssetBucketsStorageTable.tableArn]),
        dynamoStatement(["GetItem", "Query"], [dynamo.pipelineTemplatesStorageTable.tableArn]),
        dynamoStatement(
            ["GetItem", "PutItem", "UpdateItem"],
            [dynamo.workflowExecutionsStorageTableV2.tableArn]
        ),
        dynamoStatement(
            ["PutItem"],
            [
                dynamo.workflowExecutionInputsStorageTable.tableArn,
                dynamo.workflowExecutionConfigurationStorageTable.tableArn,
                dynamo.pipelineExecutionsStorageTable.tableArn,
                dynamo.pipelineExecutionInputConfigurationStorageTable.tableArn,
            ]
        ),
        dynamoStatement(
            ["BatchWriteItem"],
            [dynamo.pipelineExecutionInputMetadataStorageTable.tableArn]
        ),
    ];
}

/** The Step Functions execution ARN patterns a VAMS workflow execution runs under. */
function workflowExecutionArnPatterns(config: Config.Config): string[] {
    return [
        IAMArn("*" + config.name + "*").statemachineExecution,
        IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
    ];
}

/** Construct id of the policy that carries the execute handler's metadata schema reads. */
export const EXECUTE_METADATA_SCHEMA_READ_POLICY_ID = "MetadataSchemaReadPolicy";

/**
 * The reads the execute handler makes for metadataInputs.metadataSchema, and nothing more: a Query on
 * the schema table's database/entity-type index for each involved database and entity type, and a
 * GetItem on the database table for that database's restrictMetadataOutsideSchemas flag. Each is
 * granted on the one index ARN or the table ARN, not on `index/*` as Table.grantReadData() would add.
 * Attached as a policy of its own, like the lock statements, so the role's default policy and its
 * overflow split are the same as before. The customer managed key permissions come from
 * kmsKeyLambdaPermissionAddToResourcePolicy, which the execute handler already applies.
 */
function executeMetadataSchemaReadStatements(storageResources: storageResources) {
    return [
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:Query"],
            resources: [
                `${storageResources.dynamo.metadataSchemaStorageTableV2.tableArn}/index/${METADATA_SCHEMA_DATABASE_ENTITY_TYPE_INDEX}`,
            ],
        }),
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem"],
            resources: [storageResources.dynamo.databaseStorageTable.tableArn],
        }),
    ];
}

export function buildExecutionServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "executionService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). The log group each execution
        // was launched against is read from that execution's own record (executionLogGroupArn), so
        // no log group ARN is set here; the read scope is granted on the role policy below.
        // Permanent delete is served by its own function (buildExecutionPermanentDeleteFunction).
        environment: {
            [EXECUTION_SERVICE_ROUTES_ENV]: EXECUTION_SERVICE_ROUTE_SET_SERVICE,
            // Abort and the lazy status reconcile publish workflow.execution.completed once the
            // terminal status is written.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });
    storageResources.eventBridge.orchestrationBus.grantPutEventsTo(fun);
    // The table reads are the role's largest statement (about two dozen table and index ARNs, each
    // carrying the stack name), so they sit in a managed policy of their own and the inline policies
    // keep their headroom under IAM's quota in every partition (infra/CLAUDE.md, IAM inline policy
    // budget).
    attachManagedHandlerPolicy(
        fun,
        EXECUTION_SERVICE_TABLE_READ_POLICY_ID,
        executionServiceTableReadStatements(storageResources)
    );
    for (const statement of executionServiceTableWriteStatements(storageResources)) {
        fun.addToRolePolicy(statement);
    }
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // DescribeExecution/StopExecution + GetExecutionHistory (the whole-execution log view
            // renders the Step Functions execution history) act on executions; DescribeStateMachine
            // resolves a registered sub-SFN's CloudWatch log group from its state-machine ARN.
            actions: [
                "states:DescribeExecution",
                "states:StopExecution",
                "states:GetExecutionHistory",
                "states:DescribeStateMachine",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
                // Registered pipeline sub-executions (CDK-generated names — see the constant above).
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachine,
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachineExecution,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Terminate a Batch job a pipeline registered as an abortable sub-process. Needed only
            // for jobs a pipeline submits ITSELF: a job submitted through the Step Functions `.sync`
            // Batch integration is stopped by StopExecution on its state machine (granted above).
            // AWS Batch generates job ids with no deployment-specific prefix to scope on, so the
            // resource is a wildcard; the abort path only ever passes an id read from a registration
            // row on the execution being aborted.
            actions: ["batch:TerminateJob"],
            resources: ["*"],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Resolve a registered Batch job's status and container log stream for the execution
            // details and logs APIs. DescribeJobs supports no resource-level scoping (its only
            // resource type is `*`); the handler only ever passes job ids read from registration
            // rows of the execution being read.
            actions: ["batch:DescribeJobs"],
            resources: ["*"],
        })
    );
    // Gated the same way the createJob grant is: with the execution type disabled no pipeline can be
    // registered as DeadlineCloud, so no execution can hold a job to cancel or report on, and the
    // grants would be standing access to every farm in the account for code paths that cannot run.
    if (config.app.pipelines.deadlineCloudExecutionTypeEnabled) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                // Cancel a Deadline Cloud farm job on abort. The task is
                // `createJob.waitForTaskToken`, so Step Functions holds the token and NOT the job —
                // StopExecution on the state machine leaves the job running (and billing) on the
                // farm, which is why an explicit cancel is required here.
                //
                // ListJobs + GetJob are what make a QUEUED job cancellable. The job is otherwise
                // identified from a registration written by the job-status callback, and that
                // callback only runs on a status CHANGE — a job sitting queued with no worker never
                // produces one. Discovery walks the pipeline's queue and matches the reserved
                // VamsPipelineExecutionId job parameter instead, so it needs no event to have
                // occurred.
                //
                // GetJob also resolves a registered job's name and status for the execution details
                // view. ListSessions lists the job's sessions for the logs API: each session is one
                // CloudWatch log stream in the queue's shared '/aws/deadline/{farmId}/{queueId}'
                // group, and the lines carry no VAMS id, so the streams are read by exact session
                // id (log-group grant below).
                actions: [
                    "deadline:UpdateJob",
                    "deadline:ListJobs",
                    "deadline:GetJob",
                    "deadline:ListSessions",
                ],
                // Farm and queue ids come from the pipeline record, not from configuration, so they
                // are not known at deploy time; the handler only ever passes ids read from the
                // execution being aborted or read. Scoped to the account/Region's Deadline resources.
                resources: [
                    `arn:${Partition()}:deadline:${config.env.region}:${config.env.account}:farm/*`,
                ],
            })
        );
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
                // Deadline Cloud writes session logs to the queue's own group
                // '/aws/deadline/{farmId}/{queueId}', one stream per session, shared by every job of
                // the queue. Farm and queue ids come from the pipeline record, so the group is not
                // known at deploy time; the handler only ever reads the streams named by the session
                // ids ListSessions returned for a job registered on the execution being read. Suffixed
                // with ':*' for stream-level reads, as the log-group statement below.
                resources: [
                    IAMArn("/aws/deadline/*").loggroup,
                    IAMArn("/aws/deadline/*").loggroup + ":*",
                ],
            })
        );
        NagSuppressions.addResourceSuppressions(
            fun,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Deadline Cloud farm, queue and job ids are recorded on VAMS pipeline records rather than " +
                        "known at deploy time, so deadline:UpdateJob/ListJobs/GetJob/ListSessions are scoped to " +
                        "this account and Region's farm hierarchy. The handler only ever passes a farm/queue read " +
                        "from the pipeline definition of the execution being aborted or read, and a job id it " +
                        "either read from that execution's registration rows or matched by the execution's own id " +
                        "in a reserved job parameter.",
                    appliesTo: [{ regex: "/^Resource::arn:.*:deadline:.*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Deadline Cloud session logs land in the queue's own '/aws/deadline/<farmId>/<queueId>' " +
                        "log group, and farm and queue ids are recorded on VAMS pipeline records rather than " +
                        "known at deploy time, so the logs read is scoped to this account and Region's Deadline " +
                        "log-group namespace. The handler only ever reads the streams named by the session ids " +
                        "Deadline Cloud returned for a job registered on the execution being read.",
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:.*:logs:.*:log-group:/aws/deadline/\\*(:\\*)?$/g",
                        },
                    ],
                },
            ],
            true
        );
    }
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Stop a SageMaker training job or pipeline execution a pipeline registered as an abortable
            // sub-process, and describe either for the execution details view. Job and pipeline
            // execution names are recorded on registration rows at run time, so the resources are this
            // account and Region's training-job and pipeline-execution namespaces; the handler only
            // ever passes an ARN read from a registration row on the execution being aborted or read.
            actions: [
                "sagemaker:StopTrainingJob",
                "sagemaker:StopPipelineExecution",
                "sagemaker:DescribeTrainingJob",
                "sagemaker:DescribePipelineExecution",
            ],
            resources: [
                `arn:${Partition()}:sagemaker:${config.env.region}:${
                    config.env.account
                }:training-job/*`,
                `arn:${Partition()}:sagemaker:${config.env.region}:${
                    config.env.account
                }:pipeline/*/execution/*`,
            ],
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: SAGEMAKER_SUB_PROCESS_NAG_REASON,
                appliesTo: [{ regex: "/^Resource::arn:.*:sagemaker:.*$/g" }],
            },
        ],
        true
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Read scope covers the whole-execution and per-pipeline-step log sources the logs API
            // reads. Two groups are VAMS's own and always granted:
            //   (1) the shared workflow SFN log group — granted by its actual ARN (name is
            //       '/aws/vendedlogs/vamsPipelineWorkflows<hash>', lowercase 'vams');
            //   (2) config-name-based groups (the audit/log groups that embed the config name).
            // Every other group a pipeline may register as a per-stage log source comes from
            // app.pipelines.executionLogReadPrefixes, used verbatim as log-group resource names (a
            // trailing '*' is a prefix). IAM resource matching is CASE-SENSITIVE, which is why the
            // defaults list BOTH '/aws/vendedlogs/VAMSStateMachine-*' and
            // '/aws/vendedlogs/VAMSstateMachine-*' (pipeline state machines), the pipeline container
            // group '/aws/vendedlogs/Pipelines/*' (the ECS pipelines and the seven Fargate Batch
            // pipelines: coordinate transform, Blender renderer, 3D thumbnail, PDAL, Potree, video
            // SOP/BOM extraction, and the GenAI CAD STEP agent on its `fargate` runtime), AWS Batch's
            // default container group
            // '/aws/batch/job' and SageMaker's '/aws/sagemaker/TrainingJobs'. getConfig() rejects
            // '*', '/' and anything outside '/aws/', so the grant can never widen to the whole
            // vended-log namespace. Each entry is suffixed with ':*' for stream-level reads.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
                ...(
                    config.app.pipelines.executionLogReadPrefixes ??
                    Config.DEFAULT_EXECUTION_LOG_READ_PREFIXES
                ).flatMap((prefix) => [IAMArn(prefix).loggroup, IAMArn(prefix).loggroup + ":*"]),
            ],
        })
    );
    // Concurrency lock rows are released when an execution is aborted and when a read observes a run
    // stopped outside VAMS. The workflow and input rows the release reads are granted above already,
    // so only the lock-table calls are attached.
    attachExecutionLockPolicy(fun, executionLockDeleteStatements(storageResources));
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Permanent delete of an execution's DynamoDB rows (DELETE /workflows/executions/{executionId}/permanent),
 * on its own role. The same handler module as the execution service, serving only this route, so the
 * role that can delete every execution row is reachable through one admin-gated route alone and the
 * execution service's role holds no delete.
 */
export function buildExecutionPermanentDeleteFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const fun = new lambda.Function(scope, "executionPermanentDelete", {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: "handlers.workflows.executionService.lambda_handler",
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {
            [EXECUTION_SERVICE_ROUTES_ENV]: EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE,
        },
    });
    for (const statement of executionPermanentDeleteDynamoStatements(storageResources)) {
        fun.addToRolePolicy(statement);
    }
    // The in-progress guard: a row not yet terminal is deleted only once its Step Functions execution
    // is confirmed stopped.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:DescribeExecution"],
            resources: workflowExecutionArnPatterns(config),
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    return fun;
}

/**
 * The launch handler's timeout. It is also passed to the handler, which takes over a lock row older
 * than this (plus a margin) whose holder wrote no execution record: that launch died mid-way.
 */
export const EXECUTE_WORKFLOW_TIMEOUT = Duration.minutes(15);

/**
 * Asset-less multi-file execute handler. Reads the V2 workflow/pipeline/template/tag-schema tables +
 * the buckets table (default run bucket + per-input asset buckets), writes the V2 execution records,
 * and starts the workflow state machine. Run I/O lives in the default asset bucket; input files are
 * read from their own asset buckets and output write-back targets the output asset bucket, so it
 * needs read/write across all asset buckets.
 */
export function buildExecuteWorkflowV2Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    metadataServiceFunction: lambda.IFunction,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "executeWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: EXECUTE_WORKFLOW_TIMEOUT,
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            METADATA_SERVICE_LAMBDA_FUNCTION_NAME: metadataServiceFunction.functionName,
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
            // Block launching an execution whose referenced pipeline is DeadlineCloud when the type
            // is disabled (covers a pipeline created while enabled, then the deployment disabling it).
            DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED: config.app.pipelines
                .deadlineCloudExecutionTypeEnabled
                ? "true"
                : "false",
            EXECUTE_WORKFLOW_TIMEOUT_SECONDS: EXECUTE_WORKFLOW_TIMEOUT.toSeconds().toString(),
        },
    });

    for (const statement of executeWorkflowDynamoStatements(storageResources)) {
        fun.addToRolePolicy(statement);
    }
    // Concurrency locks: taken before the state machine starts (a conflicting row whose holder has no
    // main execution row, read above, may be taken over), released when a launch fails after acquiring.
    attachExecutionLockPolicy(fun, [
        onExecutionLocksTable(storageResources, EXECUTION_LOCK_ACQUIRE_ACTIONS),
    ]);
    // metadataInputs.metadataSchema: the schema records and each schema database's restrict flag.
    attachHandlerPolicy(
        fun,
        EXECUTE_METADATA_SCHEMA_READ_POLICY_ID,
        executeMetadataSchemaReadStatements(storageResources)
    );
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    metadataServiceFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // StopExecution compensates a launch whose execution records could not be written: the
            // state machine is already running, so it is stopped rather than left with no VAMS
            // records (invisible to the executions list and unreachable by the abort API).
            actions: [
                "states:StartExecution",
                "states:StopExecution",
                "states:DescribeStateMachine",
                "states:DescribeExecution",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
            ],
        })
    );
    return fun;
}

export function buildProcessWorkflowExecutionOutputFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    fileUploadLambdaFunction: lambda.Function,
    metadataServiceFunction: lambda.IFunction,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "processWorkflowExecutionOutput";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
            FILE_UPLOAD_LAMBDA_FUNCTION_NAME: fileUploadLambdaFunction.functionName,
            METADATA_SERVICE_LAMBDA_FUNCTION_NAME: metadataServiceFunction.functionName,
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            // The workflow.execution.completed event is published to the orchestration bus.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });

    fileUploadLambdaFunction.grantInvoke(fun);
    metadataServiceFunction.grantInvoke(fun);
    // This role is a named exception to the IAM inline policy budget (its execution table grants), so
    // the completion event's PutEvents sits in a customer managed policy of its own rather than
    // adding to the inline total (infra/CLAUDE.md, IAM Inline Policy Budget).
    attachManagedHandlerPolicy(fun, PROCESS_OUTPUT_EVENTS_POLICY_ID, [
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["events:PutEvents"],
            resources: [storageResources.eventBridge.orchestrationBus.eventBusArn],
        }),
    ]);

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetUploadsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadWriteData(fun);
    // Records the run's outputTypes on the configuration row created at launch: a single UpdateItem.
    storageResources.dynamo.workflowExecutionConfigurationStorageTable.grant(
        fun,
        "dynamodb:UpdateItem"
    );
    // The end-state step releases the run's concurrency locks.
    attachExecutionLockPolicy(fun, executionLockReleaseStatements(storageResources));
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Reads the shared workflow SFN log group to capture a run's logs on completion. Granted
            // by that group's actual ARN (its name embeds lowercase 'vams'); the config-name pattern
            // is kept as a superset for any other VAMS-named group this handler may read.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
            ],
        })
    );

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildInterimPipelineTrackingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "interimPipelineTracking";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Only non-SSM
            // values are set here: the orchestration bus ARN + event source prefix written into
            // each next pipeline's manifest.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });
    // The main execution row belongs to the launch, interim-status and end-state handlers; this
    // lambda advances only the per-pipeline rows, so the main table is read only.
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionInputsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionInputConfigurationStorageTable.grantReadData(fun);
    // The Region-routing S3 client resolves a bucket name's Region through the buckets table.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Reads original input files + output-files folder, and writes the next pipeline's
    // resolved input manifest into the asset bucket execution input folder.
    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildHandleExecutionErrorFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "handleExecutionError";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Only the
            // non-SSM shared workflow SFN log group ARN is set here (used to pull failed-run logs).
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            // The workflow.execution.completed event is published to the orchestration bus.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadWriteData(fun);
    storageResources.eventBridge.orchestrationBus.grantPutEventsTo(fun);
    // A failed run's outputTypes are derived from whatever output rows exist at failure (one
    // existence query per output table) and recorded on the launch-time configuration row.
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowExecutionConfigurationStorageTable.grant(
        fun,
        "dynamodb:UpdateItem"
    );
    // The error handler releases the failed run's concurrency locks.
    attachExecutionLockPolicy(fun, executionLockReleaseStatements(storageResources));
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Pulls the failed run's logs from the shared workflow SFN log group; granted by that
            // group's actual ARN, plus the config-name pattern as a superset for other VAMS groups.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Stop the Step Functions sub-execution an in-flight pipeline registered, before its row
            // is stamped terminal — a terminal row is no longer a candidate for the abort API, so a
            // sub-execution left running here has no in-product remedy. Scoped exactly as the abort
            // path's grant is: the two act on the same registered sub-executions.
            actions: ["states:StopExecution"],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
                // Registered pipeline sub-executions (CDK-generated names — see the constant above).
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachine,
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachineExecution,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Terminate a Batch job a pipeline submitted ITSELF and registered: a job submitted
            // through the Step Functions `.sync` Batch integration is stopped by StopExecution on
            // its state machine (granted above). AWS Batch generates job ids with no
            // deployment-specific prefix to scope on, so the resource is a wildcard; the handler
            // only ever passes an id read from a registration row on the failing execution.
            actions: ["batch:TerminateJob"],
            resources: ["*"],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Stop a SageMaker training job or pipeline execution a pipeline this failure is about to
            // stamp terminal registered as its sub-process, and describe it when the stop is refused so
            // the refusal is classified by its live status. Scoped exactly as the abort path's grant is:
            // the two share the stop implementation and act on the same registration rows.
            actions: [
                "sagemaker:StopTrainingJob",
                "sagemaker:StopPipelineExecution",
                "sagemaker:DescribeTrainingJob",
                "sagemaker:DescribePipelineExecution",
            ],
            resources: [
                `arn:${Partition()}:sagemaker:${config.env.region}:${
                    config.env.account
                }:training-job/*`,
                `arn:${Partition()}:sagemaker:${config.env.region}:${
                    config.env.account
                }:pipeline/*/execution/*`,
            ],
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: SAGEMAKER_SUB_PROCESS_NAG_REASON,
                appliesTo: [{ regex: "/^Resource::arn:.*:sagemaker:.*$/g" }],
            },
        ],
        true
    );
    // Gated the same way the abort path's grant is: with the execution type disabled no pipeline can
    // be registered as DeadlineCloud, so no failing execution can hold a job to cancel.
    if (config.app.pipelines.deadlineCloudExecutionTypeEnabled) {
        // Resolves a DeadlineCloud pipeline's farmId/queueId, which live on the pipeline DEFINITION
        // rather than on any execution row, so a job that was never registered can still be found.
        storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                // Cancel a Deadline Cloud farm job held by a pipeline this failure is about to stamp
                // terminal. The task is `createJob.waitForTaskToken`, so Step Functions holds the
                // token and NOT the job, and a terminal row is no longer a candidate for the abort
                // API — so a job left running here has no in-product remedy.
                //
                // ListJobs + GetJob are what make a QUEUED job cancellable. The job is otherwise
                // identified from a registration written by the job-status callback, and that
                // callback only runs on a status CHANGE — a job sitting queued with no worker never
                // produces one. Discovery walks the pipeline's queue and matches the reserved
                // VamsPipelineExecutionId job parameter instead, so it needs no event to have
                // occurred. Same three actions as the abort path, which shares the implementation.
                actions: ["deadline:UpdateJob", "deadline:ListJobs", "deadline:GetJob"],
                // Farm and queue ids come from the pipeline record, not from configuration, so they
                // are not known at deploy time. Scoped to the account/Region's Deadline resources.
                resources: [
                    `arn:${Partition()}:deadline:${config.env.region}:${config.env.account}:farm/*`,
                ],
            })
        );
        NagSuppressions.addResourceSuppressions(
            fun,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Deadline Cloud farm, queue and job ids are recorded on VAMS pipeline records rather than " +
                        "known at deploy time, so deadline:UpdateJob/ListJobs/GetJob are scoped to this account " +
                        "and Region's farm hierarchy. The handler only ever passes a farm/queue read from the " +
                        "failing execution's own pipeline definition, and a job id either read from a registration " +
                        "row on that execution or matched to it by the reserved VamsPipelineExecutionId parameter.",
                    appliesTo: [{ regex: "/^Resource::arn:.*:deadline:.*$/g" }],
                },
            ],
            true
        );
    }
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    // Scoped IAM5 suppression for this function's wildcard log-group read resource
    // (IAMArn("*"+config.name+"*").loggroup), so it does not depend on a stack-wide blanket.
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildRegisterPipelineExecutionFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "registerPipelineExecution";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-adjacent execution handler (EventBridge-invoked) under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX), set by
        // globalLambdaEnvironmentsAndPermissions below; no per-table env vars are needed.
        environment: {},
    });
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);

    // Standing EventBridge rule: route this deployment's pipeline.execution.register events to
    // the registration lambda. One rule matches every execution/pipeline by the source prefix;
    // the lambda routes to the exact pipeline row by detail.pipelineExecutionId.
    const registerRule = new events.Rule(scope, "PipelineExecutionRegisterRule", {
        eventBus: storageResources.eventBridge.orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
            detailType: ["pipeline.execution.register"],
        },
    });

    // The registration event is what makes an execution abortable: `execution abort` can only stop the
    // sub-state-machines and Batch jobs that were registered against the pipeline execution row. With
    // no dead-letter queue, EventBridge discards a persistently failing delivery after its own retries
    // and nothing records that it happened — leaving orphaned pipeline state machines and GPU Batch
    // jobs that keep incurring cost after the user aborted the workflow. Same reasoning, and the same
    // treatment, as the Deadline Cloud job-status rules.
    const registerDlq = new sqs.Queue(scope, "PipelineExecutionRegisterDLQ", {
        encryption: storageResources.encryption.kmsKey
            ? sqs.QueueEncryption.KMS
            : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: storageResources.encryption.kmsKey,
        enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(registerDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target for the pipeline-execution registration " +
                "EventBridge rule, so it does not take a further dead-letter queue. Its messages are the " +
                "undeliverable registration events an operator redrives; until they are, the affected " +
                "executions cannot be aborted.",
        },
    ]);

    registerRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: registerDlq,
            retryAttempts: 3,
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    return fun;
}

/**
 * File-upload workflow trigger dispatcher. An asset file upload publishes an `asset.file.uploaded`
 * event to the orchestration bus; a standing rule (deployment event-source prefix + that detail-type)
 * targets a durable SQS buffer this lambda consumes. Per uploaded file it enumerates the fileUpload
 * trigger rows (WorkflowTriggersTable TriggersByBaseTypeGSI), matches inputFileFilters + database
 * scope, and invokes executeWorkflowV2 (as SYSTEM_USER) per firing trigger. Its own SQS buffer + DLQ
 * isolate the fan-out from the invoking upload request.
 */
export function buildWorkflowTriggerDispatchFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    executeWorkflowV2Function: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const kmsEncryptionKey = storageResources.encryption.kmsKey;
    // Durable buffer: a single upload action can fan out to many files; SQS gives batching + retry.
    const dispatchDlq = new sqs.Queue(scope, "WorkflowTriggerDispatchDLQ", {
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
    });
    const dispatchQueue = new sqs.Queue(scope, "WorkflowTriggerDispatchQueue", {
        visibilityTimeout: Duration.seconds(960), // > the 900s function timeout
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
        deadLetterQueue: { queue: dispatchDlq, maxReceiveCount: 3 },
    });
    dispatchQueue.grantSendMessages(Service("EVENTS").Principal);

    const name = "workflowTriggerDispatch";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // EventBridge/SQS-invoked execution handler under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME: executeWorkflowV2Function.functionName,
        },
    });
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Read the candidate workflow's systemConfig.allowWorkflowTriggerChaining, which decides whether
    // a file written by ANOTHER workflow may fire this workflow's trigger. Read from the workflow
    // record rather than mirrored onto the trigger row, so it cannot go stale; only consulted for a
    // workflow-written file, so an ordinary upload performs no extra reads.
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    executeWorkflowV2Function.grantInvoke(fun);
    // Reads uploaded-object metadata to resolve the asset (head_object across asset buckets).
    grantReadPermissionsToAllAssetBuckets(fun);

    // Standing rule: route this deployment's asset.file.uploaded events to the dispatch buffer.
    const uploadRule = new events.Rule(scope, "WorkflowFileUploadTriggerRule", {
        eventBus: storageResources.eventBridge.orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
            detailType: ["asset.file.uploaded"],
        },
    });
    uploadRule.addTarget(new eventsTargets.SqsQueue(dispatchQueue));

    // Setup event source mapping for the dispatch buffer with GovCloud support
    dispatchQueue.grantConsumeMessages(fun);
    if (config.app.govCloud.enabled) {
        const esmDispatch = new lambda.EventSourceMapping(
            scope,
            "WorkflowTriggerDispatchSqsEventSource",
            {
                eventSourceArn: dispatchQueue.queueArn,
                target: fun,
                batchSize: 10,
                maxBatchingWindow: Duration.seconds(3),
            }
        );
        const cfnEsmDispatch = esmDispatch.node.defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmDispatch.addPropertyDeletionOverride("Tags");
    } else {
        fun.addEventSource(
            new eventsources.SqsEventSource(dispatchQueue, {
                batchSize: 10,
                maxBatchingWindow: Duration.seconds(3),
            })
        );
    }

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildDeadlineCloudJobCallbackFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "deadlineCloudJobCallback";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-adjacent execution handler (EventBridge-invoked) under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Orchestration bus + event source prefix for registering the Deadline job as
            // the pipeline execution's sub-process (same registration contract pipelines use).
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });

    // Deadline Cloud publishes job status events to the account DEFAULT bus only. Two rules
    // route job status to the callback: every combined task-run status change, and lifecycle
    // failure states (a job that fails at CREATE/UPLOAD never reaches a task-run status, so
    // its task token would otherwise only resolve by timing out). The lambda additionally
    // ignores jobs without the reserved VamsTaskToken job parameter.
    //
    // The status rule deliberately does NOT filter to terminal statuses. The callback does two
    // jobs, and they need the events at different times: it resolves the task token only on a
    // TERMINAL status, but it REGISTERS the Deadline job as the pipeline execution's sub-process on
    // EVERY status it is handed. Registration is what makes the job abortable — `execution abort`
    // cancels registered Deadline jobs via UpdateJob, and it can only cancel what has been
    // registered. Filtering the rule to terminal statuses meant a job was registered exactly when
    // there was nothing left to cancel: aborting an in-flight execution stopped the state machine
    // and left the farm job running with a task token nobody would ever resolve. The lambda side of
    // this was already correct (backend test
    // `test_non_terminal_status_registers_the_job_and_leaves_the_token_open`, S2-BACKEND-045); the
    // filter here is what kept it unreachable in practice.
    // The handler re-raises GetJob/SendTask* failures so EventBridge retries delivery. Without a
    // dead-letter queue a persistently failing terminal event is discarded after those retries and
    // the workflow's task token is left to time out with no operator-visible signal.
    const kmsEncryptionKey = storageResources.encryption.kmsKey;
    const deadlineCallbackDlq = new sqs.Queue(scope, "DeadlineCloudJobCallbackDLQ", {
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(deadlineCallbackDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target for the Deadline Cloud job-status EventBridge rules, " +
                "so it does not take a further dead-letter queue. Its messages are the undeliverable terminal " +
                "job events an operator redrives after fixing the underlying Deadline Cloud access failure.",
        },
    ]);

    const deadlineJobStatusRule = new events.Rule(scope, "DeadlineCloudJobStatusRule", {
        eventPattern: {
            source: ["aws.deadline"],
            detailType: ["Job Run Status Change"],
        },
    });
    deadlineJobStatusRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: deadlineCallbackDlq,
            retryAttempts: 3,
        })
    );

    const deadlineJobLifecycleRule = new events.Rule(scope, "DeadlineCloudJobLifecycleRule", {
        eventPattern: {
            source: ["aws.deadline"],
            detailType: ["Job Lifecycle Status Change"],
            detail: {
                lifecycleStatus: ["CREATE_FAILED", "UPLOAD_FAILED"],
            },
        },
    });
    deadlineJobLifecycleRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: deadlineCallbackDlq,
            retryAttempts: 3,
        })
    );

    const deadlineArnBase = `arn:${Partition()}:deadline:${config.env.region}:${
        config.env.account
    }`;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["deadline:GetJob"],
            resources: [
                `${deadlineArnBase}:farm/*/queue/*`,
                `${deadlineArnBase}:farm/*/queue/*/job/*`,
            ],
        })
    );
    // Task tokens are opaque (not resource-scoped), so SendTask* uses the same
    // account/region-wide states resource the pipeline callback lambdas use.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${Partition()}:states:${config.env.region}:${config.env.account}:*`],
        })
    );
    storageResources.eventBridge.orchestrationBus.grantPutEventsTo(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Deadline Cloud job submission targets operator-owned farms/queues whose IDs are stored in " +
                    "pipeline records rather than known at deploy time, so deadline:GetJob is scoped to this " +
                    "account/region's farms, queues and jobs by wildcard. Step Functions task tokens are opaque " +
                    "values that carry no resource ARN, so states:SendTaskSuccess/SendTaskFailure cannot be " +
                    "scoped below the account/region — the same scope the pipeline callback lambdas use.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:deadline:.*$/g",
                    },
                    {
                        regex: "/^Resource::arn:.*:states:.*$/g",
                    },
                ],
            },
        ],
        true
    );
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildWorkflowRole(
    scope: Construct,
    processWorkflowExecutionOutputFunction: lambda.Function,
    config: Config.Config,
    kmsKey?: kms.IKey
): iam.Role {
    const createWorkflowPolicy = new iam.PolicyDocument({
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "logs:CreateLogDelivery",
                    "logs:GetLogDelivery",
                    "logs:UpdateLogDelivery",
                    "logs:DeleteLogDelivery",
                    "logs:ListLogDeliveries",
                    "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                //"*"" Resource policy required as per AWS documentation as CloudWatch API doesn't support resource types
                //https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
                resources: ["*"],
            }),
        ],
    });

    //https://docs.aws.amazon.com/step-functions/latest/dg/stepfunctions-iam.html
    const runWorkflowPolicy = new iam.PolicyDocument({
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "cloudwatch:PutMetricData",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                //"*"" Resource policy required as per AWS documentation as CloudWatch API doesn't support resource types
                //https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
                resources: ["*"],
            }),
            // Add permissions for all asset buckets from the global array
            ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                const prefix = record.prefix || "/";
                // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
                // leading slash from the prefix so the '/' separator after the bucket
                // ARN is always present (root prefix yields {bucketArn}/*).
                const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                const objectPrefix = normalizedPrefix.replace(/^\/+/, "");

                return new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:ListBucket", "s3:PutObject", "s3:GetObject"],
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}/${objectPrefix}*`,
                    ],
                });
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [processWorkflowExecutionOutputFunction.functionArn],
            }),
            // For lambda pipelines created through lambda functions
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [
                    IAMArn("*" + config.name + "*").lambda,
                    IAMArn(BACKEND_GENERATED_NAME_PATTERN).lambda,
                ],
            }),
            // SQS SendMessage permission for SQS pipeline types
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["sqs:SendMessage"],
                resources: [IAMArn("*" + config.name + "*").sqs],
            }),
            // EventBridge PutEvents permission for EventBridge pipeline types
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["events:PutEvents"],
                resources: [IAMArn("*" + config.name + "*").eventBus, IAMArn("default").eventBus],
            }),

            // AWS X-Ray publishes no resource-level permissions for these actions, so the resource is
            // "*" of necessity.
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources: ["*"],
            }),
        ],
    });

    // Deadline Cloud CreateJob permission for DeadlineCloud pipeline types. Job submission
    // targets operator-owned farms/queues whose IDs live in pipeline records, so the resource
    // covers any farm/queue (and job under it) in this account/region rather than named ARNs.
    if (config.app.pipelines.deadlineCloudExecutionTypeEnabled) {
        const deadlineArnBase = `arn:${Partition()}:deadline:${config.env.region}:${
            config.env.account
        }`;
        runWorkflowPolicy.addStatements(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["deadline:CreateJob"],
                resources: [
                    `${deadlineArnBase}:farm/*/queue/*`,
                    `${deadlineArnBase}:farm/*/queue/*/job/*`,
                ],
            })
        );
    }

    // Exact cross-account targets declared on app.pipelines.externalPipelineBridges[].invokeTargets:
    // one statement per service, each listing the operator's ARNs verbatim. getConfig() has already
    // rejected any wildcard, so nothing here widens the same-account name patterns above.
    const bridgeInvokeTargets = (config.app.pipelines.externalPipelineBridges ?? []).flatMap(
        (bridge) => bridge.invokeTargets ?? []
    );
    const bridgeTargetsFor = (service: string): string[] =>
        bridgeInvokeTargets.filter((arn) => arn.split(":")[2] === service);
    const bridgeTargetGrants: [string, string[]][] = [
        ["lambda:InvokeFunction", bridgeTargetsFor("lambda")],
        ["sqs:SendMessage", bridgeTargetsFor("sqs")],
        ["events:PutEvents", bridgeTargetsFor("events")],
    ];
    for (const [action, resources] of bridgeTargetGrants) {
        if (resources.length > 0) {
            runWorkflowPolicy.addStatements(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: [action],
                    resources,
                })
            );
        }
    }

    //Add KMS key use if provided
    if (kmsKey) {
        runWorkflowPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
    }

    const role = new iam.Role(scope, "VAMSWorkflowIAMRole", {
        assumedBy: Service("STATES").Principal,
        description: "VAMS Workflow IAM Role.",
        inlinePolicies: {
            createWorkflowPolicy: createWorkflowPolicy,
            runWorkflowPolicy: runWorkflowPolicy,
        },
    });

    // Grant access to any external asset bucket customer managed KMS keys so the
    // workflow role can read/write objects in cross-account encrypted buckets
    // (no-op when no external keys are configured)
    grantExternalAssetBucketKmsKeys(role);

    return role;
}

/**
 * Global pipeline + workflow import custom-resource lambda. Registers a built-in (or externally
 * self-registered) pipeline + workflow into the pipeline/workflow tables from a vamsSchema bundle. It
 * upserts via SYSTEM_USER cross-calls to the four service functions, so it needs their names as env +
 * invoke permission on them, plus read on the artefacts bucket where the schema files are uploaded.
 * Also invocable directly (external self-registration) — no API route.
 */
export function buildImportGlobalPipelineWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    pipelineServiceV2Function: lambda.Function,
    pipelineTemplateServiceFunction: lambda.Function,
    workflowServiceV2Function: lambda.Function,
    workflowTriggerServiceFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "importGlobalPipelineWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            PIPELINE_SERVICE_V2_FUNCTION_NAME: pipelineServiceV2Function.functionName,
            PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME: pipelineTemplateServiceFunction.functionName,
            WORKFLOW_SERVICE_V2_FUNCTION_NAME: workflowServiceV2Function.functionName,
            WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME: workflowTriggerServiceFunction.functionName,
        },
    });

    pipelineServiceV2Function.grantInvoke(fun);
    pipelineTemplateServiceFunction.grantInvoke(fun);
    workflowServiceV2Function.grantInvoke(fun);
    workflowTriggerServiceFunction.grantInvoke(fun);
    storageResources.s3.artefactsBucket.grantRead(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Let `caller` invoke the workflow service's internal redeploy actions: the pipeline service schedules
 * a refresh of every workflow referencing a pipeline whose definition-relevant settings changed, and the
 * execute handler redeploys a stale workflow before launching it. The grant is `lambda:InvokeFunction`
 * on the workflow service's function ARN only; the caller gets no Step Functions or iam:PassRole
 * permission, since the workflow service performs the deploy with its own role.
 */
export function grantWorkflowServiceInternalInvoke(
    caller: lambda.Function,
    workflowServiceFunction: lambda.Function
): void {
    caller.addEnvironment(
        "WORKFLOW_SERVICE_V2_FUNCTION_NAME",
        workflowServiceFunction.functionName
    );
    caller.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:InvokeFunction"],
            resources: [workflowServiceFunction.functionArn],
        })
    );
}

/**
 * Workflow V2 service (CRUD + enable/disable/archive + save-validation). Reads/writes the V2
 * workflow + triggers tables (triggers listed on the details view) and reads the V2 pipeline table
 * (referenced-pipeline authorization + save-consistency checks + GLOBAL/scope string checks).
 *
 * Create/update generate the workflow ASL from the referenced pipelines and (re)deploy the Step
 * Functions state machine (common.workflows.workflowAsl.deploy_state_machine), so it receives the
 * dedicated SFN execution role (buildWorkflowRole, built by the caller so its ARN can also be published
 * to SSM) and the execution-overhaul lambda names + log group + partition as env, plus
 * states:Create/Describe/UpdateStateMachine + iam:PassRole. Every deploy publishes a state machine
 * version (and deletes versions past the retained count), which a launch starts on.
 */
export function buildWorkflowServiceV2Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowRole: iam.Role,
    processWorkflowExecutionOutputFunction: lambda.Function,
    interimPipelineTrackingFunction: lambda.Function,
    handleExecutionErrorFunction: lambda.Function,
    workflowsLogGroup: logs.LogGroup,
    stackName: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "workflowService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME:
                interimPipelineTrackingFunction.functionName,
            HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME: handleExecutionErrorFunction.functionName,
            VAMS_STACK_NAME: stackName,
            LAMBDA_ROLE_ARN: workflowRole.roleArn,
            LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            AWS_PARTITION: config.env.partition,
        },
    });
    storageResources.dynamo.workflowStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    // Archiving a workflow deletes its trigger rows (workflowService._delete_workflow_triggers): a
    // DeleteItem per row and nothing else. Trigger rows are written by the trigger service only.
    storageResources.dynamo.workflowTriggersStorageTable.grant(fun, "dynamodb:DeleteItem");
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:CreateStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
                "states:PublishStateMachineVersion",
                "states:ListStateMachineVersions",
                "states:DeleteStateMachineVersion",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:PassRole"],
            resources: [IAMArn("*" + config.name + "*").role],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

/**
 * Workflow trigger service (fileUpload trigger CRUD). Reads the workflow table (parent-object
 * Casbin) and reads/writes the triggers table.
 */
export function buildWorkflowTriggerServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "workflowTriggerService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {},
    });
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineTemplateTagSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineTemplatesStorageTable.grantReadData(fun);
    // A large tag schema is offloaded to the default asset bucket; the headless-template check
    // rehydrates it, so grant read on the asset buckets (best-effort — skipped if unreadable).
    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}
