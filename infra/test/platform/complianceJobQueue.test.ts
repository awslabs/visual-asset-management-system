/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance jobs that continue themselves (a deleted database's move, a database binding change
 * applied in the background, D161) run in the compliance job Lambda from their own queue: encrypted
 * as the trigger's queue is (the deployment key when one is configured, else SQS-managed), TLS-only,
 * a dead-letter queue with a bounded receive count, a visibility timeout of at least six times the
 * job Lambda's timeout, no topic subscription, and an event source of one message per invocation,
 * capped, that reads `batchItemFailures`.
 *
 * Senders, by role grant: the binding service (a change's first message), the database service (a
 * delete's move), the job Lambda (its continuations; a stopped move to the dead-letter queue) and the
 * trigger (a job's message an upgrade left on its queue). The job Lambda sends a binding change's
 * evaluations to the trigger's queue and reaches only the tables the two jobs use.
 *
 * Built from the builders against a stand-in storage stack, so it runs in seconds;
 * test/storage/bucketSyncRecursiveLoop.test.ts asserts the job Lambda's `RecursiveLoop` per partition
 * on full synths, and test/security/complianceLambdaTableGrants.test.ts its table grants against the
 * tables its code reaches.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    buildComplianceJobFunction,
    buildComplianceJobQueue,
    buildComplianceSchemaBindingServiceFunction,
    buildComplianceTriggerFunction,
    buildComplianceTriggerQueue,
    COMPLIANCE_JOB_BATCH_SIZE,
    COMPLIANCE_JOB_MAX_CONCURRENCY,
    COMPLIANCE_JOB_QUEUE_MAX_RECEIVE_COUNT,
    COMPLIANCE_JOB_QUEUE_VISIBILITY_TIMEOUT,
    COMPLIANCE_JOB_TIMEOUT,
} from "../../lib/lambdaBuilder/complianceFunctions";
import { buildDatabaseService } from "../../lib/lambdaBuilder/databaseFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { batchSizeOffenders } from "../support/sqsEventSourceBounds";

const HANDLERS = {
    jobs: "handlers.compliance.complianceJobs.lambda_handler",
    trigger: "handlers.compliance.complianceTrigger.lambda_handler",
    binding: "handlers.compliance.complianceSchemaBindingService.lambda_handler",
    database: "handlers.databases.databaseService.lambda_handler",
};
type Role = keyof typeof HANDLERS;

const SEND_ACTIONS = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:SendMessage"];
const CONSUME_ACTIONS = [
    "sqs:ChangeMessageVisibility",
    "sqs:DeleteMessage",
    "sqs:GetQueueAttributes",
    "sqs:GetQueueUrl",
    "sqs:ReceiveMessage",
];

const mockConfig = (govCloud: boolean): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = govCloud ? "us-gov-west-1" : "us-east-1";
    config.env.partition = govCloud ? "aws-us-gov" : "aws";
    config.env.coreStackName = "vams-test";
    config.app.baseStackName = "vams-test";
    config.app.govCloud.enabled = govCloud;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test/resourceNames";
    return config;
};

