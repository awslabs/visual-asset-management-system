/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Least-privilege checks on the synthesized execution-role policies of the workflow/execution
 * lambdas: every AWS action a handler calls is granted, and no table a handler never resolves is.
 *
 * Guards FIX-038 (S1-INFRA-079): an unscoped states:StopExecution / batch:TerminateJob grant lets
 * executionService reach any execution or job in the account, driven by registration data that is
 * validated only for ARN shape.
 */

import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    buildExecuteWorkflowV2Function,
    buildExecutionServiceFunction,
    buildHandleExecutionErrorFunction,
    buildInterimPipelineTrackingFunction,
    buildProcessWorkflowExecutionOutputFunction,
} from "../../lib/lambdaBuilder/workflowFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

/** Commercial-template config with a fixed synth environment. */
const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = false;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** Table names the builders grant on, keyed by the storageResources.dynamo field. */
const DYNAMO_FIELDS = [
    "assetStorageTable",
    "assetUploadsStorageTable",
    "assetFileVersionHistoryStorageTable",
    "authEntitiesStorageTable",
    "constraintsStorageTable",
    "databaseStorageTable",
    "pipelineExecutionsStorageTable",
    "pipelineExecutionInputFilesStorageTable",
    "pipelineExecutionInputMetadataStorageTable",
    "pipelineExecutionInputConfigurationStorageTable",
    "pipelineExecutionOutputFilesStorageTable",
    "pipelineExecutionOutputMetadataStorageTable",
    "pipelineExecutionOutputResultsStorageTable",
    "pipelineExecutionLogsStorageTable",
    "pipelineStorageTableV2",
    "pipelineTemplatesStorageTable",
    "pipelineTemplateTagSchemaStorageTable",
    "rolesStorageTable",
    "s3AssetBucketsStorageTable",
    "userRolesStorageTable",
    "workflowExecutionsStorageTableV2",
    "workflowExecutionInputsStorageTable",
    "workflowExecutionConfigurationStorageTable",
    "workflowStorageTableV2",
    "workflowTriggersStorageTable",
] as const;

interface SynthedLambda {
    template: Template;
    resources: storageResources;
    logicalIdOf: (field: (typeof DYNAMO_FIELDS)[number]) => string;
}

/** Builds a single workflow lambda in an isolated stack and returns its synthesized template. */
const synthWorkflowLambda = (
    build: (
        scope: cdk.Stack,
        layer: lambda.LayerVersion,
        resources: storageResources,
        extra: {
            metadataServiceFunction: lambda.Function;
            fileUploadFunction: lambda.Function;
            workflowsLogGroup: logs.LogGroup;
        },
        config: Config.Config
    ) => lambda.Function
): SynthedLambda => {
    const config = createMockConfig();
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "GrantStack", {
        env: { account: config.env.account, region: config.env.region },
    });

    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const dynamo: Record<string, dynamodb.Table> = {};
    for (const field of DYNAMO_FIELDS) {
        dynamo[field] = table(field);
    }
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    const resources = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: {
            orchestrationBus: new events.EventBus(stack, "OrchestrationBus"),
            eventSourcePrefix: "vams.test",
        },
        cloudWatchAuditLogGroups: {
            authentication: logGroup("AuthenticationAuditLogGroup"),
            authorization: logGroup("AuthorizationAuditLogGroup"),
            fileUpload: logGroup("FileUploadAuditLogGroup"),
            fileDownload: logGroup("FileDownloadAuditLogGroup"),
            fileDownloadStreamed: logGroup("FileDownloadStreamedAuditLogGroup"),
            authOther: logGroup("AuthOtherAuditLogGroup"),
            authChanges: logGroup("AuthChangesAuditLogGroup"),
            actions: logGroup("ActionsAuditLogGroup"),
            errors: logGroup("ErrorsAuditLogGroup"),
        },
        dynamo,
    } as unknown as storageResources;

    // Layers reject inline code, so the layer asset points at an existing directory.
    const layer = new lambda.LayerVersion(stack, "CommonLayer", {
        code: lambda.Code.fromAsset(path.join(__dirname, "..", "../common")),
    });
    const stubFunction = (id: string) =>
        new lambda.Function(stack, id, {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: lambda.Code.fromInline("def handler(e, c): pass"),
        });

    build(
        stack,
        layer,
        resources,
        {
            metadataServiceFunction: stubFunction("MetadataService"),
            fileUploadFunction: stubFunction("FileUpload"),
            workflowsLogGroup: logGroup("WorkflowsLogGroup"),
        },
        config
    );

    const template = Template.fromStack(stack);
    return {
        template,
        resources,
        // The raw logicalId is an unresolved token until the stack resolves it.
        logicalIdOf: (field) =>
            stack.getLogicalId(resources.dynamo[field].node.defaultChild as dynamodb.CfnTable),
    };
};

