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
import { Duration, RemovalPolicy, NestedStack } from "aws-cdk-lib";
import { BlockPublicAccess } from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { requireTLSAndAdditionalPolicyAddToResourcePolicy } from "../../helper/security";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";
import { kmsKeyPolicyStatementPrincipalGenerator } from "../../helper/security";
import * as s3AssetBuckets from "../../helper/s3AssetBuckets";
import { createPopulateS3AssetBucketsTableCustomResource } from "./customResources/populateS3AssetBucketsTable";

export interface storageResources {
    encryption: {
        kmsKey?: kms.IKey;
    };
    s3: {
        //Asset buckets are now tracked in s3AssetBuckets.ts global utility for ease of permissioning
        assetAuxiliaryBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
    };
    sns: {
        //Created/Deleted notification events are now tracked in s3AssetBuckets.ts global utility for ease of assignment
        eventEmailSubscriptionTopic: sns.Topic;
    };
    dynamo: {
        appFeatureEnabledStorageTable: dynamodb.Table;
        assetLinksStorageTableV2: dynamodb.Table;
        assetLinksMetadataStorageTable: dynamodb.Table;
        assetStorageTable: dynamodb.Table;
        assetUploadsStorageTable: dynamodb.Table;
        assetVersionsStorageTable: dynamodb.Table;
        assetFileVersionsStorageTable: dynamodb.Table;
        authEntitiesStorageTable: dynamodb.Table;
        commentStorageTable: dynamodb.Table;
        databaseStorageTable: dynamodb.Table;
        metadataSchemaStorageTable: dynamodb.Table;
        metadataStorageTable: dynamodb.Table;
        pipelineStorageTable: dynamodb.Table;
        rolesStorageTable: dynamodb.Table;
        s3AssetBucketsStorageTable: dynamodb.Table;
        subscriptionsStorageTable: dynamodb.Table;
        tagStorageTable: dynamodb.Table;
        tagTypeStorageTable: dynamodb.Table;
        userRolesStorageTable: dynamodb.Table;
        userStorageTable: dynamodb.Table;
        workflowExecutionsStorageTable: dynamodb.Table;
        workflowStorageTable: dynamodb.Table;
    };
}

export class StorageResourcesBuilderNestedStack extends NestedStack {
    public storageResources: storageResources;

    constructor(parent: Construct, name: string, config: Config.Config) {
        super(parent, name);

        this.storageResources = storageResourcesBuilder(this, config);

        //Nag supressions
        const reason =
            "The custom resource CDK bucket deployment needs full access to the bucket to deploy files";
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/Action::s3:.*/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*/g",
                        },
                    ],
                },
            ],
            true
        );

        if (!this.storageResources.encryption.kmsKey) {
            NagSuppressions.addResourceSuppressions(
                this,
                [
                    {
                        id: "AwsSolutions-SNS2",
                        reason: "Encryption not provided due to customer configuration of not wanting to use a KMS encryption key in VAMS",
                    },
                ],
                true
            );
        }

        //Write final outputs
        const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
        let assetBucketIndex = 0;
        for (const record of assetBucketRecords) {
            const assetBucketOutput = new cdk.CfnOutput(
                this,
                "AssetBucketNameOutput" + assetBucketIndex,
                {
                    value: record.bucket.bucketName,
                    description: "S3 bucket for asset storage - IndexCount:" + assetBucketIndex,
                }
            );
            assetBucketIndex = assetBucketIndex + 1;
        }

        const assetAuxiliaryBucketOutput = new cdk.CfnOutput(
            this,
            "AssetAuxiliaryBucketNameOutput",
            {
                value: this.storageResources.s3.assetAuxiliaryBucket.bucketName,
                description:
                    "S3 bucket for auto-generated auxiliary working objects associated with asset storage to include auto-generated previews, visualizer files, temporary storage for pipelines",
            }
        );

        const artefactsBucketOutput = new cdk.CfnOutput(this, "ArtefactsBucketNameOutput", {
            value: this.storageResources.s3.artefactsBucket.bucketName,
            description: "S3 bucket for artefacts",
        });
    }
}

