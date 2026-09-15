/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The three vector-search Lambda builders: handler modules, environment, least-privilege grants, and the
 * search-stack VPC placement predicate.
 *
 * Statements are collected from BOTH `AWS::IAM::Policy` and `AWS::IAM::ManagedPolicy`: CDK spills into a
 * managed policy once an inline policy nears the size limit, and scanning only the inline kind reports a
 * correctly-granted permission as missing.
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
import { searchLambdasInVpc } from "../../lib/helper/searchPlacement";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    SYSTEM_GENAI_METADATA_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../common/systemPipelines";
import {
    buildSystemWorkflowLauncherFunction,
    buildVectorIndexerFunction,
    buildVectorReindexerFunction,
} from "../../lib/lambdaBuilder/vectorSearchFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";

/** Commercial-template config with vector search on and the internal derived fields set. */
const mockConfig = (mutate?: (c: any) => void): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.vectorSearch = {
        enabled: true,
        embeddingModelId: "amazon.titan-embed-text-v2:0",
        embeddingDimensions: 1024,
        indexingConcurrency: 5,
    };
    (config as any).vectorIndexName = "vec-amazon-titan-embed-text-v2-0-1024";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    mutate?.(config);
    return config;
};

const TABLE_FIELDS = [
    "vectorEmbeddingsStorageTable",
    "assetStorageTable",
    "databaseStorageTable",
    "s3AssetBucketsStorageTable",
    "workflowStorageTableV2",
    "pipelineStorageTableV2",
    "workflowTriggersStorageTable",
    "authEntitiesStorageTable",
    "constraintsStorageTable",
    "userRolesStorageTable",
    "rolesStorageTable",
] as const;

interface Harness {
    stack: cdk.Stack;
    template: Template;
    resources: storageResources;
    layer: lambda.LayerVersion;
    vpc: ec2.Vpc;
}

const buildStack = (config: Config.Config): Omit<Harness, "template"> => {
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "VectorFnStack", {
        env: { account: ACCOUNT, region: config.env.region },
    });
    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const dynamo: Record<string, dynamodb.Table> = {};
    for (const field of TABLE_FIELDS) dynamo[field] = table(field);
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    const resources = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        sns: {
            eventEmailSubscriptionTopic: new sns.Topic(stack, "EmailTopic"),
            fileIndexerSnsTopic: new sns.Topic(stack, "FileIndexerTopic"),
            assetIndexerSnsTopic: new sns.Topic(stack, "AssetIndexerTopic"),
            databaseIndexerSnsTopic: new sns.Topic(stack, "DatabaseIndexerTopic"),
        },
        eventBridge: {
            orchestrationBus: new events.EventBus(stack, "OrchestrationBus"),
            orchestrationBusAuditLogGroup: logGroup("BusAudit"),
            eventSourcePrefix: "vams.vams-test",
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
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        `arn:${config.env.partition}:lambda:${config.env.region}:${ACCOUNT}:layer:vams-common:1`
    ) as lambda.LayerVersion;
    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2, natGateways: 0 });
    return { stack, resources, layer, vpc };
};

const synthAll = (config: Config.Config): Harness => {
    const h = buildStack(config);
    buildVectorIndexerFunction(
        h.stack,
        h.resources,
        config,
        h.layer,
        h.vpc,
        h.vpc.isolatedSubnets,
        {
            VECTOR_INDEXER_QUEUE_URL: "https://sqs.example/indexer",
        }
    );
    buildVectorReindexerFunction(
        h.stack,
        h.resources,
        config,
        h.layer,
        h.vpc,
        h.vpc.isolatedSubnets,
        {
            WORKFLOW_LAUNCH_QUEUE_URL: "https://sqs.example/launch",
            GENAI_METADATA_WORKFLOW_ID: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
            GENAI_METADATA_WORKFLOW_DATABASE_ID: SYSTEM_WORKFLOW_DATABASE_ID,
        }
    );
    buildSystemWorkflowLauncherFunction(
        h.stack,
        h.resources,
        config,
        h.layer,
        h.vpc,
        h.vpc.isolatedSubnets,
        {
            EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME: "executeWorkflowV2-test",
            GENAI_METADATA_WORKFLOW_ID: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
            GENAI_METADATA_WORKFLOW_DATABASE_ID: SYSTEM_WORKFLOW_DATABASE_ID,
        }
    );
    return { ...h, template: Template.fromStack(h.stack) };
};