/** Statements attached to the role of the lambda construct named `lambdaId`. */
const statementsOnRole = (template: Template, lambdaId: string): any[] => {
    // The role logical id is '{lambdaId}ServiceRole{hash}'; a lambda's grants may be spread over
    // its default policy and any overflow policies, all of which reference that one role.
    const roleId = Object.keys(template.findResources("AWS::IAM::Role")).find((id) =>
        id.startsWith(`${lambdaId}ServiceRole`)
    );
    if (!roleId) {
        throw new Error(`No role found for lambda ${lambdaId}`);
    }
    const statements: any[] = [];
    // A role's grants spill from its default AWS::IAM::Policy into AWS::IAM::ManagedPolicy overflow
    // policies once the inline document nears the IAM size limit. Both types must be read: these
    // lambdas carry enough grants that the later ones (S3, KMS, SSM, states, logs) land only in the
    // overflow, so scanning AWS::IAM::Policy alone reports a correctly granted action as missing.
    for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
        for (const policy of Object.values(template.findResources(type)) as any[]) {
            if (!JSON.stringify(policy.Properties.Roles ?? []).includes(roleId)) {
                continue;
            }
            statements.push(...policy.Properties.PolicyDocument.Statement);
        }
    }
    return statements;
};

/** Every action granted on the role of the lambda construct named `lambdaId`. */
const actionsOnRole = (template: Template, lambdaId: string): string[] =>
    statementsOnRole(template, lambdaId).flatMap((s) => ([] as string[]).concat(s.Action ?? []));

/** Resource references of every statement on `lambdaId`'s role carrying `action`. */
const resourcesForAction = (template: Template, lambdaId: string, action: string): string =>
    statementsOnRole(template, lambdaId)
        .filter((s) => ([] as string[]).concat(s.Action ?? []).includes(action))
        .map((s) => JSON.stringify(s.Resource))
        .join(" ");

/**
 * Every resource ENTRY granted for `action`, flattened across the statements carrying it.
 *
 * The joined string from `resourcesForAction` answers "is this pattern in scope" but cannot answer
 * "is a bare `*` also in scope" — `"*"` is a substring of every wildcard pattern, so a `toContain`
 * on the string is true either way. Entries that are unresolved references (`{"Fn::GetAtt": ...}`)
 * are kept as their JSON so a caller can still match on them.
 */
const resourceEntries = (template: Template, lambdaId: string, action: string): string[] =>
    statementsOnRole(template, lambdaId)
        .filter((s) => ([] as string[]).concat(s.Action ?? []).includes(action))
        .flatMap((s) => ([] as any[]).concat(s.Resource ?? []))
        .map((r) => (typeof r === "string" ? r : JSON.stringify(r)));

/** The environment map of the lambda whose Handler names `handlerSuffix`. */
const environmentOf = (template: Template, handlerSuffix: string): Record<string, unknown> => {
    for (const fn of Object.values(template.findResources("AWS::Lambda::Function")) as any[]) {
        if (String(fn.Properties.Handler ?? "").endsWith(handlerSuffix)) {
            return fn.Properties.Environment?.Variables ?? {};
        }
    }
    throw new Error(`No lambda found for handler ${handlerSuffix}`);
};

