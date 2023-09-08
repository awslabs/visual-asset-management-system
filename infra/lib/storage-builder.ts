/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as s3 from "aws-cdk-lib/aws-s3";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as sns from "aws-cdk-lib/aws-sns";
import * as kms from "aws-cdk-lib/aws-kms";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3not from "aws-cdk-lib/aws-s3-notifications";
import * as cdk from "aws-cdk-lib";
import { Duration, RemovalPolicy } from "aws-cdk-lib";
import { BlockPublicAccess } from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { requireTLSAddToResourcePolicy } from "./security";
import { NagSuppressions } from "cdk-nag";

export interface storageResources {
    s3: {
        assetBucket: s3.Bucket;
        assetVisualizerBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
        sagemakerBucket: s3.Bucket;
        stagingBucket?: s3.IBucket;
    };
    sns: {
        assetBucketObjectCreatedTopic: sns.Topic;
        assetBucketObjectRemovedTopic: sns.Topic;
        kmsTopicKey: kms.Key;
    };
    dynamo: {
        assetStorageTable: dynamodb.Table;
        commentStorageTable: dynamodb.Table;
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

    /**
     * SNS Fan out for S3 Asset Creation/Deletion
     */
    const topicKey = new kms.Key(scope, "AssetNotificationTopicKey", {
        description: "KMS key for AssetNotificationTopics",
        enableKeyRotation: true,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    topicKey.addToResourcePolicy(
        new iam.PolicyStatement({
            actions: ["kms:GenerateDataKey*", "kms:Decrypt"],
            resources: ["*"],
            principals: [new iam.ServicePrincipal("s3.amazonaws.com")],
        })
    );

    // Object Create Topic
    const S3AssetsObjectCreatedTopic = new sns.Topic(scope, "S3AssetsObjectCreatedTopic", {
        masterKey: topicKey,
    });

    // Object Removed Topic
    const S3AssetsObjectRemovedTopic = new sns.Topic(scope, "S3AssetsObjectRemovedTopic", {
        masterKey: topicKey,
    });

    //Add S3 Asset Bucket event notifications for SNS Topics
    assetBucket.addEventNotification(
        s3.EventType.OBJECT_CREATED,
        new s3not.SnsDestination(S3AssetsObjectCreatedTopic)
    );

    assetBucket.addEventNotification(
        s3.EventType.OBJECT_REMOVED,
        new s3not.SnsDestination(S3AssetsObjectRemovedTopic)
    );

    const assetVisualizerBucket = new s3.Bucket(scope, "AssetVisualizerBucket", {
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
        serverAccessLogsPrefix: "assetVisualizer-bucket-logs/",
    });
    requireTLSAddToResourcePolicy(assetVisualizerBucket);

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

    const commentStorageTable = new dynamodb.Table(scope, "CommentStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetVersionId:commentId",
            type: dynamodb.AttributeType.STRING,
        },
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
            assetVisualizerBucket: assetVisualizerBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
            sagemakerBucket: sagemakerBucket,
            stagingBucket: stagingBucket,
        },
        sns: {
            assetBucketObjectCreatedTopic: S3AssetsObjectCreatedTopic,
            assetBucketObjectRemovedTopic: S3AssetsObjectRemovedTopic,
            kmsTopicKey: topicKey,
        },
        dynamo: {
            assetStorageTable: assetStorageTable,
            commentStorageTable: commentStorageTable,
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