export function storageResourcesBuilder(scope: Construct, config: Config.Config): storageResources {
    //Import or generate new encryption keys
    let kmsEncryptionKey: kms.IKey | undefined = undefined;
    if (config.app.useKmsCmkEncryption.enabled) {
        if (
            config.app.useKmsCmkEncryption.optionalExternalCmkArn &&
            config.app.useKmsCmkEncryption.optionalExternalCmkArn != "" &&
            config.app.useKmsCmkEncryption.optionalExternalCmkArn != "UNDEFINED"
        ) {
            kmsEncryptionKey = kms.Key.fromKeyArn(
                scope,
                "VAMSEncryptionKMSKey",
                config.app.useKmsCmkEncryption.optionalExternalCmkArn
            );
        } else {
            kmsEncryptionKey = new kms.Key(scope, "VAMSEncryptionKMSKey", {
                description: "VAMS Generated KMS Encryption key",
                enableKeyRotation: true,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            //Add policy
            kmsEncryptionKey.addToResourcePolicy(
                kmsKeyPolicyStatementPrincipalGenerator(config, kmsEncryptionKey)
            );
        }
    }

    const dynamodbDefaultProps: Partial<dynamodb.TableProps> = {
        // Regarding DynamoDB contributorInsightsEnabled setting:
        // - there is a low quota on the number of tables that can use this setting:
        //      https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Limits.html
        // Note: contributor insights is disabled (by default) to avoid reaching rule limits
        // on tables (LimitExceededException) during regular deployments.
        //
        contributorInsightsSpecification: {
            enabled: false,
        },
        pointInTimeRecoverySpecification: {
            pointInTimeRecoveryEnabled: true,
        },
        removalPolicy: RemovalPolicy.DESTROY,
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: config.app.useKmsCmkEncryption.enabled
            ? dynamodb.TableEncryption.CUSTOMER_MANAGED
            : dynamodb.TableEncryption.AWS_MANAGED,
        encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
    };

    const s3DefaultProps: Partial<s3.BucketProps> = {
        // Uncomment the next two lines to enable auto deletion of objects for easier cleanup of test environments.
        // autoDeleteObjects: true,
        // removalPolicy: RemovalPolicy.DESTROY,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        versioned: true,
        encryption: config.app.useKmsCmkEncryption.enabled
            ? s3.BucketEncryption.KMS
            : s3.BucketEncryption.S3_MANAGED,
        encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        bucketKeyEnabled: config.app.useKmsCmkEncryption.enabled ? true : false,
        objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
    };

    const accessLogsBucket = new s3.Bucket(scope, "AccessLogsBucket", {
        ...s3DefaultProps,
        lifecycleRules: [
            {
                enabled: true,
                expiration: Duration.days(90),
                noncurrentVersionExpiration: Duration.days(90),
            },
        ],
    });
    requireTLSAndAdditionalPolicyAddToResourcePolicy(accessLogsBucket, config);

    NagSuppressions.addResourceSuppressions(accessLogsBucket, [
        {
            id: "AwsSolutions-S1",
            reason: "This is an access logs bucket, we do not want access logs to be reporting to itself, causing a loop.",
        },
    ]);

    // Check if asset buckets are defined in config
    let assetBucket: s3.Bucket | undefined = undefined;

    //Create new asset bucket based on configuration
    if (config.app.assetBuckets.createNewBucket) {
        // Create default bucket as before
        assetBucket = new s3.Bucket(scope, "AssetBucket", {
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
            lifecycleRules: [
                {
                    enabled: true,
                    abortIncompleteMultipartUploadAfter: Duration.days(7),
                },
            ],
            serverAccessLogsBucket: accessLogsBucket,
            serverAccessLogsPrefix: "asset-bucket-logs/",
        });
        requireTLSAndAdditionalPolicyAddToResourcePolicy(assetBucket, config);

        // Add to global array with default prefix '/'
        s3AssetBuckets.addS3AssetBucket(
            assetBucket,
            "/",
            config.app.assetBuckets.defaultNewBucketSyncDatabaseId
        );
    }

    //Load external buckets based on configuration
    if (
        config.app.assetBuckets.externalAssetBuckets &&
        config.app.assetBuckets.externalAssetBuckets.length > 0
    ) {
        // Look up each bucket and add to global array
        for (const bucketConfig of config.app.assetBuckets.externalAssetBuckets) {
            if (
                !bucketConfig.defaultSyncDatabaseId ||
                bucketConfig.defaultSyncDatabaseId == "" ||
                bucketConfig.defaultSyncDatabaseId == "UNDEFINED"
            ) {
                throw new Error(
                    `External bucket ${bucketConfig.bucketArn} is missing defaultSyncDatabaseId`
                );
            }

            //If bucketConfig.baseAssetsPrefix doesn't end in a slash, error
            if (
                bucketConfig.baseAssetsPrefix &&
                bucketConfig.baseAssetsPrefix != "" &&
                bucketConfig.baseAssetsPrefix != "/" &&
                !bucketConfig.baseAssetsPrefix.endsWith("/")
            ) {
                throw new Error(
                    `External bucket ${bucketConfig.bucketArn} baseAssetsPrefix must end in a slash`
                );
            }

            const bucket = s3.Bucket.fromBucketArn(
                scope,
                `ImportedAssetBucket-${bucketConfig.bucketArn}`,
                bucketConfig.bucketArn
            );

            requireTLSAndAdditionalPolicyAddToResourcePolicy(bucket, config);

            s3AssetBuckets.addS3AssetBucket(
                bucket,
                bucketConfig.baseAssetsPrefix,
                bucketConfig.defaultSyncDatabaseId
            );
        }
    }

    /**
     * Create per-bucket SNS topics for S3 Asset Creation/Deletion
     */

    // Helper function to create SNS topics with proper policies
    function createSNSTopicWithPolicy(
        constructScope: Construct,
        id: string,
        encryptionKey: kms.IKey | undefined
    ): sns.Topic {
        // Create the topic
        const topic = new sns.Topic(constructScope, id, {
            masterKey: encryptionKey, //Key undefined if not using KMS
            enforceSSL: true,
        });

        return topic;
    }

    // Create per-bucket SNS topics and add event notifications
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    let index = 0;
    for (const record of assetBucketRecords) {
        // Create bucket-specific SNS topics
        const createdTopic = createSNSTopicWithPolicy(
            scope,
            `${config.app.baseStackName}-S3ObjectCreatedTopic-${index}`,
            kmsEncryptionKey
        );
        index = index + 1;

        const removedTopic = createSNSTopicWithPolicy(
            scope,
            `${config.app.baseStackName}-S3ObjectRemovedTopic-${index}`,
            kmsEncryptionKey
        );
        index = index + 1;

        // Store the topics in the bucket record
        record.snsS3ObjectCreatedTopic = createdTopic;
        record.snsS3ObjectDeletedTopic = removedTopic;

        // Use the prefix from the bucket record
        const prefix = record.prefix || "/";

        //S3 Event notifications doesn't like "/" for prefix filters (doesn't error but doesn't work either)
        //Assume no prefix in this scenario
        if (prefix == "/") {
            // Add event notifications using the bucket-specific topics
            record.bucket.addEventNotification(
                s3.EventType.OBJECT_CREATED,
                new s3not.SnsDestination(createdTopic)
            );

            record.bucket.addEventNotification(
                s3.EventType.OBJECT_REMOVED,
                new s3not.SnsDestination(removedTopic)
            );
        } else {
            // Add event notifications using the bucket-specific topics
            record.bucket.addEventNotification(
                s3.EventType.OBJECT_CREATED,
                new s3not.SnsDestination(createdTopic),
                { prefix: prefix }
            );

            record.bucket.addEventNotification(
                s3.EventType.OBJECT_REMOVED,
                new s3not.SnsDestination(removedTopic),
                { prefix: prefix }
            );
        }

        console.log(
            `Added per-bucket event notifications for bucket ${record.bucket.bucketName} with prefix ${prefix}`
        );
    }

    // Event Email Subscription Topic
    const EventEmailSubscriptionTopic = new sns.Topic(scope, "EventEmailSubscriptionTopic", {
        masterKey: kmsEncryptionKey, //Key undefined if not using KMS
        enforceSSL: true,
    });

    const assetAuxiliaryBucket = new s3.Bucket(scope, "AssetAuxiliaryBucket", {
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
        lifecycleRules: [
            {
                enabled: true,
                abortIncompleteMultipartUploadAfter: Duration.days(14),
            },
        ],
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "assetAuxiliary-bucket-logs/",
    });
    requireTLSAndAdditionalPolicyAddToResourcePolicy(assetAuxiliaryBucket, config);

    const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
        ...s3DefaultProps,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "artefacts-bucket-logs/",
    });
    requireTLSAndAdditionalPolicyAddToResourcePolicy(artefactsBucket, config);

    new s3deployment.BucketDeployment(scope, "DeployArtefacts", {
        sources: [s3deployment.Source.asset("./lib/artefacts")],
        destinationBucket: artefactsBucket,
    });

    //S3 Buckets handling for assets
    const s3AssetBucketsStorageTable = new dynamodb.Table(scope, "S3AssetBucketsStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "bucketId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "bucketName:baseAssetsPrefix",
            type: dynamodb.AttributeType.STRING,
        },
        //Columns: bucketName, baseAssetsPrefix, isVersioningEnabled
    });

    s3AssetBucketsStorageTable.addGlobalSecondaryIndex({
        indexName: "bucketNameGSI",
        partitionKey: {
            name: "bucketName",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "baseAssetsPrefix",
            type: dynamodb.AttributeType.STRING,
        },
    });

    // Create a custom resource to populate the S3AssetBucketsStorageTable with bucket information
    // Pass the newly created bucket as a dependency if we created one
    const newlyCreatedBucket = assetBucket
        ? assetBucket instanceof s3.Bucket
            ? assetBucket
            : undefined
        : undefined;
    const populateS3AssetBucketsTable = createPopulateS3AssetBucketsTableCustomResource(
        scope,
        "PopulateS3AssetBucketsTable",
        s3AssetBucketsStorageTable,
        newlyCreatedBucket
    );

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

    const appFeatureEnabledStorageTable = new dynamodb.Table(
        scope,
        "AppFeatureEnabledStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "featureName",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

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

    assetStorageTable.addGlobalSecondaryIndex({
        indexName: "BucketIdGSI",
        partitionKey: {
            name: "bucketId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const databaseStorageTable = new dynamodb.Table(scope, "DatabaseStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
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

    const workflowExecutionsStorageTable = new dynamodb.Table(
        scope,
        "WorkflowExecutionsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "databaseId:assetId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "executionId",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    workflowExecutionsStorageTable.addLocalSecondaryIndex({
        indexName: "WorkflowLSI",
        sortKey: {
            name: "workflowDatabaseId:workflowId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    workflowExecutionsStorageTable.addGlobalSecondaryIndex({
        indexName: "WorkflowGSI",
        partitionKey: {
            name: "workflowDatabaseId:workflowId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "executionId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    workflowExecutionsStorageTable.addGlobalSecondaryIndex({
        indexName: "ExecutionIdGSI",
        partitionKey: {
            name: "workflowId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "executionId",
            type: dynamodb.AttributeType.STRING,
        },
    });

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

    const tagStorageTable = new dynamodb.Table(scope, "TagStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "tagName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const tagTypeStorageTable = new dynamodb.Table(scope, "TagTypeStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "tagTypeName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const subscriptionsStorageTable = new dynamodb.Table(scope, "SubscriptionsStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "eventName",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "entityName_entityId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const rolesStorageTable = new dynamodb.Table(scope, "RolesStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "roleName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const userRolesStorageTable = new dynamodb.Table(scope, "UserRolesStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "userId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "roleName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const userStorageTable = new dynamodb.Table(scope, "UserStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "userId",
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

    const assetLinksStorageTableV2 = new dynamodb.Table(scope, "AssetLinksStorageTableV2.2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "assetLinkId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetLinksStorageTableV2.addGlobalSecondaryIndex({
        indexName: "fromAssetGSI",
        partitionKey: {
            name: "fromAssetDatabaseId:fromAssetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "toAssetDatabaseId:toAssetId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetLinksStorageTableV2.addGlobalSecondaryIndex({
        indexName: "toAssetGSI",
        partitionKey: {
            name: "toAssetDatabaseId:toAssetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "fromAssetDatabaseId:fromAssetId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const assetLinksMetadataStorageTable = new dynamodb.Table(
        scope,
        "AssetLinksMetadataStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "assetLinkId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "metadataKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const assetFileVersionsStorageTable = new dynamodb.Table(
        scope,
        "AssetFileVersionsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "assetId:assetVersionId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "fileKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const assetVersionsStorageTable = new dynamodb.Table(scope, "AssetVersionsStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetVersionId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const assetUploadsStorageTable = new dynamodb.Table(scope, "AssetUploadsStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "uploadId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetUploadsStorageTable.addGlobalSecondaryIndex({
        indexName: "AssetIdGSI",
        partitionKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "uploadId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetUploadsStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdGSI",
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "uploadId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetUploadsStorageTable.addGlobalSecondaryIndex({
        indexName: "UserIdGSI",
        partitionKey: {
            name: "UserId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "createdAt",
            type: dynamodb.AttributeType.STRING,
        },
    });

    ///DEPRECATED TABLES - KEPT FOR DATA MIGRATION PURPOSES

    const assetLinksStorageTableDeprecated = new dynamodb.Table(scope, "AssetLinksStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "assetIdFrom",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetIdTo",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetLinksStorageTableDeprecated.addGlobalSecondaryIndex({
        indexName: "AssetIdFromGSI",
        partitionKey: {
            name: "assetIdFrom",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetLinksStorageTableDeprecated.addGlobalSecondaryIndex({
        indexName: "AssetIdToGSI",
        partitionKey: {
            name: "assetIdTo",
            type: dynamodb.AttributeType.STRING,
        },
    });

    ///DEPRECATED TABLES

    return {
        encryption: {
            kmsKey: kmsEncryptionKey,
        },
        s3: {
            assetAuxiliaryBucket: assetAuxiliaryBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
        },
        sns: {
            eventEmailSubscriptionTopic: EventEmailSubscriptionTopic,
        },
        dynamo: {
            appFeatureEnabledStorageTable: appFeatureEnabledStorageTable,
            assetLinksStorageTableV2: assetLinksStorageTableV2,
            assetLinksMetadataStorageTable: assetLinksMetadataStorageTable,
            assetStorageTable: assetStorageTable,
            assetUploadsStorageTable: assetUploadsStorageTable,
            assetFileVersionsStorageTable: assetFileVersionsStorageTable,
            assetVersionsStorageTable: assetVersionsStorageTable,
            commentStorageTable: commentStorageTable,
            pipelineStorageTable: pipelineStorageTable,
            databaseStorageTable: databaseStorageTable,
            workflowStorageTable: workflowStorageTable,
            workflowExecutionsStorageTable: workflowExecutionsStorageTable,
            metadataStorageTable: metadataStorageTable,
            authEntitiesStorageTable: authEntitiesTable,
            metadataSchemaStorageTable: metadataSchemaStorageTable,
            tagStorageTable: tagStorageTable,
            tagTypeStorageTable: tagTypeStorageTable,
            s3AssetBucketsStorageTable: s3AssetBucketsStorageTable,
            subscriptionsStorageTable: subscriptionsStorageTable,
            rolesStorageTable: rolesStorageTable,
            userRolesStorageTable: userRolesStorageTable,
            userStorageTable: userStorageTable,
        },
    };
}
