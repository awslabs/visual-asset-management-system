/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { SYSTEM_WORKFLOW_DATABASE_ID } from "../../common/systemPipelines";
import * as Config from "../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import { searchLambdasInVpc } from "../helper/searchPlacement";
import { Service } from "../helper/service-helper";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../helper/security";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { grantBedrockInvokeModel } from "../nestedStacks/pipelines/system/genAiMetadata/lambdaBuilder/systemGenAiMetadataFunctions";

/** Environment shared by every Lambda that addresses the vector index. */
export function vectorIndexEnvironment(config: Config.Config): Record<string, string> {
    return {
        VECTOR_INDEX_NAME: config.vectorIndexName,
        EMBEDDING_MODEL_ID:
            config.app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingModelId,
        EMBEDDING_DIMENSIONS: String(
            config.app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingDimensions
        ),
    };
}

/** Search-stack VPC placement: in the VPC exactly when `searchLambdasInVpc(config)` holds and a VPC exists. */
export function vectorLambdaPlacement(
    config: Config.Config,
    vpc?: ec2.IVpc,
    subnets?: ec2.ISubnet[]
): Pick<lambda.FunctionProps, "vpc" | "vpcSubnets"> {
    const inVpc = searchLambdasInVpc(config) && vpc !== undefined;
    return {
        vpc: inVpc ? vpc : undefined,
        vpcSubnets: inVpc && subnets ? { subnets } : undefined,
    };
}

/** The four security calls every vector-search Lambda makes (the fifth, grant suppression, is per builder). */
export function applyVectorLambdaSecurity(
    fun: lambda.Function,
    storageResources: storageResources,
    config: Config.Config
): void {
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
}

