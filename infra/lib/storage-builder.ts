/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as s3 from "aws-cdk-lib/aws-s3";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import { Duration, RemovalPolicy } from "aws-cdk-lib";
import { BlockPublicAccess } from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { requireTLSAddToResourcePolicy } from "./security";
import { NagSuppressions } from "cdk-nag";

export interface storageResources {
    s3: {
        assetBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
        sagemakerBucket: s3.Bucket;
        stagingBucket?: s3.IBucket;
    };
    dynamo: {
        assetStorageTable: dynamodb.Table;
        jobStorageTable: dynamodb.Table;
        pipelineStorageTable: dynamodb.Table;
        databaseStorageTable: dynamodb.Table;
        workflowStorageTable: dynamodb.Table;
        workflowExecutionStorageTable: dynamodb.Table;
        metadataStorageTable: dynamodb.Table;
        authEntitiesStorageTable: dynamodb.Table;
        metadataSchemaStorageTable: dynamodb.Table;
    };
}
export function storageResourcesBuilder(
    scope: Construct,
    staging_bucket?: string
): storageResources {
    // dynamodb contributorInsightsEnabled
    const dynamodbDefaultProps: Partial<dynamodb.TableProps> = {
        // there is a low quota on the number of tables that can use this setting
        // https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Limits.html
        // set to false to avoid this when creating lots of environments
        contributorInsightsEnabled: true,
        pointInTimeRecovery: true,
        removalPolicy: RemovalPolicy.DESTROY,
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
    };

    const s3DefaultProps: Partial<s3.BucketProps> = {
        // Uncomment the next two lines to enable auto deletion of objects for easier cleanup of test environments.
        // autoDeleteObjects: true,
        // removalPolicy: RemovalPolicy.DESTROY,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        versioned: true,
        encryption: s3.BucketEncryption.S3_MANAGED,
        objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
    };

    const accessLogsBucket = new s3.Bucket(scope, "AccessLogsBucket", {
        ...s3DefaultProps,
        lifecycleRules: [
            {
                enabled: true,
                expiration: Duration.days(1),
                noncurrentVersionExpiration: Duration.days(1),
            },
        ],
    });

    requireTLSAddToResourcePolicy(accessLogsBucket);

    NagSuppressions.addResourceSuppressions(accessLogsBucket, [
        {
            id: "AwsSolutions-S1",
            reason: "This is an access logs bucket, we do not want access logs to be reporting to itself, causing a loop.",
        },
    ]);

    const assetBucket = new s3.Bucket(scope, "AssetBucket", {
        ...s3DefaultProps,
        cors: [
            {
                allowedOrigins: ["*"],
                allowedHeaders: ["*"],
                allowedMethods: [
                    s3.HttpMethods.GET,
                    s3.HttpMethods.PUT,
                    s3.HttpMethods.POST,
                    s3.HttpMethods.HEAD,
                ],
                exposedHeaders: ["ETag"],
            },
        ],
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "asset-bucket-logs/",
    });
    requireTLSAddToResourcePolicy(assetBucket);

    const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
        ...s3DefaultProps,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "artefacts-bucket-logs/",
    });
    requireTLSAddToResourcePolicy(artefactsBucket);

    const sagemakerBucket = new s3.Bucket(scope, "SagemakerBucket", {
        ...s3DefaultProps,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "sagemaker-bucket-logs/",
    });
    requireTLSAddToResourcePolicy(sagemakerBucket);

    let stagingBucket = undefined;
    if (staging_bucket)
        stagingBucket = s3.Bucket.fromBucketName(scope, "Staging Bucket", staging_bucket);

    new s3deployment.BucketDeployment(scope, "DeployArtefacts", {
        sources: [s3deployment.Source.asset("./lib/artefacts")],
        destinationBucket: artefactsBucket,
    });

    const assetStorageTable = new dynamodb.Table(scope, "AssetStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        stream: dynamodb.StreamViewType.NEW_IMAGE,
    });

    const databaseStorageTable = new dynamodb.Table(scope, "DatabaseStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const jobStorageTable = new dynamodb.Table(scope, "JobStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "jobId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const pipelineStorageTable = new dynamodb.Table(scope, "PipelineStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "pipelineId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const workflowStorageTable = new dynamodb.Table(scope, "WorkflowStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "workflowId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const workflowExecutionStorageTable = new dynamodb.Table(
        scope,
        "WorkflowExecutionStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "pk",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "sk",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const metadataStorageTable = new dynamodb.Table(scope, "MetadataStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        stream: dynamodb.StreamViewType.NEW_IMAGE,
    });

    const metadataSchemaStorageTable = new dynamodb.Table(scope, "MetadataSchemaStorageTable", {
        ...dynamodbDefaultProps,

        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "field",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const authEntitiesTable = new dynamodb.Table(scope, "AuthEntitiesTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "entityType",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "sk",
            type: dynamodb.AttributeType.STRING,
        },
    });

    return {
        s3: {
            assetBucket: assetBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
            sagemakerBucket: sagemakerBucket,
            stagingBucket: stagingBucket,
        },
        dynamo: {
            assetStorageTable: assetStorageTable,
            jobStorageTable: jobStorageTable,
            pipelineStorageTable: pipelineStorageTable,
            databaseStorageTable: databaseStorageTable,
            workflowStorageTable: workflowStorageTable,
            workflowExecutionStorageTable: workflowExecutionStorageTable,
            metadataStorageTable: metadataStorageTable,
            authEntitiesStorageTable: authEntitiesTable,
            metadataSchemaStorageTable: metadataSchemaStorageTable,
        },
    };
}
