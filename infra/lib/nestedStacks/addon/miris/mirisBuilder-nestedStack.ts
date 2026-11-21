/*
 * Miris Spatial Streaming add-on nested stack.
 *
 * Serves the Miris stream status and viewer key routes the streaming viewer calls, seeds the GLOBAL
 * "Miris stream" file metadata schema, and in perView mode records each minted viewer key so that
 * an expired key is deleted from the Miris workspace.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { SqsDlq } from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../../../common/addonResourceParamKeys";
import { Service } from "../../../helper/service-helper";
import {
    NAG_REASON_LAMBDA_BASIC_EXECUTION,
    kmsKeyPolicyStatementGenerator,
} from "../../../helper/security";
import { RouteRegistry, attachFunctionToApi } from "../../apiLambda/apiRouteRegistry";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { addonDynamoDbTableDefaults, publishAddonResourceName } from "../addonStackHelpers";
import { populateMirisViewerKeySecret } from "./customResources/populateMirisViewerKeySecret";
import { DynamoDbMetadataSchemaMirisConstruct } from "./constructs/dynamodb-metadataschema-miris-construct";
import {
    buildGetMirisAssetStatusFunction,
    buildMintMirisViewerKeyFunction,
    buildMirisViewerKeyCleanupFunction,
    mirisMetricsNamespace,
} from "./lambdaBuilder/mirisFunctions";

export interface MirisBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    isolatedSubnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    registry: RouteRegistry;
}

const defaultProps: Partial<MirisBuilderNestedStackProps> = {};

export class MirisBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: MirisBuilderNestedStackProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };

        const perView = props.config.app.addons.useMirisStreaming.viewerKeyMode === "perView";

        // The shared VAMS CMK, imported by ARN. The grants below on the CMK-encrypted table and
        // queue then write only to the grantee's own policy; a grant on the Storage stack's key
        // object can instead add the grantee to that key's resource policy, which makes the Storage
        // stack consume this stack's output while this stack consumes Storage's, a CloudFormation
        // circular dependency.
        const sharedKmsKey = props.storageResources.encryption.kmsKey;
        const kmsKey = sharedKmsKey
            ? kms.Key.fromKeyArn(this, "MirisKmsKeyRef", sharedKmsKey.keyArn)
            : undefined;

        // Static mode: the deployment viewer key, in a secret created empty and filled at deploy
        // time by a custom resource that carries the key in its code asset, so the key is in
        // neither the template nor a Lambda environment.
        let viewerKeySecret: secretsmanager.Secret | undefined;
        if (!perView) {
            viewerKeySecret = new secretsmanager.Secret(this, "MirisViewerKeySecret", {
                description: "Miris viewer key returned by the VAMS Miris add-on in static mode.",
                encryptionKey: kmsKey,
            });
            populateMirisViewerKeySecret(
                this,
                "MirisViewerKeySecretPopulate",
                viewerKeySecret,
                props.config.app.addons.useMirisStreaming.viewerKey
            );
            NagSuppressions.addResourceSuppressions(viewerKeySecret, [
                {
                    id: "AwsSolutions-SMG4",
                    reason: "The Miris viewer key is issued and rotated in Miris, not by AWS; the operator rotates it with miris viewerkey rotate and redeploys with the new value.",
                },
            ]);
        }

        // Per-view viewer key IDs. TTL deletions stream to the cleanup Lambda, which deletes the
        // key from the Miris workspace.
        let viewerKeyTable: dynamodb.Table | undefined;
        if (perView) {
            viewerKeyTable = new dynamodb.Table(this, "MirisViewerKeyStorageTable", {
                partitionKey: {
                    name: "mirisViewerKeyId",
                    type: dynamodb.AttributeType.STRING,
                },
                timeToLiveAttribute: "ttl",
                stream: dynamodb.StreamViewType.KEYS_ONLY,
                // RETAIN and auto-named, as every VAMS table, so a retained orphan never collides
                // with the table a redeploy creates.
                ...addonDynamoDbTableDefaults(kmsKey),
            });

            publishAddonResourceName(
                this,
                props.config,
                ADDON_RESOURCE_PARAM_KEYS.dynamoTables.mirisViewerKeyStorage,
                viewerKeyTable.tableName
            );
        }

        // Miris refused the Integration Key after a re-read. No action is attached; an operator
        // subscribes a notification to it.
        const alarmPrefix = `${props.config.name}-${props.config.app.baseStackName}`;
        new cloudwatch.Alarm(this, "MirisAuthFailureAlarm", {
            alarmName: `${alarmPrefix}-mirisAuthFailures`,
            alarmDescription:
                "Miris refused the add-on's Integration Key (HTTP 401 or 403), so stream status " +
                "checks, viewer keys and viewer key cleanup fail. Check the key in the secret named by " +
                "apiKeySecretArn and the Miris account that owns it.",
            metric: new cloudwatch.Metric({
                namespace: mirisMetricsNamespace(props.config),
                metricName: "MirisAuthFailures",
                period: cdk.Duration.minutes(5),
                statistic: cloudwatch.Stats.SUM,
            }),
            threshold: 0,
            comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluationPeriods: 1,
            treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        });

        // Miris stream status for a file, read from that file's metadata (streaming viewer)
        const getAssetStatusFunction = buildGetMirisAssetStatusFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets,
            viewerKeySecret
        );
        attachFunctionToApi(this, getAssetStatusFunction, {
            routePath: "/addon/miris/asset-status",
            method: apigwv2.HttpMethod.GET,
            registry: props.registry,
        });

        // Viewer key for a file: the deployment key (static) or one minted per view (perView)
        const mintViewerKeyFunction = buildMintMirisViewerKeyFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets,
            viewerKeyTable,
            viewerKeySecret
        );
        attachFunctionToApi(this, mintViewerKeyFunction, {
            routePath: "/addon/miris/viewer-key",
            method: apigwv2.HttpMethod.POST,
            registry: props.registry,
        });

        // Per-view viewer keys: delete each key from Miris when TTL removes its table row
        if (viewerKeyTable) {
            const viewerKeyCleanupFunction = buildMirisViewerKeyCleanupFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources,
                props.config,
                props.vpc,
                props.isolatedSubnets,
                viewerKeyTable
            );

            const viewerKeyCleanupDlq = new sqs.Queue(this, "MirisViewerKeyCleanupDLQ", {
                retentionPeriod: cdk.Duration.days(14),
                encryption: kmsKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: kmsKey,
                enforceSSL: true,
            });

            // Only TTL deletions: REMOVE records written by the DynamoDB service itself. A batch
            // that still fails after its retries is recorded in the dead-letter queue.
            new lambda.EventSourceMapping(this, "MirisViewerKeyCleanupStream", {
                target: viewerKeyCleanupFunction,
                eventSourceArn: viewerKeyTable.tableStreamArn,
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                batchSize: 5,
                reportBatchItemFailures: true,
                bisectBatchOnError: true,
                retryAttempts: 10,
                onFailure: new SqsDlq(viewerKeyCleanupDlq),
                filters: [
                    lambda.FilterCriteria.filter({
                        eventName: lambda.FilterRule.isEqual("REMOVE"),
                        userIdentity: {
                            type: lambda.FilterRule.isEqual("Service"),
                            principalId: lambda.FilterRule.isEqual("dynamodb.amazonaws.com"),
                        },
                    }),
                ],
            });

            new cloudwatch.Alarm(this, "MirisViewerKeyCleanupDLQAlarm", {
                alarmName: `${alarmPrefix}-mirisViewerKeyCleanupDLQ`,
                alarmDescription:
                    "A batch of expired per-view viewer keys the cleanup function could not delete " +
                    "from Miris reached this dead-letter queue, which holds only the stream's shard " +
                    "and sequence range. The key IDs are in the cleanup function's log (\"Could not " +
                    'delete Miris viewer key"). The keys have already expired, so deleting them in ' +
                    "Miris after fixing the cause is housekeeping.",
                metric: viewerKeyCleanupDlq.metricApproximateNumberOfMessagesVisible({
                    period: cdk.Duration.minutes(5),
                    statistic: cloudwatch.Stats.MAXIMUM,
                }),
                threshold: 0,
                comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            });

            NagSuppressions.addResourceSuppressions(
                viewerKeyCleanupDlq,
                [
                    {
                        id: "AwsSolutions-SQS3",
                        reason: "This queue is the on-failure destination of the Miris viewer key cleanup stream mapping and so terminates that chain; a redrive policy of its own would only move the same failed batch record to a further queue.",
                    },
                ],
                true
            );
        }

        // GLOBAL "Miris stream" file metadata schema, written by a custom resource on a role that
        // can only put items into the metadata schema table.
        const schemaCustomResourceRole = new iam.Role(this, "MirisStreamSchemaCustomResourceRole", {
            assumedBy: Service("LAMBDA").Principal,
            inlinePolicies: {
                TablePolicy: new iam.PolicyDocument({
                    statements: [
                        new iam.PolicyStatement({
                            effect: iam.Effect.ALLOW,
                            actions: ["dynamodb:PutItem"],
                            resources: [
                                props.storageResources.dynamo.metadataSchemaStorageTableV2.tableArn,
                            ],
                        }),
                    ],
                }),
                ...(kmsKey
                    ? {
                          KmsPolicy: new iam.PolicyDocument({
                              statements: [kmsKeyPolicyStatementGenerator(kmsKey)],
                          }),
                      }
                    : {}),
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        });
        new DynamoDbMetadataSchemaMirisConstruct(this, "MetadataSchemaMiris", {
            customResourceRole: schemaCustomResourceRole,
            storageResources: props.storageResources,
        });

        NagSuppressions.addResourceSuppressions(
            schemaCustomResourceRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
                    appliesTo: [{ regex: "/.*AWSLambdaBasicExecutionRole$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Allow permissions for KMS unencryption/re-encryption for keys generated within VAMS. Policy statements additions on imported keys are No-Op statements and must be set externally to the deployment.",
                    appliesTo: [{ regex: "/^Action::kms:(.*)\\*$/g" }],
                },
            ],
            true
        );
    }
}
