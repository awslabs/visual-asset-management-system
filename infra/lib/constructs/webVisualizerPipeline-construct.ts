/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as s3 from "aws-cdk-lib/aws-s3";
import * as dest from 'aws-cdk-lib/aws-s3-notifications';
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as batch from '@aws-cdk/aws-batch-alpha';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as kms from "aws-cdk-lib/aws-kms";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as path from "path";
import { LambdaSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { buildConstructPipelineFunction, buildOpenPipelineFunction } from "../lambdaBuilder/visualizerPipelineFunctions";
import { CfnJobDefinition } from "aws-cdk-lib/aws-batch";
import { BatchFargatePipelineConstruct } from "./nested/batch-fargate-pipeline";

export interface WebVisualizationPipelineConstructProps extends cdk.StackProps {
  assetBucket: s3.Bucket;
  assetVisualizerBucket: s3.Bucket;
  vpc: ec2.Vpc;
  pipelineSubnets: ec2.ISubnet[];
  pipelineSecurityGroups: ec2.SecurityGroup[];
}

/**
 * Default input properties
 */
const defaultProps: Partial<WebVisualizationPipelineConstructProps> = {
  stackName: "",
  env: {},
};

/**
 * Deploys a specific batch to ECS workflow for a particular container image for the website visualizer files
 * Creates:
 * - SNS
 * - SFN
 * - Batch
 * - ECR Image
 * - ECS
 * - IAM Roles / Policy Documents for permissions to S3 / Lambda
 * On redeployment, will automatically invalidate the CloudFront distribution cache
 */
export class WebVisualizationPipelineConstruct extends Construct {
  constructor(parent: Construct, name: string, props: WebVisualizationPipelineConstructProps) {
    super(parent, name);

    props = { ...defaultProps, ...props };

    const region = Stack.of(this).region;
    const account = Stack.of(this).account;

    const vpcSubnets = props.vpc.selectSubnets({
      subnets: props.pipelineSubnets,
    });

    /**
     * SNS Resources
     */
    const topicKey = new kms.Key(this, "ObjectCreatedTopicKey", {
      description: "KMS key for ObjectCreatedTopic",
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

    // Object Create Topic -- S3 /.laz,  /.las, /.e57
    const S3AssetsObjectCreatedTopic_PointCloud = new sns.Topic(
      this,
      "S3AssetsObjectCreatedTopic_PointCloud",
      {
        masterKey: topicKey,
      }
    );

    // trigger an event notification when a .las, .laz, or .e47 point cloud file is uploaded
    props.assetBucket.addObjectCreatedNotification(
      new dest.SnsDestination(S3AssetsObjectCreatedTopic_PointCloud),
      {
        suffix: ".laz",
      }
    );

    props.assetBucket.addObjectCreatedNotification(
      new dest.SnsDestination(S3AssetsObjectCreatedTopic_PointCloud),
      {
        suffix: ".las",
      }
    );

    props.assetBucket.addObjectCreatedNotification(
      new dest.SnsDestination(S3AssetsObjectCreatedTopic_PointCloud),
      {
        suffix: ".e57",
      }
    );

    /**
     * Batch Resources
     */
    const inputBucketPolicy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          resources: [
            props.assetBucket.bucketArn,
            `${props.assetBucket.bucketArn}/*`
          ],
        }),
        new iam.PolicyStatement({
          actions: ["s3:ListBucket"],
          resources: [props.assetBucket.bucketArn],
        }),
      ],
    });

    const outputBucketPolicy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          resources: [
            props.assetVisualizerBucket.bucketArn,
            `${props.assetVisualizerBucket.bucketArn}/*`,
          ],
        }),
        new iam.PolicyStatement({
          actions: ["s3:ListBucket"],
          resources: [props.assetVisualizerBucket.bucketArn],
        }),
      ],
    });

    const stateTaskPolicy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
          resources: [`arn:aws:states:${region}:${account}:*`],
        }),
      ],
    });

    const containerExecutionRole = new iam.Role(
      this,
      "VisualizerPipelineContainerExecutionRole",
      {
        assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        inlinePolicies: {
          InputBucketPolicy: inputBucketPolicy,
          OutputBucketPolicy: outputBucketPolicy,
          StateTaskPolicy: stateTaskPolicy,
        },
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName(
            "service-role/AmazonECSTaskExecutionRolePolicy"
          ),
          iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
        ],
      }
    );

    const containerJobRole = new iam.Role(this, "VisualizerPipelineContainerJobRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      inlinePolicies: {
        InputBucketPolicy: inputBucketPolicy,
        OutputBucketPolicy: outputBucketPolicy,
        StateTaskPolicy: stateTaskPolicy,
      },
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AmazonECSTaskExecutionRolePolicy"
        ),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
      ],
    });

    /**
     * AWS Batch Job Definition & Compute Env for PDAL Container
     */
    const pdalBatchPipeline = new BatchFargatePipelineConstruct(this, "BatchFargatePipeline_PDAL", {
      vpc: props.vpc,
      subnets: props.pipelineSubnets,
      securityGroups: props.pipelineSecurityGroups,
      jobRole: containerJobRole,
      executionRole: containerExecutionRole,
      imageAssetPath: path.join("..", "..", "..", "..", "backendVisualizerPipelines", "pc"),
      dockerfileName: "Dockerfile_PDAL",
      batchJobDefinitionName: "VisualizerPipelineJob_PDAL"
    });

    /**
     * AWS Batch Job Definition & Compute Env for Potree Container
     */
    const potreeBatchPipeline = new BatchFargatePipelineConstruct(this, "BatchFargatePipeline_Potree", {
      vpc: props.vpc,
      subnets: props.pipelineSubnets,
      securityGroups: props.pipelineSecurityGroups,
      jobRole: containerJobRole,
      executionRole: containerExecutionRole,
      imageAssetPath: path.join("..", "..", "..", "..", "backendVisualizerPipelines", "pc"),
      dockerfileName: "Dockerfile_Potree",
      batchJobDefinitionName: "VisualizerPipelineJob_Potree"
    });

    /**
     * SFN States
     */

    // connect pipeline lambda function
    // transforms data input for AWS Batch
    const constructPipelineFunction = buildConstructPipelineFunction(
      this,
      props.vpc,
      props.pipelineSubnets,
      props.pipelineSecurityGroups
    );

    // creates pipeline definition based on event notification input
    const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
      lambdaFunction: constructPipelineFunction,
      outputPath: "$.Payload",
    });

    // error handler pass - PDAL Batch
    const handlePdalError = new sfn.Pass(
      this,
      "HandlePdalError",
      {
        resultPath: "$",
      }
    );

    // error handler pass - Potree Batch
    const handlePotreeError = new sfn.Pass(
      this,
      "HandlePotreeError",
      {
        resultPath: "$",
      }
    );

    // end state: success
    const successState = new sfn.Succeed(this, "SuccessState", {
      comment: "Pipeline returned SUCCESS",
    });

    // end state: failure
    const failState = new sfn.Fail(this, "FailState", {
      cause: "AWS Batch Job Failed",
      error: "Batch Job returned FAIL",
    });

    // batch job Potree Converter
    const potreeConverterBatchJob = new tasks.BatchSubmitJob(
      this,
      "PotreeConverterBatchJob",
      {
        jobName: sfn.JsonPath.stringAt("$.jobName"),
        jobDefinitionArn: potreeBatchPipeline.batchJobDefinition.jobDefinitionArn,
        jobQueueArn: potreeBatchPipeline.batchJobQueue.jobQueueArn,
        containerOverrides: {
          command: [...sfn.JsonPath.listAt("$.pipeline.definition")],
          environment: {
            TASK_TOKEN: sfn.JsonPath.taskToken,
          },
        },
      }
    )
      .addCatch(handlePotreeError, {
        resultPath: "$.error",
      })
      .next(
        new sfn.Choice(this, "EndStatesChoice")
          .when(sfn.Condition.isPresent("$.result.error"), failState)
          .otherwise(successState)
      );

    // batch job PDAL
    const pdalConverterBatchJob = new tasks.BatchSubmitJob(
      this,
      "PdalConverterBatchJob-PDAL",
      {
        jobName: sfn.JsonPath.stringAt("$.jobName"),
        jobDefinitionArn: pdalBatchPipeline.batchJobDefinition.jobDefinitionArn,
        jobQueueArn: pdalBatchPipeline.batchJobQueue.jobQueueArn,
        containerOverrides: {
          command: [...sfn.JsonPath.listAt("$.pipeline.definition")],
          environment: {
            TASK_TOKEN: sfn.JsonPath.taskToken,
          },
        },
      }
    )
      .addCatch(handlePdalError, {
        resultPath: "$.error",
      })
      .next(potreeConverterBatchJob);

    /**
     * SFN Definition
     */
    const sfnPipelineDefinition = sfn.Chain.start(
      constructPipelineTask.next(new sfn.Choice(this, "FilePathExtensionCheck")
        .when(
          sfn.Condition.stringEquals("$.pipeline.type", "POTREE"),
          potreeConverterBatchJob
        )
        .otherwise(pdalConverterBatchJob)
      )
    );

    /**
     * CloudWatch Log Group
     */
    const stateMachineLogGroup = new logs.LogGroup(
      this,
      "PointCloudVisualizerPipelineProcessing-StateMachineLogGroup",
      {
        logGroupName: "/aws/stateMachine-VizPipeline/",
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }
    );

    /**
     * SFN State Machine
     */
    const pipelineStateMachine = new sfn.StateMachine(
      this,
      "PointCloudVisualizerPipelineProcessing-StateMachine",
      {
        definitionBody: sfn.DefinitionBody.fromChainable(sfnPipelineDefinition),
        timeout: Duration.hours(5),
        logs: {
          destination: stateMachineLogGroup,
          includeExecutionData: true,
          level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
      }
    );

    pipelineStateMachine.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ImportKeyMaterial",
        ],
        resources: [topicKey.keyArn],
      })
    );

    pipelineStateMachine.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["kms:ListKeys"],
        resources: ["*"],
      })
    );

    /**
     * Lambda Resources & SNS Subscriptions
     */
    //Build Lambda Web Visualizer Pipeline Resources
    const openPipelineFunction = buildOpenPipelineFunction(
      this,
      props.assetBucket,
      props.assetVisualizerBucket,
      pipelineStateMachine,
      props.vpc,
      props.pipelineSubnets,
    );

    //Add subscription to kick-off lambda function of pipeline
    S3AssetsObjectCreatedTopic_PointCloud.addSubscription(
      new LambdaSubscription(openPipelineFunction)
    );
  }

}
