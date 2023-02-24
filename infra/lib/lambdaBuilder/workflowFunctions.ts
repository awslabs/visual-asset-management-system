/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from 'aws-cdk-lib';
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import * as lambdaPython from "@aws-cdk/aws-lambda-python-alpha"
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../security";
export function buildWorkflowService(
  scope: Construct,
  workflowStorageTable: dynamodb.Table
): lambda.Function {
  const name = "workflowService";
  const workflowService = new lambda.DockerImageFunction(scope, name, {
    code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`),{
        cmd: [`backend.handlers.workflows.${name}.lambda_handler`], 
    }),
    timeout: Duration.minutes(15), 
    memorySize: 3008,
    environment: {
      WORKFLOW_STORAGE_TABLE_NAME: workflowStorageTable.tableName,
    },
  });
  workflowStorageTable.grantReadWriteData(workflowService);
  workflowService.addToRolePolicy(
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        "states:DeleteStateMachine",
        "states:DescribeStateMachine",
        "states:UpdateStateMachine",
      ],
      resources: ["*"],
    })
  );
  return workflowService;
}

export function buildRunProcessingJobFunction(
  scope: Construct,
): lambda.Function {
  const name = "runProcessingJob";
  const runProcessingJobFunction = new lambda.DockerImageFunction(scope, name, {
    code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`),{
        cmd: [`backend.handlers.workflows.${name}.lambda_handler`], 
    }),
    timeout: Duration.minutes(15), 
    memorySize: 3008,
      environment: {
      },
  });
  return runProcessingJobFunction;
}

export function buildListlWorkflowExecutionsFunction(
  scope: Construct,
  workflowExecutionStorageTable: dynamodb.Table
): lambda.Function {
  const name = "listExecutions";
  const listAllWorkflowsFunction = new lambda.DockerImageFunction(scope, name, {
    code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`),{
        cmd: [`backend.handlers.workflows.${name}.lambda_handler`], 
    }),
    timeout: Duration.minutes(15), 
    memorySize: 3008,
    environment: {
      WORKFLOW_EXECUTION_STORAGE_TABLE_NAME:
        workflowExecutionStorageTable.tableName,
    },
  });
  workflowExecutionStorageTable.grantReadData(listAllWorkflowsFunction);
  listAllWorkflowsFunction.addToRolePolicy(
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ["states:DescribeExecution"],
      resources: ["*"],
    })
  );
  return listAllWorkflowsFunction;
}

export function buildCreateWorkflowFunction(
  scope: Construct,
  workflowStorageTable: dynamodb.Table,
  assetStorageBucket: s3.Bucket,
  uploadAllAssetFunction: lambda.Function
): lambda.Function {
  const role = buildWorkflowRole(
    scope,
    assetStorageBucket,
    uploadAllAssetFunction
  );
  const name = "createWorkflow";
  const createWorkflowFunction = new lambda.DockerImageFunction(scope, name, {
    code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`),{
        cmd: [`backend.handlers.workflows.${name}.lambda_handler`], 
    }),
    timeout: Duration.minutes(15), 
    memorySize: 3008,
    environment: {
      WORKFLOW_STORAGE_TABLE_NAME: workflowStorageTable.tableName,
      UPLOAD_ALL_LAMBDA_FUNCTION_NAME: uploadAllAssetFunction.functionName,
      LAMBDA_ROLE_ARN: role.roleArn,
    },
  });
  workflowStorageTable.grantReadWriteData(createWorkflowFunction);
  createWorkflowFunction.addToRolePolicy(
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        "states:CreateStateMachine",
        "states:DescribeStateMachine",
        "states:UpdateStateMachine",
      ],
      resources: ["*"],
    })
  );
  createWorkflowFunction.addToRolePolicy(
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ["iam:PassRole"],
      resources: ["arn:aws:iam::*:role/*VAMS*"],
    })
  );
  suppressCdkNagErrorsByGrantReadWrite(createWorkflowFunction);
  return createWorkflowFunction;
}