describe("executeWorkflow state machine grants", () => {
    const synthed = () =>
        synthWorkflowLambda((scope, layer, resources, extra, config) =>
            buildExecuteWorkflowV2Function(
                scope,
                layer,
                resources,
                extra.metadataServiceFunction,
                extra.workflowsLogGroup,
                config,
                undefined as any,
                []
            )
        );

    // The handler stops the state machine it just started when the post-start record writes fail
    // (_stop_started_execution). Without the grant the compensation can only log, leaving a running
    // execution with no VAMS records - absent from the executions list and unreachable by abort.
    test("grants states:StopExecution alongside StartExecution", () => {
        const actions = actionsOnRole(synthed().template, "executeWorkflow");
        expect(actions).toContain("states:StartExecution");
        expect(actions).toContain("states:StopExecution");
    });

    // StopExecution acts on an execution ARN, so the config-name and backend-generated execution
    // patterns must both be in scope. The builder scopes on config.name, which the mock leaves at
    // the template default, not on the overridden baseStackName.
    test("scopes StopExecution to the execution ARN patterns", () => {
        const refs = resourcesForAction(
            synthed().template,
            "executeWorkflow",
            "states:StopExecution"
        );
        expect(refs).toContain("execution:*vams*");
        expect(refs).toContain("execution:vams-*");
    });
});

describe("interimPipelineTracking least privilege", () => {
    const synthed = () =>
        synthWorkflowLambda((scope, layer, resources, extra, config) =>
            buildInterimPipelineTrackingFunction(
                scope,
                layer,
                resources,
                extra.workflowsLogGroup,
                config,
                undefined as any,
                []
            )
        );

    // The handler advances only the per-pipeline rows; the main execution row is written by the
    // launch, status and end-state handlers.
    test("holds no write action on the workflow executions table", () => {
        const { template, logicalIdOf } = synthed();
        const writeRefs = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
            .map((action) => resourcesForAction(template, "interimPipelineTracking", action))
            .join(" ");
        expect(writeRefs).not.toContain(logicalIdOf("workflowExecutionsStorageTableV2"));
        expect(writeRefs).toContain(logicalIdOf("pipelineExecutionsStorageTable"));
    });

    // Read access is still required: the handler resolves the table for the run's context.
    test("keeps read access on the workflow executions table", () => {
        const { template, logicalIdOf } = synthed();
        const readRefs = resourcesForAction(
            template,
            "interimPipelineTracking",
            "dynamodb:GetItem"
        );
        expect(readRefs).toContain(logicalIdOf("workflowExecutionsStorageTableV2"));
    });

    // The next step's configuration row supplies the declared configFormat its config body is
    // rendered under. It is written at launch, so this lambda reads it and never writes it.
    test("reads the pipeline execution input configuration table", () => {
        const { template, logicalIdOf } = synthed();
        const readRefs = resourcesForAction(
            template,
            "interimPipelineTracking",
            "dynamodb:GetItem"
        );
        expect(readRefs).toContain(logicalIdOf("pipelineExecutionInputConfigurationStorageTable"));
    });

    test("holds no write action on the pipeline execution input configuration table", () => {
        const { template, logicalIdOf } = synthed();
        const writeRefs = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
            .map((action) => resourcesForAction(template, "interimPipelineTracking", action))
            .join(" ");
        expect(writeRefs).not.toContain(
            logicalIdOf("pipelineExecutionInputConfigurationStorageTable")
        );
        // Positive control: the write scan does find the tables this lambda does write.
        expect(writeRefs).toContain(logicalIdOf("pipelineExecutionsStorageTable"));
    });

    // The handler reads no log group ARN from its environment.
    test("sets no workflow log group ARN in the environment", () => {
        const env = environmentOf(synthed().template, "interimPipelineTracking.lambda_handler");
        expect(Object.keys(env)).not.toContain("WORKFLOW_EXECUTION_LOG_GROUP_ARN");
    });
});

describe("executionService environment", () => {
    // The log group each execution ran against is read from that execution's own record
    // (executionLogGroupArn), not from the lambda environment.
    test("sets no workflow log group ARN in the environment", () => {
        const synthed = synthWorkflowLambda((scope, layer, resources, extra, config) =>
            buildExecutionServiceFunction(
                scope,
                layer,
                resources,
                extra.workflowsLogGroup,
                config,
                undefined as any,
                []
            )
        );
        const env = environmentOf(synthed.template, "executionService.lambda_handler");
        expect(Object.keys(env)).not.toContain("WORKFLOW_EXECUTION_LOG_GROUP_ARN");
        // The log-group read scope is still granted on the role.
        expect(actionsOnRole(synthed.template, "executionService")).toContain(
            "logs:FilterLogEvents"
        );
    });
});

