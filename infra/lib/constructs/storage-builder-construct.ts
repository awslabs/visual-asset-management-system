/**
 * Copyright 2022 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   https://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import * as cdk from 'aws-cdk-lib'
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as iam from 'aws-cdk-lib/aws-iam';
import { Duration, NestedStack } from "aws-cdk-lib";
import { BlockPublicAccess } from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { requireTLSAddToResourcePolicy } from '../security';

export interface storageResources {
    s3: {
        assetBucket: s3.Bucket,
        artefactsBucket: s3.Bucket,
        accessLogsBucket: s3.Bucket,
        sagemakerBucket: s3.Bucket,
    }
    dynamo: {
        assetStorageTable: dynamodb.Table,
        jobStorageTable: dynamodb.Table,
        pipelineStorageTable: dynamodb.Table,
        databaseStorageTable: dynamodb.Table,
        workflowStorageTable: dynamodb.Table,
        workflowExecutionStorageTable: dynamodb.Table,
    }
}
export class nestedStorageResourcesBuilder extends NestedStack {
    s3: {
        assetBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
        sagemakerBucket: s3.Bucket;
    }
    dynamo: {
        assetStorageTable: dynamodb.Table;
        jobStorageTable: dynamodb.Table;
        pipelineStorageTable: dynamodb.Table;
        databaseStorageTable: dynamodb.Table;
        workflowStorageTable: dynamodb.Table;
        workflowExecutionStorageTable: dynamodb.Table;
    }

    constructor(scope: Construct, name: string) {
        super(scope, name)
        const accessLogsBucket = new s3.Bucket(scope, "AccessLogsBucket", {
            encryption: s3.BucketEncryption.KMS_MANAGED,
            serverAccessLogsPrefix: 'access-log-bucket-logs/',
            versioned: true,
            blockPublicAccess: BlockPublicAccess.BLOCK_ALL
        });

        accessLogsBucket.addLifecycleRule({
            enabled: true,
            expiration: Duration.days(3650),
        });

        requireTLSAddToResourcePolicy(accessLogsBucket);


        const assetBucket = new s3.Bucket(scope, "AssetBucket", {
            cors: [
                {
                    allowedOrigins: ["*"],
                    allowedHeaders: ['*'],
                    allowedMethods: [
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.POST,
                    ],
                    exposedHeaders: [
                        'ETag'
                    ]
                },
            ],
            versioned: true,
            encryption: s3.BucketEncryption.KMS_MANAGED,
            serverAccessLogsBucket: accessLogsBucket,
            serverAccessLogsPrefix: "asset-bucket-logs/",
            blockPublicAccess: BlockPublicAccess.BLOCK_ALL
        });
        requireTLSAddToResourcePolicy(assetBucket);

        const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
            versioned: false,
            encryption: s3.BucketEncryption.KMS_MANAGED,
            serverAccessLogsBucket: accessLogsBucket,
            serverAccessLogsPrefix: "artefacts-bucket-logs/",
            blockPublicAccess: BlockPublicAccess.BLOCK_ALL
        });
        requireTLSAddToResourcePolicy(artefactsBucket);

        const sagemakerBucket = new s3.Bucket(scope, "SagemakerBucket", {
            versioned: false,
            encryption: s3.BucketEncryption.KMS_MANAGED,
            serverAccessLogsBucket: accessLogsBucket,
            serverAccessLogsPrefix: "sagemaker-bucket-logs/",
            blockPublicAccess: BlockPublicAccess.BLOCK_ALL
        });
        requireTLSAddToResourcePolicy(sagemakerBucket);

        new s3deployment.BucketDeployment(scope, "DeployArtefacts", {
            sources: [s3deployment.Source.asset('./lib/artefacts')],
            destinationBucket: artefactsBucket
        });

        const assetStorageTable = new dynamodb.Table(scope, "AssetStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING
            },
            sortKey: {
                name: "assetId",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })

        const databaseStorageTable = new dynamodb.Table(scope, "DatabaseStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })

        const jobStorageTable = new dynamodb.Table(scope, "JobStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "jobId",
                type: dynamodb.AttributeType.STRING
            },
            sortKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })

        const pipelineStorageTable = new dynamodb.Table(scope, "PipelineStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING
            },
            sortKey: {
                name: "pipelineId",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })

        const workflowStorageTable = new dynamodb.Table(scope, "WorkflowStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "databaseId",
                type: dynamodb.AttributeType.STRING
            },
            sortKey: {
                name: "workflowId",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })

        const workflowExecutionStorageTable = new dynamodb.Table(scope, "WorkflowExecutionStorageTable", {
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            partitionKey: {
                name: "pk",
                type: dynamodb.AttributeType.STRING
            },
            sortKey: {
                name: "sk",
                type: dynamodb.AttributeType.STRING
            },
            contributorInsightsEnabled: true,
            encryption: dynamodb.TableEncryption.AWS_MANAGED
        })
        this.s3 = {
            assetBucket: assetBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
            sagemakerBucket: sagemakerBucket
        }
        this.dynamo = {
            assetStorageTable: assetStorageTable,
            jobStorageTable: jobStorageTable,
            pipelineStorageTable: pipelineStorageTable,
            databaseStorageTable: databaseStorageTable,
            workflowStorageTable: workflowStorageTable,
            workflowExecutionStorageTable: workflowExecutionStorageTable
        }
        const assetBucketOutput = new cdk.CfnOutput(this, "AssetBucketNameOutput", {
            value: this.s3.assetBucket.bucketName,
            description: "S3 bucket for asset storage"
        })

        const artefactsBucketOutput = new cdk.CfnOutput(this, "artefactsBucketOutput", {
            value: this.s3.artefactsBucket.bucketName,
            description: "S3 bucket for template notebooks"
        })

    }
}
export function storageResourcesBuilder(scope: Construct): storageResources {

    const accessLogsBucket = new s3.Bucket(scope, "AccessLogsBucket", {
        encryption: s3.BucketEncryption.KMS_MANAGED,
        serverAccessLogsPrefix: 'access-log-bucket-logs/',
        versioned: true,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });

    accessLogsBucket.addLifecycleRule({
        enabled: true,
        expiration: Duration.days(3650),
    });

    requireTLSAddToResourcePolicy(accessLogsBucket);


    const assetBucket = new s3.Bucket(scope, "AssetBucket", {
        cors: [
            {
                allowedOrigins: ["*"],
                allowedHeaders: ['*'],
                allowedMethods: [
                    s3.HttpMethods.GET,
                    s3.HttpMethods.PUT,
                    s3.HttpMethods.POST,
                ],
                exposedHeaders: [
                    'ETag'
                ]
            },
        ],
        versioned: true,
        encryption: s3.BucketEncryption.KMS_MANAGED,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "asset-bucket-logs/",
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });
    requireTLSAddToResourcePolicy(assetBucket);

    const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
        versioned: false,
        encryption: s3.BucketEncryption.KMS_MANAGED,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "artefacts-bucket-logs/",
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });
    requireTLSAddToResourcePolicy(artefactsBucket);

    const sagemakerBucket = new s3.Bucket(scope, "SagemakerBucket", {
        versioned: false,
        encryption: s3.BucketEncryption.KMS_MANAGED,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "sagemaker-bucket-logs/",
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });
    requireTLSAddToResourcePolicy(sagemakerBucket);

    new s3deployment.BucketDeployment(scope, "DeployArtefacts", {
        sources: [s3deployment.Source.asset('./lib/artefacts')],
        destinationBucket: artefactsBucket
    });

    const assetStorageTable = new dynamodb.Table(scope, "AssetStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING
        },
        sortKey: {
            name: "assetId",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })

    const databaseStorageTable = new dynamodb.Table(scope, "DatabaseStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })

    const jobStorageTable = new dynamodb.Table(scope, "JobStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "jobId",
            type: dynamodb.AttributeType.STRING
        },
        sortKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })

    const pipelineStorageTable = new dynamodb.Table(scope, "PipelineStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING
        },
        sortKey: {
            name: "pipelineId",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })

    const workflowStorageTable = new dynamodb.Table(scope, "WorkflowStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "databaseId",
            type: dynamodb.AttributeType.STRING
        },
        sortKey: {
            name: "workflowId",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })

    const workflowExecutionStorageTable = new dynamodb.Table(scope, "WorkflowExecutionStorageTable", {
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true,
        partitionKey: {
            name: "pk",
            type: dynamodb.AttributeType.STRING
        },
        sortKey: {
            name: "sk",
            type: dynamodb.AttributeType.STRING
        },
        contributorInsightsEnabled: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED
    })
    return {
        s3: {
            assetBucket: assetBucket,
            artefactsBucket: artefactsBucket,
            accessLogsBucket: accessLogsBucket,
            sagemakerBucket: sagemakerBucket
        },
        dynamo: {
            assetStorageTable: assetStorageTable,
            jobStorageTable: jobStorageTable,
            pipelineStorageTable: pipelineStorageTable,
            databaseStorageTable: databaseStorageTable,
            workflowStorageTable: workflowStorageTable,
            workflowExecutionStorageTable: workflowExecutionStorageTable
        }
    }
}