/** The Lambda resource whose handler is `handlers.vectorsearch.<module>.lambda_handler`. */
const functionFor = (template: Template, module: string): [string, any] => {
    const entries = Object.entries(template.findResources("AWS::Lambda::Function")).filter(
        ([, r]: [string, any]) =>
            r.Properties.Handler === `handlers.vectorsearch.${module}.lambda_handler`
    );
    expect(entries).toHaveLength(1);
    return entries[0] as [string, any];
};

/** Every statement of every policy (inline AND managed) attached to a function's role. */
const statementsFor = (template: Template, fn: any): any[] => {
    const roleId: string = fn.Properties.Role["Fn::GetAtt"][0];
    const attached = (policy: any) =>
        JSON.stringify(policy.Properties.Roles ?? []).includes(`"Ref":"${roleId}"`);
    const policies = [
        ...Object.values(template.findResources("AWS::IAM::Policy")),
        ...Object.values(template.findResources("AWS::IAM::ManagedPolicy")),
    ].filter(attached);
    return policies.flatMap((p: any) => {
        const st = p.Properties.PolicyDocument.Statement;
        return Array.isArray(st) ? st : [st];
    });
};

const flat = (value: any): string => JSON.stringify(value);

const grantsOn = (statements: any[], tableId: string) =>
    statements
        .filter((s) => flat(s.Resource).includes(tableId))
        .flatMap((s) => ([] as string[]).concat(s.Action));