/**
 * The error handler CALLS stop_execution and terminate_job on every caught workflow failure — it
 * builds both clients and passes them into `mark_inflight_pipelines_terminal`. Its role held neither
 * action, so both stops were attempted and denied, and the handler recorded the AccessDenied as
 * "may still be running" text on the pipeline log row. The row is stamped terminal in the same pass,
 * after which it is no longer a candidate for the abort API, so the sub-execution or Batch job it
 * left running had no in-product remedy.
 *
 * Nothing surfaces that from the outside: a denied stop and an unstoppable resource type produce the
 * same warning string, so a test reading the handler's own output cannot tell them apart. The grant
 * is what has to be asserted.
 *
 * Neither action is gated on the DeadlineCloud flag — every deployment's failure path needs them.
 */
describe("handleExecutionError sub-process stop grants", () => {
    const synthed = () =>
        synthWorkflowLambda((scope, layer, resources, extra, config) =>
            buildHandleExecutionErrorFunction(
                scope,
                layer,
                resources,
                extra.workflowsLogGroup,
                config,
                undefined as any,
                []
            )
        );

    test("grants states:StopExecution and batch:TerminateJob", () => {
        const actions = actionsOnRole(synthed().template, "handleExecutionError");
        expect(actions).toContain("states:StopExecution");
        expect(actions).toContain("batch:TerminateJob");
    });

    // FIX-038's rule applies here too: StopExecution acts on an execution ARN, and this file exists
    // because an unscoped grant reaches every execution in the account driven by registration data
    // that is validated only for ARN shape. The stack's own cdk-nag suppressions blanket-waive
    // Resource wildcards, so an accidental `Resource: "*"` would ship with a green nag run and a
    // green presence test above.
    test("scopes StopExecution to the execution ARN patterns rather than every execution", () => {
        const refs = resourcesForAction(
            synthed().template,
            "handleExecutionError",
            "states:StopExecution"
        );
        expect(refs).toContain("execution:*vams*");
        expect(refs).toContain("execution:vams-*");
        // A bare "*" among the StopExecution resources defeats the patterns above while leaving
        // them present, so the patterns alone are not the assertion.
        expect(
            resourceEntries(synthed().template, "handleExecutionError", "states:StopExecution")
        ).not.toContain("*");
    });

    // Batch generates job ids with no deployment-specific prefix, so this one genuinely cannot be
    // scoped by name. Pinned rather than left implicit: it is the one wildcard in this family, and a
    // reviewer comparing it with StopExecution above should find the asymmetry asserted on purpose.
    test("keeps batch:TerminateJob on the account-wide resource Batch ids force", () => {
        expect(
            resourceEntries(synthed().template, "handleExecutionError", "batch:TerminateJob")
        ).toEqual(["*"]);
    });
});

describe("processWorkflowExecutionOutput least privilege", () => {
    // The handler resolves ten tables, none of them the database table: it reads the default
    // asset bucket from the buckets table instead.
    test("holds no grant on the database storage table", () => {
        const synthed = synthWorkflowLambda((scope, layer, resources, extra, config) =>
            buildProcessWorkflowExecutionOutputFunction(
                scope,
                layer,
                resources,
                extra.fileUploadFunction,
                extra.metadataServiceFunction,
                extra.workflowsLogGroup,
                config,
                undefined as any,
                []
            )
        );
        const dynamoRefs = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
            .map((action) =>
                resourcesForAction(synthed.template, "processWorkflowExecutionOutput", action)
            )
            .join(" ");
        expect(dynamoRefs).not.toContain(synthed.logicalIdOf("databaseStorageTable"));
        expect(dynamoRefs).toContain(synthed.logicalIdOf("s3AssetBucketsStorageTable"));
    });
});