export function buildRunWorkflowFunction(
  scope: Construct,
  workflowStorageTable: dynamodb.Table,
  pipelineStorageTable: dynamodb.Table,
  assetStorageTable: dynamodb.Table,
  workflowExecutionStorageTable: dynamodb.Table,
  assetStorageBucket: s3.Bucket
): lambda.Function {
  const name = "executeWorkflow";
  const runWorkflowFunction = new lambda.DockerImageFunction(scope, name, {
    code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`),{
        cmd: [`backend.handlers.workflows.${name}.lambda_handler`], 
    }),
    timeout: Duration.minutes(15), 
    memorySize: 3008,
    environment: {
      WORKFLOW_STORAGE_TABLE_NAME: workflowStorageTable.tableName,
      PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
      ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
      WORKFLOW_EXECUTION_STORAGE_TABLE_NAME:
        workflowExecutionStorageTable.tableName
    },
  });
  workflowStorageTable.grantReadData(runWorkflowFunction);
  pipelineStorageTable.grantReadData(runWorkflowFunction);
  assetStorageTable.grantReadData(runWorkflowFunction);
  workflowExecutionStorageTable.grantReadWriteData(runWorkflowFunction);
  assetStorageBucket.grantReadWrite(runWorkflowFunction);
  suppressCdkNagErrorsByGrantReadWrite(runWorkflowFunction);
  runWorkflowFunction.addToRolePolicy(
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ["states:StartExecution", "states:DescribeStateMachine"],
      resources: ["*"],
    })
  );
  return runWorkflowFunction;
}

export function buildWorkflowRole(
  scope: Construct,
  assetStorageBucket: s3.Bucket,
  uploadAllAssetFunction: lambda.Function
): iam.Role {
  const createWorkflowPolicy = new iam.PolicyDocument({
    statements: [
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "states:CreateStateMachine",
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule",
        ],
        resources: ["*"],
      }),
    ],
  });

  const runWorkflowPolicy = new iam.PolicyDocument({
    statements: [
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "sagemaker:CreateProcessingJob",
          "ecr:Describe*",
          "sagemaker:DescribeEndpointConfig",
          "sagemaker:DescribeModel",
          "sagemaker:InvokeEndpoint",
          "sagemaker:ListTags",
          "sagemaker:DescribeEndpoint",
          "sagemaker:CreateModel",
          "sagemaker:CreateEndpointConfig",
          "sagemaker:CreateEndpoint",
          "sagemaker:DeleteModel",
          "sagemaker:DeleteEndpointConfig",
          "sagemaker:DeleteEndpoint",
          "sagemaker:AddTags",
          "cloudwatch:PutMetricData",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup",
          "logs:DescribeLogStreams",
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["s3:ListBucket", "s3:PutObject", "s3:GetObject"],
        resources: [
          assetStorageBucket.bucketArn,
          assetStorageBucket.bucketArn + "/*",
        ],
      }),
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["lambda:InvokeFunction"],
        resources: [uploadAllAssetFunction.functionArn],
      }),
      // For lambda pipelines created through lambda functions
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["lambda:InvokeFunction"],
        resources: ['*']
      }),
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["iam:PassRole"],
        resources: ["arn:aws:iam::*:role/*VAMS*"],
      }),
    ],
  });

  const role = new iam.Role(scope, "VAMSWorkflowIAMRole", {
    assumedBy: new iam.CompositePrincipal(
      new iam.ServicePrincipal("lambda.amazonaws.com"),
      new iam.ServicePrincipal("sagemaker.amazonaws.com"),
      new iam.ServicePrincipal("states.amazonaws.com")
    ),
    description: "VAMS Workflow IAM Role.",
    inlinePolicies: {
      createWorkflowPolicy: createWorkflowPolicy,
      runWorkflowPolicy: runWorkflowPolicy,
    },
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName("AWSLambdaExecute"),
    ],
  });

  return role;
}
