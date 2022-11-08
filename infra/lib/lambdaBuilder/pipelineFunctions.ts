import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as path from "path";
import * as dynamodb from "@aws-cdk/aws-dynamodb";
import * as s3 from "@aws-cdk/aws-s3";
import * as iam from "@aws-cdk/aws-iam"
import { create } from "domain";

export function buildCreatePipelineFunction(
    scope: cdk.Construct,
    pipelineStorageTable: dynamodb.Table,
    artefactsBucket: s3.Bucket, 
    sagemakerBucket: s3.Bucket, 
    assetBucket: s3.Bucket,
    enablePipelineFunction: lambda.Function
): lambda.Function {
    const name = "createPipeline";
    const newPipelineLambdaRole = createRoleToAttachToLambdaPipelines(scope, assetBucket);
    const createPipelineFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/pipelines/`)),
        environment: {
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
            S3_BUCKET: artefactsBucket.bucketName,
            SAGEMAKER_BUCKET_NAME: sagemakerBucket.bucketName,
            SAGEMAKER_BUCKET_ARN: sagemakerBucket.bucketArn,
            ASSET_BUCKET_ARN: assetBucket.bucketArn,
            ENABLE_PIPELINE_FUNCTION_NAME: enablePipelineFunction.functionName, 
            ENABLE_PIPELINE_FUNCTION_ARN: enablePipelineFunction.functionArn, 
            LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET: artefactsBucket.bucketName, 
            LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY: 'sample_lambda_pipeline/lambda_pipeline_deployment_package.zip',
            ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE: newPipelineLambdaRole.roleArn
        },
    });
    enablePipelineFunction.grantInvoke(createPipelineFunction);
    artefactsBucket.grantRead(createPipelineFunction);
    pipelineStorageTable.grantReadWriteData(createPipelineFunction);
    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [ 'cloudFormation:CreateStack' ],
        resources: [ '*' ]
    }));
    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [ 
            "iam:PassRole",
            "iam:CreateRole",
            "iam:GetRole",
            "iam:DeleteRole",
            "iam:CreatePolicy",
            "iam:GetPolicy",
            "iam:DeletePolicy",
            "iam:ListPolicyVersions",
            "iam:AttachRolePolicy",
            "iam:DetachRolePolicy" 
        ],
        resources: [ 'arn:aws:iam::*:role/*NotebookIAMRole*', 'arn:aws:iam::*:policy/*NotebookIAMRolePolicy*' ]
    }));
    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW, 
        actions: [
            "iam:PassRole"
        ], 
        resources: [newPipelineLambdaRole.roleArn]
    }))
    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [                 
            "ecr:CreateRepository",
            "ecr:DeleteRepository", 
            "ecr:DescribeRepositories" 
        ],
        resources: [ '*' ]
    }));
    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [                 
            "sagemaker:CreateNotebookInstanceLifecycleConfig",
            "sagemaker:DescribeNotebookInstanceLifecycleConfig",
            "sagemaker:DeleteNotebookInstanceLifecycleConfig",
            "sagemaker:CreateNotebookInstance",
            "sagemaker:DescribeNotebookInstance",
            "sagemaker:DeleteNotebookInstance"
        ],
        resources: [ '*' ]
    }));

    createPipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [                 
            "lambda:CreateFunction",
        ],
        resources: [ '*' ]
    }));
    return createPipelineFunction;
}

function createRoleToAttachToLambdaPipelines(scope: cdk.Construct, assetBucket: s3.Bucket) {
    const newPipelineLambdaRole = new iam.Role(scope, "lambdaPipelineRole", {
        assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
        inlinePolicies: {
            'ReadWriteAssetBucketPolicy': new iam.PolicyDocument({
                statements: [new iam.PolicyStatement({
                    actions: [
                        "s3:PutObject",
                        "s3:GetObject",
                        "s3:ListBucket",
                        "s3:DeleteObject",
                        "s3:GetObjectVersion"
                    ],
                    resources: [`${assetBucket.bucketArn}`,`${assetBucket.bucketArn}/*`]
                })]
            })
        }
    });
    newPipelineLambdaRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"));
    return newPipelineLambdaRole;
}

export function buildPipelineService(
    scope: cdk.Construct,
    pipelineStorageTable: dynamodb.Table,
): lambda.Function {
    const name = "pipelineService";
    const pipelineService = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/pipelines/`)),
        environment: {
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
        },
    });
    pipelineStorageTable.grantReadWriteData(pipelineService);
    pipelineService.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [ 'cloudFormation:DeleteStack' ],
        resources: [ '*' ]
    }));
    pipelineService.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [ 
            "iam:GetPolicy",
            "iam:GetRole",
            "iam:DeleteRole",
            "iam:DeletePolicy",
            "iam:ListPolicyVersions",
            "iam:DeletePolicyVersion",
            "iam:DetachRolePolicy" 
        ],
        resources: [ 'arn:aws:iam::*:role/*NotebookIAMRole*', 'arn:aws:iam::*:policy/*NotebookIAMRolePolicy*' ]
    }));
    pipelineService.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [                 
            "ecr:DeleteRepository", 
            "ecr:DescribeRepositories"  
        ],
        resources: [ '*' ]
    }));
    pipelineService.addToRolePolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [                 
            "sagemaker:DescribeNotebookInstanceLifecycleConfig",
            "sagemaker:DeleteNotebookInstanceLifecycleConfig",
            "sagemaker:DescribeNotebookInstance",
            "sagemaker:DeleteNotebookInstance",
            "sagemaker:StopNotebookInstance"
        ],
        resources: [ '*' ]
    }));
    return pipelineService;
}

export function buildEnablePipelineFunction(
    scope: cdk.Construct, 
    pipelineStorageTable: dynamodb.Table,
) {
    const name="enablePipeline"
    const enablePipelineFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/pipelines/`)),
        environment: {
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
        },
    });
    pipelineStorageTable.grantReadWriteData(enablePipelineFunction);
    return enablePipelineFunction;
}