describe("vector-search Lambda builders", () => {
    const config = mockConfig();
    const h = synthAll(config);

    test("each builder names a handler module under handlers/vectorsearch", () => {
        for (const module of ["vectorIndexer", "vectorReindexer", "systemWorkflowLauncher"]) {
            const [, fn] = functionFor(h.template, module);
            expect(fn.Properties.Runtime).toBe("python3.12");
            expect(fn.Properties.Timeout).toBe(900);
            expect(fn.Properties.MemorySize).toBe(Config.LAMBDA_MEMORY_SIZE);
        }
    });

    test("the indexer and reindexer carry the vector index environment; the launcher its target", () => {
        const [, indexer] = functionFor(h.template, "vectorIndexer");
        const env = indexer.Properties.Environment.Variables;
        expect(env.VECTOR_INDEX_NAME).toBe("vec-amazon-titan-embed-text-v2-0-1024");
        expect(env.EMBEDDING_MODEL_ID).toBe("amazon.titan-embed-text-v2:0");
        expect(env.EMBEDDING_DIMENSIONS).toBe("1024");
        // The auxiliary bucket is resolved from the SSM resource names at runtime; a second copy
        // in the environment would let the two disagree.
        expect(env.AUX_BUCKET_NAME).toBeUndefined();
        expect(env.VECTOR_INDEXER_QUEUE_URL).toBe("https://sqs.example/indexer");
        expect(env.VAMS_RESOURCE_PARAM_PREFIX).toBe("/vams-test-us-east-1/resourceNames");

        const [, reindexer] = functionFor(h.template, "vectorReindexer");
        const renv = reindexer.Properties.Environment.Variables;
        expect(renv.VECTOR_INDEX_NAME).toBe("vec-amazon-titan-embed-text-v2-0-1024");
        expect(renv.WORKFLOW_LAUNCH_QUEUE_URL).toBe("https://sqs.example/launch");
        expect(renv.GENAI_METADATA_WORKFLOW_ID).toBe("system-genai-metadata");
        expect(renv.GENAI_METADATA_WORKFLOW_DATABASE_ID).toBe("GLOBAL");

        const [, launcher] = functionFor(h.template, "systemWorkflowLauncher");
        const lenv = launcher.Properties.Environment.Variables;
        expect(lenv.EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME).toBe("executeWorkflowV2-test");
        expect(lenv.GENAI_METADATA_WORKFLOW_ID).toBe("system-genai-metadata");
        expect(lenv).not.toHaveProperty("VECTOR_INDEX_NAME");
    });

    test("the indexer reads and writes the vector table and only reads the asset tables", () => {
        const [, fn] = functionFor(h.template, "vectorIndexer");
        const statements = statementsFor(h.template, fn);
        const vectorTable = h.stack.getLogicalId(
            h.resources.dynamo.vectorEmbeddingsStorageTable.node.defaultChild as cdk.CfnElement
        );
        const assetTable = h.stack.getLogicalId(
            h.resources.dynamo.assetStorageTable.node.defaultChild as cdk.CfnElement
        );
        expect(grantsOn(statements, vectorTable)).toEqual(
            expect.arrayContaining([
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
            ])
        );
        expect(grantsOn(statements, assetTable)).toEqual(
            expect.arrayContaining(["dynamodb:GetItem", "dynamodb:Query"])
        );
        expect(grantsOn(statements, assetTable)).not.toContain("dynamodb:PutItem");
        // The embedding document is read then deleted from the auxiliary bucket.
        const aux = h.stack.getLogicalId(
            h.resources.s3.assetAuxiliaryBucket.node.defaultChild as cdk.CfnElement
        );
        expect(grantsOn(statements, aux)).toEqual(
            expect.arrayContaining(["s3:GetObject*", "s3:DeleteObject*"])
        );
        // The send grant on its own queue is the construct's, which owns the queue; the builder grants none.
        expect(statements.some((s) => flat(s.Action).includes("sqs:SendMessage"))).toBe(false);
    });

    test("the reindexer may send to no queue by default and grants itself InvokeFunction outside its default policy", () => {
        const [fnId, fn] = functionFor(h.template, "vectorReindexer");
        const statements = statementsFor(h.template, fn);
        const selfInvoke = statements.filter(
            (s) =>
                flat(s.Action).includes("lambda:InvokeFunction") && flat(s.Resource).includes(fnId)
        );
        expect(selfInvoke).toHaveLength(1);
        // The function DependsOn its role's default policy; the self-invoke statement lives in a separate
        // policy so the reference to the function's own ARN is not a cycle.
        const dependsOn: string[] = fn.DependsOn ?? [];
        const selfInvokePolicies = Object.entries(
            h.template.findResources("AWS::IAM::Policy")
        ).filter(([, p]: [string, any]) => flat(p.Properties.PolicyDocument).includes(`"${fnId}"`));
        expect(selfInvokePolicies).toHaveLength(1);
        expect(dependsOn).not.toContain(selfInvokePolicies[0][0]);
        expect(statements.some((s) => flat(s.Action).includes("sqs:SendMessage"))).toBe(false);
    });

    test("the launcher reads the workflow triggers table and nothing else of the data model", () => {
        const [, fn] = functionFor(h.template, "systemWorkflowLauncher");
        const statements = statementsFor(h.template, fn);
        const triggers = h.stack.getLogicalId(
            h.resources.dynamo.workflowTriggersStorageTable.node.defaultChild as cdk.CfnElement
        );
        const vectorTable = h.stack.getLogicalId(
            h.resources.dynamo.vectorEmbeddingsStorageTable.node.defaultChild as cdk.CfnElement
        );
        expect(grantsOn(statements, triggers)).toEqual(expect.arrayContaining(["dynamodb:Query"]));
        expect(grantsOn(statements, vectorTable)).toEqual([]);
    });
});

describe("VPC placement follows searchLambdasInVpc(config)", () => {
    test("the commercial template (public serverless OpenSearch) keeps the Lambdas outside the VPC", () => {
        const config = mockConfig();
        expect(searchLambdasInVpc(config)).toBe(false);
        const h = synthAll(config);
        for (const module of ["vectorIndexer", "vectorReindexer", "systemWorkflowLauncher"]) {
            expect(functionFor(h.template, module)[1].Properties).not.toHaveProperty("VpcConfig");
        }
    });

    test("provisioned OpenSearch places all three in the VPC", () => {
        const config = mockConfig((c) => {
            c.app.openSearch.useServerless.enabled = false;
            c.app.openSearch.useProvisioned.enabled = true;
            c.app.useGlobalVpc.enabled = true;
        });
        expect(searchLambdasInVpc(config)).toBe(true);
        const h = synthAll(config);
        for (const module of ["vectorIndexer", "vectorReindexer", "systemWorkflowLauncher"]) {
            expect(functionFor(h.template, module)[1].Properties.VpcConfig.SubnetIds).toHaveLength(
                2
            );
        }
    });
});
