import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as path from "path";
import * as dynamodb from "@aws-cdk/aws-dynamodb";
import * as iam from "@aws-cdk/aws-iam";
import * as s3 from "@aws-cdk/aws-s3";

export function buildWorkflowService(
  scope: cdk.Construct,
  workflowStorageTable: dynamodb.Table
): lambda.Function {
  const name = "workflowService";
  const workflowService = new lambda.Function(scope, name, {
    runtime: lambda.Runtime.PYTHON_3_8,
    handler: `${name}.lambda_handler`,
    code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/workflows/`)),
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
  scope: cdk.Construct,
): lambda.Function {
  const name = "runProcessingJob";
  const runProcessingJobFunction = new lambda.Function(scope, name, {
      runtime: lambda.Runtime.PYTHON_3_8,
      handler: `${name}.lambda_handler`,
      code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/workflows/`)),
      environment: {
      },
  });
  return runProcessingJobFunction;
}

export function buildListlWorkflowExecutionsFunction(
  scope: cdk.Construct,
  workflowExecutionStorageTable: dynamodb.Table
): lambda.Function {
  const name = "listExecutions";
  const listAllWorkflowsFunction = new lambda.Function(scope, name, {
    runtime: lambda.Runtime.PYTHON_3_8,
    handler: `${name}.lambda_handler`,
    code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/workflows/`)),
    timeout: cdk.Duration.seconds(30),
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
  scope: cdk.Construct,
  workflowStorageTable: dynamodb.Table,
  assetStorageBucket: s3.Bucket,
  uploadAllAssetFunction: lambda.Function,
  layer: lambda.LayerVersion
): lambda.Function {
  const role = buildWorkflowRole(
    scope,
    assetStorageBucket,
    uploadAllAssetFunction
  );
  const name = "createWorkflow";
  const createWorkflowFunction = new lambda.Function(scope, name, {
    runtime: lambda.Runtime.PYTHON_3_8,
    handler: `${name}.lambda_handler`,
    code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/workflows/`)),
    layers: [layer],
    timeout: cdk.Duration.seconds(90),
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
  return createWorkflowFunction;
}

export function buildRunWorkflowFunction(
  scope: cdk.Construct,
  workflowStorageTable: dynamodb.Table,
  pipelineStorageTable: dynamodb.Table,
  assetStorageTable: dynamodb.Table,
  workflowExecutionStorageTable: dynamodb.Table,
  assetStorageBucket: s3.Bucket,
  layer: lambda.LayerVersion
): lambda.Function {
  const name = "executeWorkflow";
  const runWorkflowFunction = new lambda.Function(scope, name, {
    runtime: lambda.Runtime.PYTHON_3_8,
    handler: `${name}.lambda_handler`,
    code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/workflows/`)),
    layers: [layer],
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
  scope: cdk.Construct,
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
