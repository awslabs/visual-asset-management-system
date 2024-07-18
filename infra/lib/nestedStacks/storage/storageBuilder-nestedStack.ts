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

export interface storageResources {
    encryption: {
        kmsKey?: kms.IKey;
    };
    s3: {
        assetBucket: s3.Bucket;
        assetAuxiliaryBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
        assetStagingBucket?: s3.IBucket;
        //assetAuxiliaryStagingBucket?: s3.IBucket;
    };
    sns: {
        assetBucketObjectCreatedTopic: sns.Topic;
        assetBucketObjectRemovedTopic: sns.Topic;
        eventEmailSubscriptionTopic: sns.Topic;
    };
    dynamo: {
        appFeatureEnabledStorageTable: dynamodb.Table;
        assetLinksStorageTable: dynamodb.Table;
        assetStorageTable: dynamodb.Table;
        authEntitiesStorageTable: dynamodb.Table;
        commentStorageTable: dynamodb.Table;
        databaseStorageTable: dynamodb.Table;
        jobStorageTable: dynamodb.Table;
        metadataSchemaStorageTable: dynamodb.Table;
        metadataStorageTable: dynamodb.Table;
        pipelineStorageTable: dynamodb.Table;
        rolesStorageTable: dynamodb.Table;
        subscriptionsStorageTable: dynamodb.Table;
        tagStorageTable: dynamodb.Table;
        tagTypeStorageTable: dynamodb.Table;
        userRolesStorageTable: dynamodb.Table;
        workflowExecutionStorageTable: dynamodb.Table;
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
        const assetBucketOutput = new cdk.CfnOutput(this, "AssetBucketNameOutput", {
            value: this.storageResources.s3.assetBucket.bucketName,
            description: "S3 bucket for asset storage",
        });

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
                kmsKeyPolicyStatementPrincipalGenerator(kmsEncryptionKey)
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
        contributorInsightsEnabled: false,
        pointInTimeRecovery: true,
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
                expiration: Duration.days(30),
                noncurrentVersionExpiration: Duration.days(30),
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

    const assetBucket = new s3.Bucket(scope, "AssetBucket", {
        ...s3DefaultProps,
        transferAcceleration: true,
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
    requireTLSAndAdditionalPolicyAddToResourcePolicy(assetBucket, config);

    /**
     * SNS Fan out for S3 Asset Creation/Deletion
     */

    // Object Create Topic
    const S3AssetsObjectCreatedTopic = new sns.Topic(scope, "S3AssetsObjectCreatedTopic", {
        masterKey: kmsEncryptionKey, //Key undefined if not using KMS
    });

    //Set TLS HTTPS on SNS Create topic
    const S3AssetsObjectCreatedTopicPolicy = new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ["sns:Publish"],
        resources: [S3AssetsObjectCreatedTopic.topicArn],
        conditions: {
            Bool: {
                "aws:SecureTransport": "false",
            },
        },
    });
    S3AssetsObjectCreatedTopic.addToResourcePolicy(S3AssetsObjectCreatedTopicPolicy);

    // Object Removed Topic
    const S3AssetsObjectRemovedTopic = new sns.Topic(scope, "S3AssetsObjectRemovedTopic", {
        masterKey: kmsEncryptionKey, //Key undefined if not using KMS
    });

    //Set TLS HTTPS on SNS Removed topic
    const S3AssetsObjectRemovedTopicPolicy = new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ["sns:Publish"],
        resources: [S3AssetsObjectRemovedTopic.topicArn],
        conditions: {
            Bool: {
                "aws:SecureTransport": "false",
            },
        },
    });

    S3AssetsObjectRemovedTopic.addToResourcePolicy(S3AssetsObjectRemovedTopicPolicy);

    //Add S3 Asset Bucket event notifications for SNS Topics
    assetBucket.addEventNotification(
        s3.EventType.OBJECT_CREATED,
        new s3not.SnsDestination(S3AssetsObjectCreatedTopic)
    );

    assetBucket.addEventNotification(
        s3.EventType.OBJECT_REMOVED,
        new s3not.SnsDestination(S3AssetsObjectRemovedTopic)
    );

    // Event Email Subscription Topic
    const EventEmailSubscriptionTopic = new sns.Topic(scope, "EventEmailSubscriptionTopic", {
        masterKey: kmsEncryptionKey, //Key undefined if not using KMS
    });

    //Set TLS HTTPS on SNS Event Email Subscription
    const EventEmailSubscriptionTopicPolicy = new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ["sns:Publish"],
        resources: [EventEmailSubscriptionTopic.topicArn],
        conditions: {
            Bool: {
                "aws:SecureTransport": "false",
            },
        },
    });

    EventEmailSubscriptionTopic.addToResourcePolicy(EventEmailSubscriptionTopicPolicy);

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

    //Bucket Staging Migration Setup
    let assetStagingBucket = undefined;
    if (
        config.app.bucketMigrationStaging.assetBucketName &&
        config.app.bucketMigrationStaging.assetBucketName != "" &&
        config.app.bucketMigrationStaging.assetBucketName != "UNDEFINED"
    )
        assetStagingBucket = s3.Bucket.fromBucketName(
            scope,
            "Asset Staging Bucket",
            config.app.bucketMigrationStaging.assetBucketName
        );

    // let assetAuxiliaryStagingBucket = undefined;
    // if (config.app.bucketMigrationStaging.assetAuxiliaryBucketName && config.app.bucketMigrationStaging.assetAuxiliaryBucketName != "" && config.app.bucketMigrationStaging.assetAuxiliaryBucketName != "UNDEFINED")
    //     assetAuxiliaryStagingBucket = s3.Bucket.fromBucketName(scope, "Asset Visualizer Staging Bucket", config.app.bucketMigrationStaging.assetAuxiliaryBucketName);

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

    subscriptionsStorageTable.addGlobalSecondaryIndex({
        indexName: "eventName-entityName_entityId-index",
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

    const assetLinksStorageTable = new dynamodb.Table(scope, "AssetLinksStorageTable", {
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

    assetLinksStorageTable.addGlobalSecondaryIndex({
        indexName: "AssetIdFromGSI",
        partitionKey: {
            name: "assetIdFrom",
            type: dynamodb.AttributeType.STRING,
        },
    });

    assetLinksStorageTable.addGlobalSecondaryIndex({
        indexName: "AssetIdToGSI",
        partitionKey: {
            name: "assetIdTo",
            type: dynamodb.AttributeType.STRING,
        },
    });

    return {
        encryption: {
            kmsKey: kmsEncryptionKey,
        },
        s3: {
            assetBucket: assetBucket,
            assetAuxiliaryBucket: assetAuxiliaryBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
            assetStagingBucket: assetStagingBucket,
            //assetAuxiliaryStagingBucket: assetAuxiliaryStagingBucket,
        },
        sns: {
            assetBucketObjectCreatedTopic: S3AssetsObjectCreatedTopic,
            assetBucketObjectRemovedTopic: S3AssetsObjectRemovedTopic,
            eventEmailSubscriptionTopic: EventEmailSubscriptionTopic,
        },
        dynamo: {
            appFeatureEnabledStorageTable: appFeatureEnabledStorageTable,
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
            tagStorageTable: tagStorageTable,
            tagTypeStorageTable: tagTypeStorageTable,
            subscriptionsStorageTable: subscriptionsStorageTable,
            assetLinksStorageTable: assetLinksStorageTable,
            rolesStorageTable: rolesStorageTable,
            userRolesStorageTable: userRolesStorageTable,
        },
    };
}
