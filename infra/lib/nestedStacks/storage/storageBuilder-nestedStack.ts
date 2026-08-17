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
import * as logs from "aws-cdk-lib/aws-logs";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as cdk from "aws-cdk-lib";
import { Duration, RemovalPolicy, NestedStack } from "aws-cdk-lib";
import { BlockPublicAccess } from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import {
    requireTLSAndAdditionalPolicyAddToResourcePolicy,
    addPresignedUrlNetworkRestrictionsToBucketPolicy,
    generateUniqueNameHash,
} from "../../helper/security";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";
import { kmsKeyPolicyStatementPrincipalGenerator } from "../../helper/security";
import * as s3AssetBuckets from "../../helper/s3AssetBuckets";
import { createPopulateS3AssetBucketsTableCustomResource } from "./customResources/populateS3AssetBucketsTable";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Service, Partition } from "../../helper/service-helper";
import {
    buildSqsBucketSyncFunction,
    buildFileIndexerSnsQueuingFunction,
    buildAssetIndexerSnsQueuingFunction,
    buildDatabaseIndexerSnsQueuingFunction,
} from "../../lambdaBuilder/searchIndexBucketSyncFunctions";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";
import { ResourceNameRegistry } from "../resourceNames/resourceNameRegistry";

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
        fileIndexerSnsTopic: sns.Topic;
        assetIndexerSnsTopic: sns.Topic;
        databaseIndexerSnsTopic: sns.Topic;
    };
    cloudWatchAuditLogGroups: {
        authentication: logs.LogGroup;
        authorization: logs.LogGroup;
        fileUpload: logs.LogGroup;
        fileDownload: logs.LogGroup;
        fileDownloadStreamed: logs.LogGroup;
        authOther: logs.LogGroup;
        authChanges: logs.LogGroup;
        actions: logs.LogGroup;
        errors: logs.LogGroup;
    };
    eventBridge: {
        orchestrationBus: events.EventBus;
        orchestrationBusAuditLogGroup: logs.LogGroup;
        eventSourcePrefix: string;
    };
    dynamo: {
        appFeatureEnabledStorageTable: dynamodb.Table;
        assetLinksStorageTableV2: dynamodb.Table;
        assetLinksMetadataStorageTable: dynamodb.Table;
        assetStorageTable: dynamodb.Table;
        assetUploadsStorageTable: dynamodb.Table;
        assetVersionsStorageTable: dynamodb.Table;
        assetFileVersionsStorageTable: dynamodb.Table;
        assetFileMetadataVersionsStorageTable: dynamodb.Table;
        authEntitiesStorageTable: dynamodb.Table;
        commentStorageTable: dynamodb.Table;
        constraintsStorageTable: dynamodb.Table;
        databaseStorageTable: dynamodb.Table;
        metadataSchemaStorageTableV2: dynamodb.Table;
        databaseMetadataStorageTable: dynamodb.Table;
        assetFileMetadataStorageTable: dynamodb.Table;
        assetFileVersionHistoryStorageTable: dynamodb.Table;
        assetHistoryStorageTable: dynamodb.Table;
        syncTrackingOutboundStorageTable: dynamodb.Table;
        fileAttributeStorageTable: dynamodb.Table;
        pipelineStorageTable: dynamodb.Table;
        rolesStorageTable: dynamodb.Table;
        s3AssetBucketsStorageTable: dynamodb.Table;
        subscriptionsStorageTable: dynamodb.Table;
        tagStorageTable: dynamodb.Table;
        tagTypeStorageTable: dynamodb.Table;
        tagStorageTableLegacy: dynamodb.Table;
        tagTypeStorageTableLegacy: dynamodb.Table;
        userRolesStorageTable: dynamodb.Table;
        userStorageTable: dynamodb.Table;
        workflowExecutionsStorageTable: dynamodb.Table;
        workflowExecutionsStorageTableV2: dynamodb.Table;
        pipelineExecutionsStorageTable: dynamodb.Table;
        pipelineExecutionInputFilesStorageTable: dynamodb.Table;
        pipelineExecutionInputMetadataStorageTable: dynamodb.Table;
        pipelineExecutionInputConfigurationStorageTable: dynamodb.Table;
        pipelineExecutionOutputFilesStorageTable: dynamodb.Table;
        pipelineExecutionOutputMetadataStorageTable: dynamodb.Table;
        pipelineExecutionOutputResultsStorageTable: dynamodb.Table;
        pipelineExecutionLogsStorageTable: dynamodb.Table;
        workflowExecutionInputsStorageTable: dynamodb.Table;
        workflowExecutionConfigurationStorageTable: dynamodb.Table;
        workflowStorageTable: dynamodb.Table;
        apiKeyStorageTable: dynamodb.Table;
        // Pipeline + workflow V2 data model tables
        pipelineStorageTableV2: dynamodb.Table;
        pipelineTemplatesStorageTable: dynamodb.Table;
        pipelineTemplateTagSchemaStorageTable: dynamodb.Table;
        workflowStorageTableV2: dynamodb.Table;
        workflowTriggersStorageTable: dynamodb.Table;
    };
}