const backendCode = () => lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`));

/**
 * Recursive-loop setting for the two vector functions that continue their own work.
 *
 * The vector reindexer continues a run by invoking itself asynchronously every time fewer than 90 s of its
 * 15-minute window remain; the vector indexer continues an asset- or file-wide rule by sending a
 * `vector.indexer.continue` message to the queue it consumes. AWS Lambda's recursive-loop detection counts
 * both chains (Lambda to Lambda, and Lambda to SQS to the same Lambda) and stops them after about 16 hops,
 * sending the event to the on-failure destination or the queue's DLQ, so a long reindex run (a large
 * enumeration or clear, the v2.6-to-v2.7 vector backfill) or a rule over a large item collection would
 * stop part-way. ALLOW turns the detection off for these two functions only; each handler bounds its chain
 * itself (`MAX_RUN_INVOCATIONS` in `vectorReindexer.py`, `MAX_CONTINUATION_HOPS` in `vectorIndexer.py`)
 * and sends what resumes the work to its DLQ when the chain reaches the cap.
 *
 * Set in the partitions that have the detection, where it is on by default: commercial (`aws`) and the AWS
 * European Sovereign Cloud (`aws-eusc`), as for the bucket-sync function in `indexingFunctions.ts`. The AWS
 * GovCloud (US) user guide lists it as not available, so `aws-us-gov` and any other partition omit the
 * property and the function template stays unchanged there.
 */
export function vectorSelfContinuationRecursiveLoop(
    config: Config.Config
): lambda.RecursiveLoop | undefined {
    const partition = config.env.partition;
    return partition === "aws" || partition === "aws-eusc" ? lambda.RecursiveLoop.ALLOW : undefined;
}

/**
 * The single writer of the vector embeddings table. Consumes the vector indexer queue (embedding-ready
 * events, bucket-sync S3 records, asset stream records, its own continuation messages); the queue wiring
 * lives in the construct, which passes the queue URL as `VECTOR_INDEXER_QUEUE_URL` and the queue's
 * dead-letter queue (where a continuation past the hop cap is parked) as `VECTOR_INDEXER_DLQ_URL` through
 * `extraEnv`.
 */
export function buildVectorIndexerFunction(
    scope: Construct,
    storageResources: storageResources,
    config: Config.Config,
    lambdaCommonBaseLayer: LayerVersion,
    vpc?: ec2.IVpc,
    subnets?: ec2.ISubnet[],
    extraEnv?: Record<string, string>
): lambda.Function {
    const name = "vectorIndexer";
    const fun = new lambda.Function(scope, name, {
        code: backendCode(),
        handler: `handlers.osVectorSearch.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        recursiveLoop: vectorSelfContinuationRecursiveLoop(config),
        ...vectorLambdaPlacement(config, vpc, subnets),
        environment: {
            ...vectorIndexEnvironment(config),
            ...(extraEnv ?? {}),
        },
    });
    storageResources.dynamo.vectorEmbeddingsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // head_object / list_object_versions on the file whose embedding arrived, in any registered bucket.
    grantReadPermissionsToAllAssetBuckets(fun);
    // Embedding documents are read from and deleted in the auxiliary bucket.
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    applyVectorLambdaSecurity(fun, storageResources, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

/**
 * Operator-invoked clear / enqueue / both. Continuation is an asynchronous self-invoke; `extraEnv`
 * carries the launch queue URL and the system workflow identity. Built whenever the system GenAI
 * metadata pipeline is on and registered; `VECTOR_SEARCH_ENABLED` tells it whether vector search is on.
 * With it off the function carries no vector index environment and no grant on the vector table, and
 * refuses `clear` and `both`: `enqueue` alone launches the system workflow over the existing files.
 */
export function buildVectorReindexerFunction(
    scope: Construct,
    storageResources: storageResources,
    config: Config.Config,
    lambdaCommonBaseLayer: LayerVersion,
    vpc?: ec2.IVpc,
    subnets?: ec2.ISubnet[],
    extraEnv?: Record<string, string>
): lambda.Function {
    const name = "vectorReindexer";
    const fun = new lambda.Function(scope, name, {
        code: backendCode(),
        handler: `handlers.osVectorSearch.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        recursiveLoop: vectorSelfContinuationRecursiveLoop(config),
        ...vectorLambdaPlacement(config, vpc, subnets),
        environment: {
            ...(config.app.vectorSearch.enabled ? vectorIndexEnvironment(config) : {}),
            VECTOR_SEARCH_ENABLED: config.app.vectorSearch.enabled ? "true" : "false",
            ...(extraEnv ?? {}),
        },
    });
    // The table is read and written only by `clear` and `both`, which need vector search.
    if (config.app.vectorSearch.enabled) {
        storageResources.dynamo.vectorEmbeddingsStorageTable.grantReadWriteData(fun);
    }
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    // list_objects_v2 over every registered bucket prefix when enumerating the latest live files.
    grantReadPermissionsToAllAssetBuckets(fun);
    // The function DependsOn its role's default policy, so a statement there that names the function's own
    // ARN is a circular reference; the self-invoke grant is a standalone policy on the same role.
    new iam.Policy(scope, `${name}SelfInvokePolicy`, {
        roles: [fun.role as iam.IRole],
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [fun.functionArn],
            }),
        ],
    });
    applyVectorLambdaSecurity(fun, storageResources, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

/**
 * Consumes the system-workflow launch queue and runs the system GenAI workflow once per message as
 * SYSTEM_USER. The InvokeFunction grant on the execute-workflow Lambda is added by the construct, which
 * knows the target function's name.
 */
export function buildSystemWorkflowLauncherFunction(
    scope: Construct,
    storageResources: storageResources,
    config: Config.Config,
    lambdaCommonBaseLayer: LayerVersion,
    vpc?: ec2.IVpc,
    subnets?: ec2.ISubnet[],
    extraEnv?: Record<string, string>
): lambda.Function {
    const name = "systemWorkflowLauncher";
    const fun = new lambda.Function(scope, name, {
        code: backendCode(),
        handler: `handlers.osVectorSearch.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vectorLambdaPlacement(config, vpc, subnets),
        environment: {
            // The workflow database id is the shared literal of infra/common/systemPipelines.ts.
            GENAI_METADATA_WORKFLOW_DATABASE_ID: SYSTEM_WORKFLOW_DATABASE_ID,
            ...(extraEnv ?? {}),
        },
    });
    // The system workflow's fileUpload trigger row supplies the default template a reindex run uses.
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    applyVectorLambdaSecurity(fun, storageResources, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

/**
 * The search-domain Lambda behind POST /search/nlp: query embedding through Bedrock and similarity
 * search on the vector embeddings table. Its own IAM statements name exact resources: the vector
 * table and its one index for SearchVectors, the one embedding model for InvokeModel (through the
 * shared Bedrock statement, exact for a plain id and for an inference profile alike), and, only when
 * an OpenSearch mode is on, the three aos/* parameters the lazily imported /search code reads.
 */
export function buildVectorSearchFunction(
    scope: Construct,
    storageResources: storageResources,
    config: Config.Config,
    lambdaCommonBaseLayer: LayerVersion,
    vpc?: ec2.IVpc,
    subnets?: ec2.ISubnet[],
    extraEnv?: Record<string, string>
): lambda.Function {
    const name = "vectorSearchService";
    const openSearchEnabled =
        config.app.openSearch.useProvisioned.enabled || config.app.openSearch.useServerless.enabled;
    const fun = new lambda.Function(scope, name, {
        code: backendCode(),
        handler: `handlers.osVectorSearch.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(2),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vectorLambdaPlacement(config, vpc, subnets),
        environment: {
            ...vectorIndexEnvironment(config),
            OPENSEARCH_ENDPOINT_SSM_PARAM: config.openSearchDomainEndpointSSMParam,
            OPENSEARCH_ASSET_INDEX_SSM_PARAM: config.openSearchAssetIndexNameSSMParam,
            OPENSEARCH_FILE_INDEX_SSM_PARAM: config.openSearchFileIndexNameSSMParam,
            OPENSEARCH_TYPE: config.app.openSearch.useProvisioned.enabled
                ? "provisioned"
                : "serverless",
            OPENSEARCH_DISABLED: openSearchEnabled ? "false" : "true",
            ...(extraEnv ?? {}),
        },
    });

    const vectorTable = storageResources.dynamo.vectorEmbeddingsStorageTable;
    // Similarity search on the one vector index; the statement names the table and that index only.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:SearchVectors"],
            resources: [
                vectorTable.tableArn,
                `${vectorTable.tableArn}/index/${config.vectorIndexName}`,
            ],
        })
    );
    // Query embeddings come from the configured embedding model and no other; the shared statement
    // names the exact model (and, for an inference profile, the exact profile) — see its builder.
    grantBedrockInvokeModel(fun, config, [
        config.app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingModelId,
    ]);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    if (openSearchEnabled) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:GetParameter"],
                resources: [
                    config.openSearchDomainEndpointSSMParam,
                    config.openSearchAssetIndexNameSSMParam,
                    config.openSearchFileIndexNameSSMParam,
                ].map((param) => Service("SSM").ARN("parameter", param.replace(/^\//, ""))),
            })
        );
    }
    applyVectorLambdaSecurity(fun, storageResources, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}