/** A storage stand-in whose `dynamo` hands out a table (`T<member>`) for whatever name is read. */
const buildStorageResources = (stack: cdk.Stack, kmsKey?: kms.Key): storageResources => {
    const tables: Record<string, dynamodb.Table> = {};
    const dynamo = new Proxy(tables, {
        get: (target, prop: string) => {
            if (!(prop in target)) {
                target[prop] = new dynamodb.Table(stack, `T${prop}`, {
                    partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
                });
            }
            return target[prop];
        },
    });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    const topics = {
        assetIndexerSnsTopic: new sns.Topic(stack, "AssetIndexerSnsTopic"),
        fileIndexerSnsTopic: new sns.Topic(stack, "FileIndexerSnsTopic"),
    };
    const jobQueues = buildComplianceJobQueue(stack, kmsKey);
    return {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        sns: topics,
        sqs: {
            complianceTriggerQueue: buildComplianceTriggerQueue(stack, topics, kmsKey),
            complianceJobQueue: jobQueues.queue,
            complianceJobDLQ: jobQueues.deadLetterQueue,
        },
        eventBridge: {
            orchestrationBus: new events.EventBus(stack, "OrchestrationBus"),
            eventSourcePrefix: "vams.test",
        },
        cloudWatchAuditLogGroups: {
            authentication: logGroup("AuditAuthentication"),
            authorization: logGroup("AuditAuthorization"),
            fileUpload: logGroup("AuditFileUpload"),
            fileDownload: logGroup("AuditFileDownload"),
            fileDownloadStreamed: logGroup("AuditFileDownloadStreamed"),
            authOther: logGroup("AuditAuthOther"),
            authChanges: logGroup("AuditAuthChanges"),
            actions: logGroup("AuditActions"),
            errors: logGroup("AuditErrors"),
        },
        dynamo,
    } as unknown as storageResources;
};

const synth = (opts: { govCloud?: boolean; cmk?: boolean } = {}): Template => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "123456789012", region: opts.govCloud ? "us-gov-west-1" : "us-east-1" },
    });
    const config = mockConfig(!!opts.govCloud);
    Service.SetConfig(config);
    cdk.Tags.of(stack).add("vams:stackname", stack.stackName);
    const kmsKey = opts.cmk ? new kms.Key(stack, "DeploymentKey") : undefined;
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        `arn:${config.env.partition}:lambda:${config.env.region}:123456789012:layer:vams-common:1`
    ) as lambda.LayerVersion;
    const executeWorkflowV2 = new lambda.Function(stack, "executeWorkflowV2", {
        code: lambda.Code.fromInline("def lambda_handler(e, c): pass"),
        handler: "index.lambda_handler",
        runtime: lambda.Runtime.PYTHON_3_12,
    });
    const storage = buildStorageResources(stack, kmsKey);
    const vpc = undefined as unknown as ec2.IVpc;
    buildComplianceJobFunction(stack, layer, storage, config, vpc, []);
    buildComplianceTriggerFunction(stack, layer, storage, executeWorkflowV2, config, vpc, []);
    buildComplianceSchemaBindingServiceFunction(stack, layer, storage, config, vpc, []);
    buildDatabaseService(stack, layer, storage, config, vpc, [], kmsKey);
    return Template.fromStack(stack);
};

const resources = (template: Template, type: string) =>
    Object.entries(template.findResources(type)).map(([id, r]) => ({ id, ...(r as any) }));

const getAtt = (value: any): string | undefined =>
    value && Array.isArray(value["Fn::GetAtt"]) ? String(value["Fn::GetAtt"][0]) : undefined;

const queueId = (template: Template, prefix: string): string => {
    const ids = resources(template, "AWS::SQS::Queue")
        .map((q) => q.id)
        .filter((id) => id.startsWith(prefix));
    expect(ids).toHaveLength(1);
    return ids[0];
};

const functionOf = (template: Template, role: Role) => {
    const found = resources(template, "AWS::Lambda::Function").filter(
        (f) => f.Properties.Handler === HANDLERS[role]
    );
    expect(found).toHaveLength(1);
    return found[0];
};

/** Every IAM statement on the function's role (its policies and its attached managed policies). */
const statementsOf = (template: Template, role: Role): any[] => {
    const roleId = functionOf(template, role).Properties.Role["Fn::GetAtt"][0];
    const [roleResource] = resources(template, "AWS::IAM::Role").filter((r) => r.id === roleId);
    const attached = new Set(
        ((roleResource.Properties.ManagedPolicyArns ?? []) as any[]).map((arn) => arn?.Ref)
    );
    return [
        ...resources(template, "AWS::IAM::Policy"),
        ...resources(template, "AWS::IAM::ManagedPolicy"),
    ]
        .filter(
            (p) => JSON.stringify(p.Properties.Roles ?? []).includes(roleId) || attached.has(p.id)
        )
        .flatMap((p) => p.Properties.PolicyDocument.Statement);
};

