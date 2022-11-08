import * as cdk from "@aws-cdk/core";
import * as s3 from '@aws-cdk/aws-s3';
import * as dynamodb from '@aws-cdk/aws-dynamodb';
import * as s3deployment from "@aws-cdk/aws-s3-deployment";
import { Duration } from "@aws-cdk/core";
import { BlockPublicAccess } from "@aws-cdk/aws-s3";
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
export function storageResourcesBuilder(scope: cdk.Construct): storageResources {

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

    const artefactsBucket = new s3.Bucket(scope, "ArtefactsBucket", {
        versioned: false,
        encryption: s3.BucketEncryption.KMS_MANAGED, 
        serverAccessLogsBucket: accessLogsBucket, 
        serverAccessLogsPrefix: "artefacts-bucket-logs/", 
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });

    const sagemakerBucket = new s3.Bucket(scope, "SagemakerBucket", {
        versioned: false,
        encryption: s3.BucketEncryption.KMS_MANAGED, 
        serverAccessLogsBucket: accessLogsBucket, 
        serverAccessLogsPrefix: "sagemaker-bucket-logs/", 
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });


    new s3deployment.BucketDeployment(scope, "DeployArtefacts",{
        sources: [s3deployment.Source.asset('./lib/artefacts')],
        destinationBucket: artefactsBucket
    });
      
      const assetStorageTable = new dynamodb.Table(scope, "AssetStorageTable", {
          billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, 
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
          partitionKey: {
              name: "databaseId", 
              type: dynamodb.AttributeType.STRING
          },
          contributorInsightsEnabled: true, 
          encryption: dynamodb.TableEncryption.AWS_MANAGED
      })

      const jobStorageTable = new dynamodb.Table(scope, "JobStorageTable", {
          billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, 
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