export class StorageResourcesBuilderNestedStack extends NestedStack {
    public storageResources: storageResources;

    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[],
        resourceNameRegistry: ResourceNameRegistry
    ) {
        super(parent, name);

        this.storageResources = storageResourcesBuilder(
            this,
            config,
            lambdaCommonBaseLayer,
            vpc,
            subnets,
            resourceNameRegistry
        );

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

export function storageResourcesBuilder(
    scope: Construct,
    config: Config.Config,
    lambdaCommonBaseLayer: LayerVersion,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    resourceNameRegistry: ResourceNameRegistry
): storageResources {
    //Import or generate new encryption keys
    let kmsEncryptionKey: kms.IKey | undefined = undefined;
    // Tracks whether VAMS created the key (resource policy is mutable) versus
    // imported it by ARN (resource policy changes are a no-op).
    let vamsGeneratedKmsKey = false;
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
            vamsGeneratedKmsKey = true;
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
        // RETAIN so DynamoDB tables survive a stack teardown and protect their data. All VAMS
        // tables are auto-named (no explicit tableName), so retained orphans never collide with
        // the freshly-named tables a redeploy creates.
        removalPolicy: RemovalPolicy.RETAIN,
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
                    // Expose range/streaming headers for file streaming
                    exposedHeaders: [
                        "ETag",
                        "Accept-Ranges",
                        "Content-Range",
                        "Content-Length",
                        "Content-Encoding",
                    ],
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
        addPresignedUrlNetworkRestrictionsToBucketPolicy(
            assetBucket,
            config.app.assetBuckets.presignedUrlNetworkRestrictions
        );

        // The created bucket is the default UNLESS an imported external bucket is marked default.
        const anyExternalIsDefault = (config.app.assetBuckets.externalAssetBuckets || []).some(
            (b) => b && b.isDefault
        );
        // Add to global array with default prefix '/'
        s3AssetBuckets.addS3AssetBucket(
            assetBucket,
            "/",
            config.app.assetBuckets.defaultNewBucketSyncDatabaseId,
            undefined,
            undefined,
            !anyExternalIsDefault
        );
    }

    //Load external buckets based on configuration
    if (
        config.app.assetBuckets.externalAssetBuckets &&
        config.app.assetBuckets.externalAssetBuckets.length > 0
    ) {
        // A single bucket ARN may be registered under multiple (non-overlapping)
        // prefixes. Each unique ARN must be imported exactly once: a single IBucket
        // instance accumulates all of its addEventNotification calls into one S3
        // notification configuration with multiple prefix-filtered topic entries.
        // Importing the same ARN twice would create duplicate construct IDs and
        // racing notification custom resources that overwrite each other.
        const importedBucketsByArn = new Map<string, s3.IBucket>();

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

            // Normalize optional cross-account / encryption fields
            const bucketAccountId =
                bucketConfig.bucketAccountId &&
                bucketConfig.bucketAccountId != "" &&
                bucketConfig.bucketAccountId != "UNDEFINED"
                    ? bucketConfig.bucketAccountId
                    : undefined;
            const bucketRegion =
                bucketConfig.bucketRegion &&
                bucketConfig.bucketRegion != "" &&
                bucketConfig.bucketRegion != "UNDEFINED"
                    ? bucketConfig.bucketRegion
                    : undefined;
            const bucketKmsKeyArn =
                bucketConfig.bucketKmsKeyArn &&
                bucketConfig.bucketKmsKeyArn != "" &&
                bucketConfig.bucketKmsKeyArn != "UNDEFINED"
                    ? bucketConfig.bucketKmsKeyArn
                    : undefined;

            // Import each unique bucket ARN only once and reuse the instance for
            // every prefix registered against it.
            let bucket = importedBucketsByArn.get(bucketConfig.bucketArn);
            if (!bucket) {
                // Import the bucket account-aware so CDK treats it as cross-account when
                // an owning account is provided (drives correct event-notification source
                // handling and region resolution). Falls back to same-account behavior
                // when no account is given.
                bucket = s3.Bucket.fromBucketAttributes(
                    scope,
                    `ImportedAssetBucket-${bucketConfig.bucketArn}`,
                    {
                        bucketArn: bucketConfig.bucketArn,
                        account: bucketAccountId,
                        region: bucketRegion,
                    }
                );

                // VAMS only applies bucket-level resource policies to buckets it owns.
                // For imported (potentially cross-account) buckets this is a no-op; the
                // bucket owner applies TLS/additional policies (see external-s3 docs).
                // Apply once per unique bucket.
                requireTLSAndAdditionalPolicyAddToResourcePolicy(bucket, config);

                importedBucketsByArn.set(bucketConfig.bucketArn, bucket);
            }

            s3AssetBuckets.addS3AssetBucket(
                bucket,
                bucketConfig.baseAssetsPrefix,
                bucketConfig.defaultSyncDatabaseId,
                bucketAccountId,
                bucketKmsKeyArn,
                !!bucketConfig.isDefault
            );
        }
    }

    // When VAMS generated its own CMK and there are cross-account external buckets,
    // the S3 service in the bucket's account must be able to generate data keys with
    // the VAMS key to encrypt notifications published to the VAMS-owned SNS topics.
    // Add an additive statement scoped to those external accounts. (No-op when the
    // key was imported by ARN or when there are no cross-account buckets, so existing
    // deployments without externals see no key-policy change.)
    if (vamsGeneratedKmsKey && kmsEncryptionKey) {
        const externalBucketAccountIds = Array.from(
            new Set(
                s3AssetBuckets
                    .getS3AssetBucketRecords()
                    .map((record) => record.accountId)
                    .filter((accountId): accountId is string => !!accountId)
            )
        );

        if (externalBucketAccountIds.length > 0) {
            kmsEncryptionKey.addToResourcePolicy(
                new iam.PolicyStatement({
                    sid: "AllowExternalBucketS3Notifications",
                    effect: iam.Effect.ALLOW,
                    principals: [Service("S3").Principal],
                    actions: ["kms:GenerateDataKey*", "kms:Decrypt"],
                    resources: ["*"],
                    conditions: {
                        StringEquals: { "aws:SourceAccount": externalBucketAccountIds },
                    },
                })
            );
        }
    }

    /**
     * Create SNS topics
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

        // For cross-account buckets, explicitly allow the S3 service to publish to
        // the topics on behalf of the external bucket. CDK derives the SourceAccount
        // from the importing stack account, which is the VAMS account and would not
        // match an external bucket's account, so the auto-added condition can drop
        // notifications silently. Scope the grant to the external bucket ARN/account.
        if (record.accountId) {
            for (const topic of [createdTopic, removedTopic]) {
                topic.addToResourcePolicy(
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        principals: [Service("S3").Principal],
                        actions: ["SNS:Publish"],
                        resources: [topic.topicArn],
                        conditions: {
                            ArnLike: { "aws:SourceArn": record.bucket.bucketArn },
                            StringEquals: { "aws:SourceAccount": record.accountId },
                        },
                    })
                );
            }
        }

        console.log(
            `Added per-bucket event notifications for bucket ${record.bucket.bucketName} with prefix ${prefix}`
        );
    }

    // Event Email Subscription Topic
    const EventEmailSubscriptionTopic = createSNSTopicWithPolicy(
        scope,
        "EventEmailSubscriptionTopic",
        kmsEncryptionKey
    );

    // Create SNS topics for indexer queuing
    const FileIndexerSnsTopic = createSNSTopicWithPolicy(
        scope,
        "FileIndexerSnsTopic",
        kmsEncryptionKey
    );

    const AssetIndexerSnsTopic = createSNSTopicWithPolicy(
        scope,
        "AssetIndexerSnsTopic",
        kmsEncryptionKey
    );

    const DatabaseIndexerSnsTopic = createSNSTopicWithPolicy(
        scope,
        "DatabaseIndexerSnsTopic",
        kmsEncryptionKey
    );

    /**
     * Create CloudWatch Log Groups for Audit Logging
     */
    const auditLogGroups = {
        authentication: new logs.LogGroup(scope, "AuthenticationAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditAuthentication-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditAuthentication",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        authorization: new logs.LogGroup(scope, "AuthorizationAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditAuthorization-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditAuthorization",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        fileUpload: new logs.LogGroup(scope, "FileUploadAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditFileUpload-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditFileUpload",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        fileDownload: new logs.LogGroup(scope, "FileDownloadAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditFileDownload-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditFileDownload",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        fileDownloadStreamed: new logs.LogGroup(scope, "FileDownloadStreamedAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditFileDownloadStreamed-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditFileDownloadStreamed",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        authOther: new logs.LogGroup(scope, "AuthOtherAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditAuthOther-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditAuthOther",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        authChanges: new logs.LogGroup(scope, "AuthChangesAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditAuthChanges-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditAuthChanges",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        actions: new logs.LogGroup(scope, "ActionsAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditActions-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditActions",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
        errors: new logs.LogGroup(scope, "ErrorsAuditLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSAuditErrors-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSAuditErrors",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }),
    };

    /**
     * Create EventBridge Orchestration Bus
     */

    // Deployment-unique bus name and event source prefix so multiple deployments can coexist in a region
    const orchestrationBusName = `${config.name}-${config.app.baseStackName}-orchestration`;
    const eventSourcePrefix = `${config.name}.${config.app.baseStackName}`;

    const orchestrationBus = new events.EventBus(scope, "OrchestrationBus", {
        eventBusName: orchestrationBusName,
    });

    // KMS encryption is only settable on the underlying CfnEventBus. Event bus CMK encryption
    // is only supported in the commercial partition; elsewhere (GovCloud, EU Sovereign Cloud)
    // the KmsKeyIdentifier property is rejected by CloudFormation and the bus falls back to
    // EventBridge's default AWS-owned-key encryption at rest.
    if (config.app.useKmsCmkEncryption.enabled && kmsEncryptionKey && Partition() === "aws") {
        const cfnBus = orchestrationBus.node.defaultChild as events.CfnEventBus;
        cfnBus.kmsKeyIdentifier = kmsEncryptionKey.keyArn;
    }

    // Audit log group for the orchestration bus
    const orchestrationBusAuditLogGroup = new logs.LogGroup(
        scope,
        "OrchestrationBusAuditLogGroup",
        {
            logGroupName:
                "/aws/vendedlogs/VAMSOrchestrationBusAudit-" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "VAMSOrchestrationBusAudit",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            encryptionKey: config.app.useKmsCmkEncryption.enabled ? kmsEncryptionKey : undefined,
        }
    );

    // Audit rule: route all events from this deployment's sources to the audit log group
    const orchestrationBusAuditRule = new events.Rule(scope, "OrchestrationBusAuditRule", {
        eventBus: orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(eventSourcePrefix),
        },
    });

    orchestrationBusAuditRule.addTarget(
        new targets.CloudWatchLogGroup(orchestrationBusAuditLogGroup)
    );

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
    addPresignedUrlNetworkRestrictionsToBucketPolicy(
        assetAuxiliaryBucket,
        config.app.assetBuckets.presignedUrlNetworkRestrictions
    );

    const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
        ...s3DefaultProps,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "artefacts-bucket-logs/",
    });
    requireTLSAndAdditionalPolicyAddToResourcePolicy(artefactsBucket, config);

    new s3deployment.BucketDeployment(scope, "DeployArtefacts", {
        sources: [s3deployment.Source.asset("./lib/artefacts")],
        destinationBucket: artefactsBucket,
        // This deployment owns the bucket root and prunes anything absent from ./lib/artefacts. The
        // pipeline schema bundles live in the same bucket under vamsSchema/, uploaded by a separate
        // per-registration deployment, so they are excluded from the prune — otherwise refreshing an
        // artefact deletes every bundle while the registration resources still expect to read them.
        exclude: ["vamsSchema/*"],
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

    assetStorageTable.addGlobalSecondaryIndex({
        indexName: "assetIdGSI",
        partitionKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const databaseStorageTable = new dynamodb.Table(scope, "DatabaseStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        stream: dynamodb.StreamViewType.NEW_IMAGE,
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

    // ----------------------------------------------------------------------
    // Pipeline + Workflow Storage Overhaul (V2 data model).
    // Pipelines and workflows are database-scoped: the partition key is the
    // databaseId and the sort key is the entity id, so (databaseId, id) is unique
    // even when an id is overridden to a known value, and a database's entities
    // list with a native Query. Templates, the template tag schema, triggers, and
    // the output-asset execution index hang off these entities. All tables follow
    // the shared dynamodbDefaultProps (RETAIN, KMS, PITR, auto-named).
    // ----------------------------------------------------------------------

    const pipelineStorageTableV2 = new dynamodb.Table(scope, "PipelineStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "pipelineId", type: dynamodb.AttributeType.STRING },
    });
    // List not-archived pipelines within a database ordered by last modified.
    pipelineStorageTableV2.addGlobalSecondaryIndex({
        indexName: "PipelinesByDatabaseGSI",
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "dateModified", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    // List a database's pipelines within a category.
    pipelineStorageTableV2.addGlobalSecondaryIndex({
        indexName: "PipelinesByCategoryGSI",
        partitionKey: { name: "databaseId:category", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "pipelineId", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    // Global (cross-database) pipeline list: constant partition + dateModified, so the "all
    // pipelines" list is a single query instead of a table scan.
    pipelineStorageTableV2.addGlobalSecondaryIndex({
        indexName: "PipelinesByDateGSI",
        partitionKey: { name: "allListPartition", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "dateModified", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // One row per (pipeline, template). Composite PK matches the pipeline table so a
    // database-scoped pipeline's templates are unambiguous; SK is the template id.
    const pipelineTemplatesStorageTable = new dynamodb.Table(
        scope,
        "PipelineTemplatesStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "pipelineDatabaseId:pipelineId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: { name: "templateId", type: dynamodb.AttributeType.STRING },
        }
    );

    // One row per template holding its tag-field definitions inline as a JSON string
    // (mirrors MetadataSchemaStorageTableV2: UUID PK + composite owner SK + owner GSI).
    // Kept separate from the template row so a lengthy tag schema does not consume the
    // template row's size budget.
    const pipelineTemplateTagSchemaStorageTable = new dynamodb.Table(
        scope,
        "PipelineTemplateTagSchemaStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "tagSchemaId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "pipelineDatabaseId:pipelineId:templateId",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );
    // Resolve a template's tag schema by its owner composite key.
    pipelineTemplateTagSchemaStorageTable.addGlobalSecondaryIndex({
        indexName: "TagSchemaByTemplateGSI",
        partitionKey: {
            name: "pipelineDatabaseId:pipelineId:templateId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: { name: "tagSchemaId", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const workflowStorageTableV2 = new dynamodb.Table(scope, "WorkflowStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "workflowId", type: dynamodb.AttributeType.STRING },
    });
    workflowStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowsByDatabaseGSI",
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "dateModified", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    workflowStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowsByCategoryGSI",
        partitionKey: { name: "databaseId:category", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "workflowId", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    // Global (cross-database) workflow list: constant partition + dateModified, so the "all
    // workflows" list is a single query instead of a table scan.
    workflowStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowsByDateGSI",
        partitionKey: { name: "allListPartition", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "dateModified", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // One row per (workflow, trigger). Composite PK matches the workflow table; SK is the trigger type
    // for a workflow's FIRST trigger of that type, or "type#triggerId" for an additional one — a
    // workflow may carry several triggers of one type, each with its own file filters and default
    // templates. A by-type GSI lets the upload dispatcher find candidate workflows without scanning;
    // file-filter evaluation runs after the lookup.
    const workflowTriggersStorageTable = new dynamodb.Table(scope, "WorkflowTriggersStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "workflowDatabaseId:workflowId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: { name: "triggerType", type: dynamodb.AttributeType.STRING },
    });
    // Partitioned on triggerBaseType — the BARE type, carried separately from the sort key. A lookup by
    // type is an exact match, so a suffixed sort-key value ("fileUpload#7f3a91") would land in its own
    // partition and that trigger would never fire.
    //
    // Named ByBaseType rather than keying on the sort key because DynamoDB cannot change an existing
    // index's key schema: CloudFormation rejects it outright with "Cannot update GSI's properties other
    // than Provisioned Throughput ... You can create a new GSI with a different name."
    workflowTriggersStorageTable.addGlobalSecondaryIndex({
        indexName: "TriggersByBaseTypeGSI",
        partitionKey: { name: "triggerBaseType", type: dynamodb.AttributeType.STRING },
        sortKey: {
            name: "workflowDatabaseId:workflowId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
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

    // ----------------------------------------------------------------------
    // Workflow Execution Storage Overhaul (V2 data model).
    // Executions are workflow-keyed; asset/database linkage lives in the
    // workflow/pipeline input tables. The legacy WorkflowExecutionsStorageTable
    // above is retained intact as the migration source.
    // ----------------------------------------------------------------------

    const workflowExecutionsStorageTableV2 = new dynamodb.Table(
        scope,
        "WorkflowExecutionsStorageTableV2",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "workflowDatabaseId:workflowId",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );
    workflowExecutionsStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowExecutionsByWorkflowGSI",
        partitionKey: {
            name: "workflowDatabaseId:workflowId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: { name: "executionStartDate", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    // Sparse GSI over executionGroupId (present only on grouped executions), so abort-by-group can
    // enumerate every active execution in a group without scanning the table.
    workflowExecutionsStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowExecutionsByGroupGSI",
        partitionKey: { name: "executionGroupId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "executionStartDate", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    // Global newest-first listing GSI. Every main row carries a constant partition attribute
    // (allListPartition = "execution") with executionStartDate as the sort key, so the global
    // executions list is a single QUERY (ScanIndexForward=false) bounded by a filterStartDate
    // key-condition
    workflowExecutionsStorageTableV2.addGlobalSecondaryIndex({
        indexName: "WorkflowExecutionsByDateGSI",
        partitionKey: { name: "allListPartition", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "executionStartDate", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const pipelineExecutionsStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
        }
    );
    pipelineExecutionsStorageTable.addGlobalSecondaryIndex({
        indexName: "PipelineExecByWorkflowExecGSI",
        partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "pipelineDatabaseId:pipelineId", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    pipelineExecutionsStorageTable.addGlobalSecondaryIndex({
        indexName: "PipelineExecChainGSI",
        partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "from_pipeline_execution_id", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    pipelineExecutionsStorageTable.addGlobalSecondaryIndex({
        indexName: "PipelineExecEndStateGSI",
        partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "endStatePipeline", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const pipelineExecutionInputFilesStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionInputFilesStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "databaseId:assetId:inputAssetFileKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );
    pipelineExecutionInputFilesStorageTable.addGlobalSecondaryIndex({
        indexName: "InputFilesByAssetGSI",
        partitionKey: { name: "databaseId:assetId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const pipelineExecutionInputMetadataStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionInputMetadataStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "databaseId:assetId:filePath",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const pipelineExecutionInputConfigurationStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionInputConfigurationStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "recordType", type: dynamodb.AttributeType.STRING },
        }
    );

    const pipelineExecutionOutputFilesStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionOutputFilesStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "fileType:relativeFilePath",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const pipelineExecutionOutputMetadataStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionOutputMetadataStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "targetFilePath:metadataKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const pipelineExecutionOutputResultsStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionOutputResultsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "relativeFilePath", type: dynamodb.AttributeType.STRING },
        }
    );

    const pipelineExecutionLogsStorageTable = new dynamodb.Table(
        scope,
        "PipelineExecutionLogsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "pipelineExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "logType", type: dynamodb.AttributeType.STRING },
        }
    );

    const workflowExecutionInputsStorageTable = new dynamodb.Table(
        scope,
        "WorkflowExecutionInputsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: {
                name: "databaseId:assetId:inputAssetFileKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );
    workflowExecutionInputsStorageTable.addGlobalSecondaryIndex({
        indexName: "WorkflowExecInputsByAssetGSI",
        partitionKey: { name: "databaseId:assetId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "executionStartDate", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const workflowExecutionConfigurationStorageTable = new dynamodb.Table(
        scope,
        "WorkflowExecutionConfigurationStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: { name: "workflowExecutionId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "recordType", type: dynamodb.AttributeType.STRING },
        }
    );

    // Executions by OUTPUT asset. An asset's execution history is the union of runs that read it and
    // runs that wrote to it, and the latter cannot be found any other way: a results-only or
    // generate-from-nothing pipeline (inputFileArity 'none') has no input rows at all, so its output
    // target is its only asset association. The partition attribute is written only on configuration
    // rows whose outputLocationType is 'asset', which keeps the index off results-only executions.
    workflowExecutionConfigurationStorageTable.addGlobalSecondaryIndex({
        indexName: "WorkflowExecConfigByOutputAssetGSI",
        partitionKey: {
            name: "outputDatabaseId:outputAssetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: { name: "executionStartDate", type: dynamodb.AttributeType.STRING },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    //old
    const metadataStorageTableLegacy = new dynamodb.Table(scope, "MetadataStorageTable", {
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

    const databaseMetadataStorageTable = new dynamodb.Table(
        scope,
        "DatabaseMetadataStorageTableV2",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "metadataKey",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING,
            },
            stream: dynamodb.StreamViewType.NEW_IMAGE,
        }
    );

    // GSI for querying by database
    databaseMetadataStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdIndex",
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataKey",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const assetFileMetadataStorageTable = new dynamodb.Table(
        scope,
        "AssetFileMetadataStorageTableV2",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "metadataKey",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "databaseId:assetId:filePath",
                type: dynamodb.AttributeType.STRING,
            },
            stream: dynamodb.StreamViewType.NEW_IMAGE,
        }
    );

    // GSI for querying by database/asset/file
    assetFileMetadataStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdAssetIdFilePathIndex",
        partitionKey: {
            name: "databaseId:assetId:filePath",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataKey",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying all metadata across an asset (without file path)
    assetFileMetadataStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdAssetIdIndex",
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataKey",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // Asset File Version History — one record per (file, S3 version) change event.
    // Captures change provenance (who/what/how) stamped as vams-change* S3
    // metadata and ingested by sqsBucketSync. PK is the composite
    // databaseId:assetId:filePath (matching the AssetFileMetadata convention);
    // SK is the S3 VersionId ("null" for non-versioned buckets). The
    // DatabaseIdAssetIdIndex GSI supports "all history for an asset" lookups.
    const assetFileVersionHistoryStorageTable = new dynamodb.Table(
        scope,
        "AssetFileVersionHistoryStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "databaseId:assetId:filePath",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "versionId",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    // GSI for querying all version-history records across an asset.
    assetFileVersionHistoryStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdAssetIdIndex",
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "versionId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // Sparse GSI for resolving which asset file versions a workflow execution produced.
    // Only records carrying changeWorkflowExecutionId (workflow-produced versions) are
    // indexed; direct uploads and other change sources are absent from this index.
    assetFileVersionHistoryStorageTable.addGlobalSecondaryIndex({
        indexName: "WorkflowExecutionIdIndex",
        partitionKey: {
            name: "changeWorkflowExecutionId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "databaseId:assetId:filePath",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // Asset History — one record per asset lifecycle operation (create, edit,
    // archive, unarchive, permanent delete). PK is the composite
    // databaseId:assetId; SK is the timestamp-prefixed historyRecordId, queried
    // with ScanIndexForward=false for newest-first. Records are permanent and
    // survive asset permanent deletes.
    const assetHistoryStorageTable = new dynamodb.Table(scope, "AssetHistoryStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "historyRecordId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    // Sync Tracking Outbound — one record per outbound synchronization of a
    // VAMS object (database, asset, assetFile) to an external system (e.g.
    // Physna, Garnet Framework). PK is the hierarchical object identifier
    // (databaseId | databaseId:assetId | databaseId:assetId:/filePath); SK is
    // the timestamp-prefixed syncRecordId, queried with ScanIndexForward=false
    // for newest-first. Append-only; no stream (the Garnet indexers route
    // stream events by table-name substring and must never see this table).
    const syncTrackingOutboundStorageTable = new dynamodb.Table(
        scope,
        "SyncTrackingOutboundStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "objectId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "syncRecordId",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    // GSIs for narrowing sync records by database, database+system, and system.
    syncTrackingOutboundStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdIndex",
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "syncRecordId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    syncTrackingOutboundStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseSystemIndex",
        partitionKey: {
            name: "databaseId:systemType:systemUniqueId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "syncRecordId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });
    syncTrackingOutboundStorageTable.addGlobalSecondaryIndex({
        indexName: "SystemIndex",
        partitionKey: {
            name: "systemType:systemUniqueId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "syncRecordId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const fileAttributeStorageTable = new dynamodb.Table(scope, "FileAttributeStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "attributeKey",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "databaseId:assetId:filePath",
            type: dynamodb.AttributeType.STRING,
        },
        stream: dynamodb.StreamViewType.NEW_IMAGE,
    });

    // GSI for querying by database/asset/file
    fileAttributeStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdAssetIdFilePathIndex",
        partitionKey: {
            name: "databaseId:assetId:filePath",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "attributeKey",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying all attributes across an asset (without file path)
    fileAttributeStorageTable.addGlobalSecondaryIndex({
        indexName: "DatabaseIdAssetIdIndex",
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "attributeKey",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    //Old
    const metadataSchemaStorageTableLegacy = new dynamodb.Table(
        scope,
        "MetadataSchemaStorageTable",
        {
            ...dynamodbDefaultProps,

            partitionKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "field",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    const metadataSchemaStorageTableV2 = new dynamodb.Table(scope, "MetadataSchemaStorageTableV2", {
        ...dynamodbDefaultProps,

        partitionKey: {
            name: "metadataSchemaId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "databaseId:metadataEntityType",
            type: dynamodb.AttributeType.STRING,
        },
    });

    // GSI for querying by database/metadataEntityType
    metadataSchemaStorageTableV2.addGlobalSecondaryIndex({
        indexName: "DatabaseIdMetadataEntityTypeIndex",
        partitionKey: {
            name: "databaseId:metadataEntityType",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataSchemaId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying by metadataEntityType
    metadataSchemaStorageTableV2.addGlobalSecondaryIndex({
        indexName: "MetadataEntityTypeIndex",
        partitionKey: {
            name: "metadataEntityType",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataSchemaId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying by database
    metadataSchemaStorageTableV2.addGlobalSecondaryIndex({
        indexName: "DatabaseIdIndex",
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "metadataSchemaId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
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

    // Per-database namespaced tag tables (PK=databaseId, "GLOBAL" for global tags).
    const tagStorageTableV2 = new dynamodb.Table(scope, "TagStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "tagName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    // GSI for cross-database lookups by tag name
    tagStorageTableV2.addGlobalSecondaryIndex({
        indexName: "tagNameIndex",
        partitionKey: {
            name: "tagName",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const tagTypeStorageTableV2 = new dynamodb.Table(scope, "TagTypeStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "tagTypeName",
            type: dynamodb.AttributeType.STRING,
        },
    });

    // GSI for cross-database lookups by tag type name
    tagTypeStorageTableV2.addGlobalSecondaryIndex({
        indexName: "tagTypeNameIndex",
        partitionKey: {
            name: "tagTypeName",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
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

    // New optimized constraints table with GSIs for efficient querying
    const constraintsStorageTable = new dynamodb.Table(scope, "ConstraintsStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "constraintId",
            type: dynamodb.AttributeType.STRING,
        },
        // No sort key - simple primary key for direct constraint lookups
    });

    // GSI for querying constraints by groupId (role-based permissions)
    constraintsStorageTable.addGlobalSecondaryIndex({
        indexName: "GroupPermissionsIndex",
        partitionKey: {
            name: "groupId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "objectType",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying constraints by userId (user-specific permissions)
    constraintsStorageTable.addGlobalSecondaryIndex({
        indexName: "UserPermissionsIndex",
        partitionKey: {
            name: "userId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "objectType",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI for querying constraints by objectType (admin/management queries)
    constraintsStorageTable.addGlobalSecondaryIndex({
        indexName: "ObjectTypeIndex",
        partitionKey: {
            name: "objectType",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "constraintId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const assetLinksStorageTableV2 = new dynamodb.Table(scope, "AssetLinksStorageTableV2.2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "assetLinkId",
            type: dynamodb.AttributeType.STRING,
        },
        stream: dynamodb.StreamViewType.NEW_IMAGE,
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
            stream: dynamodb.StreamViewType.NEW_IMAGE,
        }
    );

    // Legacy tables (V1) — kept for data migration, not used by Lambda handlers
    const assetFileVersionsStorageTableV1 = new dynamodb.Table(
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

    const assetVersionsStorageTableV1 = new dynamodb.Table(scope, "AssetVersionsStorageTable", {
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

    // V2 tables — new key schemas with databaseId scoping
    const assetVersionsStorageTable = new dynamodb.Table(scope, "AssetVersionsStorageTableV2", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "assetVersionId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    const assetFileVersionsStorageTable = new dynamodb.Table(
        scope,
        "AssetFileVersionsStorageTableV2",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "databaseId:assetId:assetVersionId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "fileKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    // GSI for querying all file versions across asset versions within a database+asset
    assetFileVersionsStorageTable.addGlobalSecondaryIndex({
        indexName: "databaseIdAssetIdIndex",
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    const assetFileMetadataVersionsStorageTable = new dynamodb.Table(
        scope,
        "AssetFileMetadataVersionsStorageTable",
        {
            ...dynamodbDefaultProps,
            partitionKey: {
                name: "databaseId:assetId:assetVersionId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "type:filePath:metadataKey",
                type: dynamodb.AttributeType.STRING,
            },
        }
    );

    // GSI for querying all metadata versions across asset versions within a database+asset
    assetFileMetadataVersionsStorageTable.addGlobalSecondaryIndex({
        indexName: "databaseIdAssetIdIndex",
        partitionKey: {
            name: "databaseId:assetId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
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

    const apiKeyStorageTable = new dynamodb.Table(scope, "ApiKeyStorageTable", {
        ...dynamodbDefaultProps,
        partitionKey: {
            name: "apiKeyId",
            type: dynamodb.AttributeType.STRING,
        },
    });

    apiKeyStorageTable.addGlobalSecondaryIndex({
        indexName: "apiKeyHashIndex",
        partitionKey: {
            name: "apiKeyHash",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
    });

    apiKeyStorageTable.addGlobalSecondaryIndex({
        indexName: "userIdIndex",
        partitionKey: {
            name: "userId",
            type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
            name: "apiKeyId",
            type: dynamodb.AttributeType.STRING,
        },
        projectionType: dynamodb.ProjectionType.ALL,
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

    //Build storage resources object
    const storageResources = {
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
            fileIndexerSnsTopic: FileIndexerSnsTopic,
            assetIndexerSnsTopic: AssetIndexerSnsTopic,
            databaseIndexerSnsTopic: DatabaseIndexerSnsTopic,
        },
        cloudWatchAuditLogGroups: auditLogGroups,
        eventBridge: {
            orchestrationBus: orchestrationBus,
            orchestrationBusAuditLogGroup: orchestrationBusAuditLogGroup,
            eventSourcePrefix: eventSourcePrefix,
        },
        dynamo: {
            appFeatureEnabledStorageTable: appFeatureEnabledStorageTable,
            assetLinksStorageTableV2: assetLinksStorageTableV2,
            assetLinksMetadataStorageTable: assetLinksMetadataStorageTable,
            assetStorageTable: assetStorageTable,
            assetUploadsStorageTable: assetUploadsStorageTable,
            assetFileVersionsStorageTable: assetFileVersionsStorageTable,
            assetFileMetadataVersionsStorageTable: assetFileMetadataVersionsStorageTable,
            assetVersionsStorageTable: assetVersionsStorageTable,
            commentStorageTable: commentStorageTable,
            constraintsStorageTable: constraintsStorageTable,
            pipelineStorageTable: pipelineStorageTable,
            databaseStorageTable: databaseStorageTable,
            workflowStorageTable: workflowStorageTable,
            workflowExecutionsStorageTable: workflowExecutionsStorageTable,
            workflowExecutionsStorageTableV2: workflowExecutionsStorageTableV2,
            pipelineExecutionsStorageTable: pipelineExecutionsStorageTable,
            pipelineExecutionInputFilesStorageTable: pipelineExecutionInputFilesStorageTable,
            pipelineExecutionInputMetadataStorageTable: pipelineExecutionInputMetadataStorageTable,
            pipelineExecutionInputConfigurationStorageTable:
                pipelineExecutionInputConfigurationStorageTable,
            pipelineExecutionOutputFilesStorageTable: pipelineExecutionOutputFilesStorageTable,
            pipelineExecutionOutputMetadataStorageTable:
                pipelineExecutionOutputMetadataStorageTable,
            pipelineExecutionOutputResultsStorageTable: pipelineExecutionOutputResultsStorageTable,
            pipelineExecutionLogsStorageTable: pipelineExecutionLogsStorageTable,
            workflowExecutionInputsStorageTable: workflowExecutionInputsStorageTable,
            workflowExecutionConfigurationStorageTable: workflowExecutionConfigurationStorageTable,
            metadataSchemaStorageTableV2: metadataSchemaStorageTableV2,
            databaseMetadataStorageTable: databaseMetadataStorageTable,
            assetFileMetadataStorageTable: assetFileMetadataStorageTable,
            assetFileVersionHistoryStorageTable: assetFileVersionHistoryStorageTable,
            assetHistoryStorageTable: assetHistoryStorageTable,
            syncTrackingOutboundStorageTable: syncTrackingOutboundStorageTable,
            fileAttributeStorageTable: fileAttributeStorageTable,
            authEntitiesStorageTable: authEntitiesTable,
            tagStorageTable: tagStorageTableV2,
            tagTypeStorageTable: tagTypeStorageTableV2,
            tagStorageTableLegacy: tagStorageTable,
            tagTypeStorageTableLegacy: tagTypeStorageTable,
            s3AssetBucketsStorageTable: s3AssetBucketsStorageTable,
            subscriptionsStorageTable: subscriptionsStorageTable,
            rolesStorageTable: rolesStorageTable,
            userRolesStorageTable: userRolesStorageTable,
            userStorageTable: userStorageTable,
            apiKeyStorageTable: apiKeyStorageTable,
            // Pipeline + workflow V2 data model tables
            pipelineStorageTableV2: pipelineStorageTableV2,
            pipelineTemplatesStorageTable: pipelineTemplatesStorageTable,
            pipelineTemplateTagSchemaStorageTable: pipelineTemplateTagSchemaStorageTable,
            workflowStorageTableV2: workflowStorageTableV2,
            workflowTriggersStorageTable: workflowTriggersStorageTable,
        },
    };

    /////////////////////////////////////////////////////////////////////////////
    // Create SNS Queuing Lambdas
    /////////////////////////////////////////////////////////////////////////////

    // Create file indexer SNS queuing Lambda
    const fileIndexerSnsQueuingFunction = buildFileIndexerSnsQueuingFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    // Setup event source mapping for file indexer SNS queuing with GovCloud support
    if (config.app.govCloud.enabled) {
        // Subscribe assetFileMetadataStorageTable to fileIndexerSns
        const esmFileIndexerAssetFileMetadata = new lambda.EventSourceMapping(
            scope,
            "FileIndexerSnsQueuingAssetFileMetadataStream",
            {
                target: fileIndexerSnsQueuingFunction,
                eventSourceArn: assetFileMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmFileIndexerAssetFileMetadata = esmFileIndexerAssetFileMetadata.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmFileIndexerAssetFileMetadata.addPropertyDeletionOverride("Tags");

        // Subscribe fileAttributeStorageTable to fileIndexerSns
        const esmFileIndexerFileAttribute = new lambda.EventSourceMapping(
            scope,
            "FileIndexerSnsQueuingFileAttributeStream",
            {
                target: fileIndexerSnsQueuingFunction,
                eventSourceArn: fileAttributeStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmFileIndexerFileAttribute = esmFileIndexerFileAttribute.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmFileIndexerFileAttribute.addPropertyDeletionOverride("Tags");
    } else {
        // Subscribe assetFileMetadataStorageTable to fileIndexerSns
        const esmFileIndexerAssetFileMetadataNonGov = new lambda.EventSourceMapping(
            scope,
            "FileIndexerSnsQueuingAssetFileMetadataStream",
            {
                target: fileIndexerSnsQueuingFunction,
                eventSourceArn: assetFileMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );

        // Subscribe fileAttributeStorageTable to fileIndexerSns
        const esmFileIndexerFileAttributeNonGov = new lambda.EventSourceMapping(
            scope,
            "FileIndexerSnsQueuingFileAttributeStream",
            {
                target: fileIndexerSnsQueuingFunction,
                eventSourceArn: fileAttributeStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
    }

    // Create asset indexer SNS queuing Lambda
    const assetIndexerSnsQueuingFunction = buildAssetIndexerSnsQueuingFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    // Setup event source mappings for asset indexer SNS queuing with GovCloud support
    // Asset table stream
    if (config.app.govCloud.enabled) {
        const esmAssetIndexerAsset = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmAssetIndexerAsset = esmAssetIndexerAsset.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmAssetIndexerAsset.addPropertyDeletionOverride("Tags");
    } else {
        const esmAssetIndexerAssetNonGov = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
    }

    // Metadata table stream
    if (config.app.govCloud.enabled) {
        // Subscribe assetFileMetadataStorageTable to assetIndexerSns
        const esmAssetIndexerAssetFileMetadata = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetFileMetadataStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetFileMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmAssetIndexerAssetFileMetadata = esmAssetIndexerAssetFileMetadata.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmAssetIndexerAssetFileMetadata.addPropertyDeletionOverride("Tags");
    } else {
        // Subscribe assetFileMetadataStorageTable to assetIndexerSns
        const esmAssetIndexerAssetFileMetadataNonGov = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetFileMetadataStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetFileMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
    }

    // Asset links table stream
    if (config.app.govCloud.enabled) {
        const esmAssetIndexerLinks = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetLinksStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetLinksStorageTableV2.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmAssetIndexerLinks = esmAssetIndexerLinks.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmAssetIndexerLinks.addPropertyDeletionOverride("Tags");

        const esmAssetIndexerLinksMetadata = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetLinksMetadataStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetLinksMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmAssetIndexerLinksMetadata = esmAssetIndexerLinksMetadata.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmAssetIndexerLinksMetadata.addPropertyDeletionOverride("Tags");
    } else {
        const esmAssetIndexerLinksNonGov = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetLinksStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetLinksStorageTableV2.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );

        const esmAssetIndexerLinksMetadataNonGov = new lambda.EventSourceMapping(
            scope,
            "AssetIndexerSnsQueuingAssetLinksMetadataStream",
            {
                target: assetIndexerSnsQueuingFunction,
                eventSourceArn: assetLinksMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
    }

    // Create database indexer SNS queuing Lambda
    const databaseIndexerSnsQueuingFunction = buildDatabaseIndexerSnsQueuingFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    // Setup event source mapping for database indexer SNS queuing with GovCloud support
    //Database Table + Database Metadata Table
    if (config.app.govCloud.enabled) {
        const esmDatabaseTableIndexerSns = new lambda.EventSourceMapping(
            scope,
            "DatabaseTableIndexerSnsQueuingDatabaseStreamv2",
            {
                target: databaseIndexerSnsQueuingFunction,
                eventSourceArn: databaseStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmDatabaseTableIndexerSns = esmDatabaseTableIndexerSns.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmDatabaseTableIndexerSns.addPropertyDeletionOverride("Tags");

        const esmDatabaseMetadataIndexerSns = new lambda.EventSourceMapping(
            scope,
            "DatabaseMetadataIndexerSnsQueuingDatabaseStreamv2",
            {
                target: databaseIndexerSnsQueuingFunction,
                eventSourceArn: databaseMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
        const cfnEsmDatabaseMetadataIndexerSns = esmDatabaseMetadataIndexerSns.node
            .defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmDatabaseMetadataIndexerSns.addPropertyDeletionOverride("Tags");
    } else {
        const esmDatabaseTableIndexerSnsNonGov = new lambda.EventSourceMapping(
            scope,
            "DatabaseTableIndexerSnsQueuingDatabaseStreamv2",
            {
                target: databaseIndexerSnsQueuingFunction,
                eventSourceArn: databaseStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );

        const esmDatabaseMetadataIndexerSnsNonGov = new lambda.EventSourceMapping(
            scope,
            "DatabaseMetadataTableIndexerSnsQueuingDatabaseStreamv2",
            {
                target: databaseIndexerSnsQueuingFunction,
                eventSourceArn: databaseMetadataStorageTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 100,
            }
        );
    }

    /////////////////////////////////////////////////////////////////////////////
    // Setup S3 bucket sync and indexing (moved from searchBuilder)
    /////////////////////////////////////////////////////////////////////////////

    // Loop through each asset bucket and setup S3 event notifications sync
    let bucketSyncIndex = 0;
    const bucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    // A bucket instance can appear in multiple records (same bucket, different
    // prefixes). The sync-queue construct IDs are derived from the bucket instance,
    // so they would collide across those records. Keep the original construct ID for
    // the first registration of each bucket (preserves existing logical IDs / no diff
    // for single-registration deployments) and add a per-occurrence suffix for any
    // additional prefix registrations of the same bucket.
    const bucketSyncOccurrence = new Map<s3.IBucket, number>();
    for (const record of bucketRecords) {
        const bucketOccurrence = bucketSyncOccurrence.get(record.bucket) ?? 0;
        bucketSyncOccurrence.set(record.bucket, bucketOccurrence + 1);
        const bucketSyncIdSuffix = bucketOccurrence === 0 ? "" : `-${bucketOccurrence}`;

        // Create SQS queue for S3 object created events
        const onS3ObjectCreatedQueue = new sqs.Queue(
            scope,
            "bucketSyncCreated--" + record.bucket + bucketSyncIdSuffix,
            {
                queueName: `${config.name}-${config.app.baseStackName}-bucketSyncCreated--${bucketSyncIndex}`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's is 900
                encryption: kmsEncryptionKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: kmsEncryptionKey,
                enforceSSL: true,
            }
        );
        onS3ObjectCreatedQueue.grantSendMessages(Service("SNS").Principal);

        // Create Lambda for bucket sync (created events)
        const sqsBucketSyncFunctionCreated = buildSqsBucketSyncFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            record.bucket.bucketName,
            record.prefix,
            record.defaultSyncDatabaseId,
            "created",
            bucketSyncIndex,
            config,
            vpc,
            subnets
        );

        // Subscribe SQS queue to SNS topic
        if (record.snsS3ObjectCreatedTopic) {
            record.snsS3ObjectCreatedTopic.addSubscription(
                new SqsSubscription(onS3ObjectCreatedQueue)
            );
        }

        onS3ObjectCreatedQueue.grantConsumeMessages(sqsBucketSyncFunctionCreated);

        // Setup event source mapping with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmCreated = new lambda.EventSourceMapping(
                scope,
                `SQSEventSourceBucketSyncCreated-${bucketSyncIndex}`,
                {
                    eventSourceArn: onS3ObjectCreatedQueue.queueArn,
                    target: sqsBucketSyncFunctionCreated,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsm = esmCreated.node.defaultChild as lambda.CfnEventSourceMapping;
            cfnEsm.addPropertyDeletionOverride("Tags");
        } else {
            const esmCreatedNonGov = new lambda.EventSourceMapping(
                scope,
                `SQSEventSourceBucketSyncCreated-${bucketSyncIndex}`,
                {
                    eventSourceArn: onS3ObjectCreatedQueue.queueArn,
                    target: sqsBucketSyncFunctionCreated,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
        }

        bucketSyncIndex = bucketSyncIndex + 1;

        // Create SQS queue for S3 object deleted events
        const onS3ObjectDeletedQueue = new sqs.Queue(
            scope,
            "bucketSyncDeleted--" + record.bucket + bucketSyncIdSuffix,
            {
                queueName: `${config.name}-${config.app.baseStackName}-bucketSyncDeleted--${bucketSyncIndex}`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's is 900
                encryption: kmsEncryptionKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: kmsEncryptionKey,
                enforceSSL: true,
            }
        );
        onS3ObjectDeletedQueue.grantSendMessages(Service("SNS").Principal);

        // Create Lambda for bucket sync (deleted events)
        const sqsBucketSyncFunctionRemoved = buildSqsBucketSyncFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            record.bucket.bucketName,
            record.prefix,
            record.defaultSyncDatabaseId,
            "deleted",
            bucketSyncIndex,
            config,
            vpc,
            subnets
        );

        // Subscribe SQS queue to SNS topic
        if (record.snsS3ObjectDeletedTopic) {
            record.snsS3ObjectDeletedTopic.addSubscription(
                new SqsSubscription(onS3ObjectDeletedQueue)
            );
        }

        onS3ObjectDeletedQueue.grantConsumeMessages(sqsBucketSyncFunctionRemoved);

        // Setup event source mapping with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmDeleted = new lambda.EventSourceMapping(
                scope,
                `SQSEventSourceBucketSyncDeleted-${bucketSyncIndex}`,
                {
                    eventSourceArn: onS3ObjectDeletedQueue.queueArn,
                    target: sqsBucketSyncFunctionRemoved,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsm = esmDeleted.node.defaultChild as lambda.CfnEventSourceMapping;
            cfnEsm.addPropertyDeletionOverride("Tags");
        } else {
            const esmDeletedNonGov = new lambda.EventSourceMapping(
                scope,
                `SQSEventSourceBucketSyncDeleted-${bucketSyncIndex}`,
                {
                    eventSourceArn: onS3ObjectDeletedQueue.queueArn,
                    target: sqsBucketSyncFunctionRemoved,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
        }

        bucketSyncIndex = bucketSyncIndex + 1;
    }

    /// Register fixed resource names for SSM publication by the ResourceNames builder stack

    const resourceNameParamValues: { [paramKey: string]: string } = {
        [RESOURCE_PARAM_KEYS.dynamoTables.appFeatureEnabledStorage]:
            storageResources.dynamo.appFeatureEnabledStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetLinksStorageV2]:
            storageResources.dynamo.assetLinksStorageTableV2.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetLinksMetadataStorage]:
            storageResources.dynamo.assetLinksMetadataStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetStorage]:
            storageResources.dynamo.assetStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetUploadsStorage]:
            storageResources.dynamo.assetUploadsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetVersionsStorage]:
            storageResources.dynamo.assetVersionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetFileVersionsStorage]:
            storageResources.dynamo.assetFileVersionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetFileVersionHistoryStorage]:
            storageResources.dynamo.assetFileVersionHistoryStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetHistoryStorage]:
            storageResources.dynamo.assetHistoryStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.syncTrackingOutboundStorage]:
            storageResources.dynamo.syncTrackingOutboundStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetFileMetadataVersionsStorage]:
            storageResources.dynamo.assetFileMetadataVersionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.assetFileMetadataStorage]:
            storageResources.dynamo.assetFileMetadataStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.authEntitiesStorage]:
            storageResources.dynamo.authEntitiesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.commentStorage]:
            storageResources.dynamo.commentStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.constraintsStorage]:
            storageResources.dynamo.constraintsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.databaseStorage]:
            storageResources.dynamo.databaseStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.metadataSchemaStorageV2]:
            storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.databaseMetadataStorage]:
            storageResources.dynamo.databaseMetadataStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.fileAttributeStorage]:
            storageResources.dynamo.fileAttributeStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineStorage]:
            storageResources.dynamo.pipelineStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.rolesStorage]:
            storageResources.dynamo.rolesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.s3AssetBucketsStorage]:
            storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.subscriptionsStorage]:
            storageResources.dynamo.subscriptionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.tagStorage]:
            storageResources.dynamo.tagStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.tagTypeStorage]:
            storageResources.dynamo.tagTypeStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.userRolesStorage]:
            storageResources.dynamo.userRolesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.userStorage]:
            storageResources.dynamo.userStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionsStorage]:
            storageResources.dynamo.workflowExecutionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.apiKeyStorage]:
            storageResources.dynamo.apiKeyStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowStorage]:
            storageResources.dynamo.workflowStorageTable.tableName,
        // Workflow-execution V2 data model tables
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionsStorageV2]:
            storageResources.dynamo.workflowExecutionsStorageTableV2.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionInputsStorage]:
            storageResources.dynamo.workflowExecutionInputsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionConfigurationStorage]:
            storageResources.dynamo.workflowExecutionConfigurationStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionsStorage]:
            storageResources.dynamo.pipelineExecutionsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionInputFilesStorage]:
            storageResources.dynamo.pipelineExecutionInputFilesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionInputMetadataStorage]:
            storageResources.dynamo.pipelineExecutionInputMetadataStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionInputConfigurationStorage]:
            storageResources.dynamo.pipelineExecutionInputConfigurationStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionOutputFilesStorage]:
            storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionOutputMetadataStorage]:
            storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionOutputResultsStorage]:
            storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineExecutionLogsStorage]:
            storageResources.dynamo.pipelineExecutionLogsStorageTable.tableName,
        // Pipeline + workflow V2 data model tables
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineStorageV2]:
            storageResources.dynamo.pipelineStorageTableV2.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineTemplatesStorage]:
            storageResources.dynamo.pipelineTemplatesStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.pipelineTemplateTagSchemaStorage]:
            storageResources.dynamo.pipelineTemplateTagSchemaStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowStorageV2]:
            storageResources.dynamo.workflowStorageTableV2.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTables.workflowTriggersStorage]:
            storageResources.dynamo.workflowTriggersStorageTable.tableName,
        [RESOURCE_PARAM_KEYS.s3Buckets.assetAuxiliary]:
            storageResources.s3.assetAuxiliaryBucket.bucketName,
        [RESOURCE_PARAM_KEYS.s3Buckets.artefacts]: storageResources.s3.artefactsBucket.bucketName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditAuthentication]:
            storageResources.cloudWatchAuditLogGroups.authentication.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditAuthorization]:
            storageResources.cloudWatchAuditLogGroups.authorization.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditFileUpload]:
            storageResources.cloudWatchAuditLogGroups.fileUpload.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditFileDownload]:
            storageResources.cloudWatchAuditLogGroups.fileDownload.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditFileDownloadStreamed]:
            storageResources.cloudWatchAuditLogGroups.fileDownloadStreamed.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditAuthOther]:
            storageResources.cloudWatchAuditLogGroups.authOther.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditAuthChanges]:
            storageResources.cloudWatchAuditLogGroups.authChanges.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditActions]:
            storageResources.cloudWatchAuditLogGroups.actions.logGroupName,
        [RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditErrors]:
            storageResources.cloudWatchAuditLogGroups.errors.logGroupName,
        // Deprecated tables — published for data-migration tooling only
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.assetVersionsStorageV1]:
            assetVersionsStorageTableV1.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.assetFileVersionsStorageV1]:
            assetFileVersionsStorageTableV1.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.assetLinksStorage]:
            assetLinksStorageTableDeprecated.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.metadataStorage]:
            metadataStorageTableLegacy.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.metadataSchemaStorage]:
            metadataSchemaStorageTableLegacy.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.tagStorage]:
            storageResources.dynamo.tagStorageTableLegacy.tableName,
        [RESOURCE_PARAM_KEYS.dynamoTablesLegacy.tagTypeStorage]:
            storageResources.dynamo.tagTypeStorageTableLegacy.tableName,
    };
    Object.entries(resourceNameParamValues).forEach(([paramKey, value]) => {
        resourceNameRegistry.register({ paramKey, value });
    });

    // Add Nag suppressions for SQS queues
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-SQS3",
                reason: "Intended not to use DLQs for these types of SQS events. Files easily redriven based on the logic of assets.",
            },
        ],
        true
    );

    //Return final storage resource object
    return storageResources;
}