/** The role's SQS statements as `[queue id, sorted actions]`, sorted. */
const sqsGrants = (template: Template, role: Role): [string, string[]][] =>
    statementsOf(template, role)
        .filter((st) => [st.Action].flat().some((a: string) => a.startsWith("sqs:")))
        .map((st) => [
            getAtt(st.Resource) ?? JSON.stringify(st.Resource),
            [st.Action].flat().sort(),
        ])
        .sort(([a, x]: any, [b, y]: any) => `${a}${x.join()}`.localeCompare(`${b}${y.join()}`)) as [
        string,
        string[]
    ][];

describe("compliance job queue and job Lambda", () => {
    const commercial = synth();
    const job = () => queueId(commercial, "ComplianceJobQueue");
    const dlq = () => queueId(commercial, "ComplianceJobDLQ");
    const trigger = () => queueId(commercial, "ComplianceTriggerQueue");

    test("the job queue dead-letters after a bounded receive count", () => {
        const [queue] = resources(commercial, "AWS::SQS::Queue").filter((q) => q.id === job());
        const [deadLetter] = resources(commercial, "AWS::SQS::Queue").filter((q) => q.id === dlq());
        expect(getAtt(queue.Properties.RedrivePolicy.deadLetterTargetArn)).toBe(dlq());
        expect(queue.Properties.RedrivePolicy.maxReceiveCount).toBe(
            COMPLIANCE_JOB_QUEUE_MAX_RECEIVE_COUNT
        );
        expect(COMPLIANCE_JOB_QUEUE_MAX_RECEIVE_COUNT).toBe(3);
        expect(deadLetter.Properties.MessageRetentionPeriod).toBe(14 * 24 * 3600);
        // The job queue keeps SQS's default retention (4 days), which the binding apply's status
        // rules rely on (backend complianceBindingApply.JOB_QUEUE_RETENTION_SECONDS).
        expect(queue.Properties.MessageRetentionPeriod).toBeUndefined();
    });

    test("the visibility timeout is at least six times the job Lambda's timeout", () => {
        const fun = functionOf(commercial, "jobs");
        const [queue] = resources(commercial, "AWS::SQS::Queue").filter((q) => q.id === job());
        expect(fun.Properties.Timeout).toBe(COMPLIANCE_JOB_TIMEOUT.toSeconds());
        expect(queue.Properties.VisibilityTimeout).toBe(
            COMPLIANCE_JOB_QUEUE_VISIBILITY_TIMEOUT.toSeconds()
        );
        expect(queue.Properties.VisibilityTimeout).toBeGreaterThanOrEqual(
            6 * fun.Properties.Timeout
        );
    });

    test("both job queues are KMS-encrypted with the deployment key when one is configured", () => {
        const template = synth({ cmk: true });
        const keyId = resources(template, "AWS::KMS::Key")[0].id;
        for (const prefix of ["ComplianceJobQueue", "ComplianceJobDLQ"]) {
            const [q] = resources(template, "AWS::SQS::Queue").filter(
                (r) => r.id === queueId(template, prefix)
            );
            expect(getAtt(q.Properties.KmsMasterKeyId)).toBe(keyId);
            expect(q.Properties.SqsManagedSseEnabled).toBeUndefined();
        }
    });

    test("both job queues use SQS-managed encryption without a deployment key", () => {
        for (const id of [job(), dlq()]) {
            const [q] = resources(commercial, "AWS::SQS::Queue").filter((r) => r.id === id);
            expect(q.Properties.SqsManagedSseEnabled).toBe(true);
            expect(q.Properties.KmsMasterKeyId).toBeUndefined();
        }
    });

    test("both job queues refuse non-TLS access and allow nothing by queue policy", () => {
        const policies = resources(commercial, "AWS::SQS::QueuePolicy");
        for (const id of [job(), dlq()]) {
            const policy = policies.find((p) =>
                JSON.stringify(p.Properties.Queues).includes(`"${id}"`)
            );
            expect(policy).toBeDefined();
            const statements = policy!.Properties.PolicyDocument.Statement;
            expect(
                statements.filter(
                    (st: any) =>
                        st.Effect === "Deny" &&
                        JSON.stringify(st.Condition ?? {}).includes("aws:SecureTransport")
                )
            ).toHaveLength(1);
            // No topic and no principal is allowed by the queue's own policy: the senders are the
            // roles granted SendMessage below.
            expect(statements.filter((st: any) => st.Effect === "Allow")).toEqual([]);
        }
        const subscribed = resources(commercial, "AWS::SNS::Subscription").filter(
            (s) => getAtt(s.Properties.Endpoint) === job()
        );
        expect(subscribed).toEqual([]);
    });

    test.each([
        ["commercial", false],
        ["govCloud", true],
    ])(
        "the %s event source reads one job message at a time, capped, with batchItemFailures",
        (label, govCloud) => {
            const template = govCloud ? synth({ govCloud: true }) : commercial;
            const jobQueue = queueId(template, "ComplianceJobQueue");
            const mappings = resources(template, "AWS::Lambda::EventSourceMapping").filter(
                (m) => getAtt(m.Properties.EventSourceArn) === jobQueue
            );
            expect(mappings).toHaveLength(1);
            const props = mappings[0].Properties;
            expect(props.FunctionName.Ref).toBe(functionOf(template, "jobs").id);
            expect(props.BatchSize).toBe(COMPLIANCE_JOB_BATCH_SIZE);
            expect(COMPLIANCE_JOB_BATCH_SIZE).toBe(1);
            expect(batchSizeOffenders([{ at: label, properties: props }])).toEqual([]);
            expect(props.MaximumBatchingWindowInSeconds).toBeUndefined();
            expect(props.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);
            expect(props.ScalingConfig).toEqual({
                MaximumConcurrency: COMPLIANCE_JOB_MAX_CONCURRENCY,
            });
            // GovCloud and EU Sovereign reject Tags on a mapping; commercial keeps the stack tags.
            if (govCloud) expect(props).not.toHaveProperty("Tags");
            else expect(props).toHaveProperty("Tags");
            // Only the job Lambda reads the job queue.
            const readers = resources(template, "AWS::Lambda::EventSourceMapping")
                .filter((m) => getAtt(m.Properties.EventSourceArn) === jobQueue)
                .map((m) => m.Properties.FunctionName.Ref);
            expect(readers).toEqual([functionOf(template, "jobs").id]);
        }
    );

    test("each function is told the queues it sends to, and no other", () => {
        const env = (role: Role) =>
            functionOf(commercial, role).Properties.Environment?.Variables ?? {};
        const queueEnv = (role: Role) =>
            Object.fromEntries(
                Object.entries(env(role)).filter(([name]) => /^COMPLIANCE_.*_URL$/.test(name))
            );
        expect(queueEnv("jobs")).toEqual({
            COMPLIANCE_JOB_QUEUE_URL: { Ref: job() },
            COMPLIANCE_JOB_DLQ_URL: { Ref: dlq() },
            COMPLIANCE_TRIGGER_QUEUE_URL: { Ref: trigger() },
        });
        expect(queueEnv("trigger")).toEqual({
            COMPLIANCE_JOB_QUEUE_URL: { Ref: job() },
            COMPLIANCE_TRIGGER_QUEUE_URL: { Ref: trigger() },
        });
        expect(queueEnv("binding")).toEqual({
            COMPLIANCE_JOB_QUEUE_URL: { Ref: job() },
            COMPLIANCE_TRIGGER_QUEUE_URL: { Ref: trigger() },
        });
        expect(queueEnv("database")).toEqual({ COMPLIANCE_JOB_QUEUE_URL: { Ref: job() } });
    });

    test("the SQS grants of each role are exactly the queues it reads and sends to", () => {
        const consume = [...CONSUME_ACTIONS].sort();
        const send = [...SEND_ACTIONS].sort();
        const expected: Record<Role, [string, string[]][]> = {
            // Reads the job queue; sends continuations there, a stopped move to the DLQ, and a
            // binding change's evaluations to the trigger's queue (GetQueueAttributes paces them).
            jobs: [
                [job(), consume],
                [job(), send],
                [dlq(), send],
                [trigger(), send],
            ],
            // Reads its own queue; its only send is a leftover job's message, to the job queue.
            trigger: [
                [trigger(), consume],
                [job(), send],
            ],
            // A binding change's first message; an asset override's evaluation.
            binding: [
                [job(), send],
                [trigger(), send],
            ],
            // A delete's move.
            database: [[job(), send]],
        };
        for (const role of Object.keys(expected) as Role[]) {
            const sorted = [...expected[role]].sort(([a, x], [b, y]) =>
                `${a}${x.join()}`.localeCompare(`${b}${y.join()}`)
            );
            expect({ role, grants: sqsGrants(commercial, role) }).toEqual({ role, grants: sorted });
        }
    });

    test("the job Lambda evaluates nothing: no workflow launch, topic publish or bucket read", () => {
        const actions = statementsOf(commercial, "jobs").flatMap((st) => [st.Action].flat());
        expect(actions.filter((a: string) => a.startsWith("lambda:"))).toEqual([]);
        expect(actions.filter((a: string) => a.startsWith("sns:"))).toEqual([]);
        expect(actions.filter((a: string) => a.startsWith("s3:"))).toEqual([]);
        expect(
            functionOf(commercial, "jobs").Properties.Environment?.Variables ?? {}
        ).not.toHaveProperty("EXECUTE_WORKFLOW_FUNCTION_NAME");
    });

    test("the job Lambda's table grants are the two jobs' tables", () => {
        const statements = statementsOf(commercial, "jobs").filter((st) =>
            [st.Action].flat().some((a: string) => a.startsWith("dynamodb:"))
        );
        const byTable: Record<string, Set<string>> = {};
        for (const st of statements) {
            for (const entry of [st.Resource].flat()) {
                const table = getAtt(entry) ?? getAtt(entry?.["Fn::Join"]?.[1]?.[0]);
                if (!table) continue;
                byTable[table] = byTable[table] ?? new Set();
                [st.Action].flat().forEach((a: string) => byTable[table].add(a));
            }
        }
        // Logical ids are the construct id plus an 8-character hash. The authorization tables are
        // the security helper's, on every VAMS Lambda (setupSecurityAndLoggingEnvironmentAndPermissions).
        const securityHelper = [
            "TconstraintsStorageTable",
            "TrolesStorageTable",
            "TuserRolesStorageTable",
        ];
        const tables = Object.keys(byTable)
            .map((id) => id.replace(/[0-9A-F]{8}$/, ""))
            .filter((id) => !securityHelper.includes(id));
        expect(tables.sort()).toEqual([
            "TassetStorageTable",
            "TcomplianceAssetStateStorageTable",
            "TcomplianceAuditStorageTable",
            "TcomplianceCascadeStorageTable",
            "TcomplianceEvaluationStorageTable",
            "TcomplianceSchemaStorageTable",
            "TdatabaseStorageTable",
        ]);
        const writes = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"];
        const written = (prefix: string) =>
            Object.entries(byTable)
                .filter(([id]) => id.startsWith(prefix))
                .flatMap(([, actions]) => [...actions])
                .filter((a) => writes.includes(a))
                .sort();
        // Read only: the database row and the asset rows a binding change pages.
        expect(written("TdatabaseStorageTable")).toEqual([]);
        expect(written("TassetStorageTable")).toEqual([]);
        // The move re-scopes the deleted database's schemas with UpdateItem, nothing else.
        expect(written("TcomplianceSchemaStorageTable")).toEqual(["dynamodb:UpdateItem"]);
    });
});
