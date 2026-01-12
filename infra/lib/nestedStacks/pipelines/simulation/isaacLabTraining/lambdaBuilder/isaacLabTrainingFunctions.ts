/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as batch from "aws-cdk-lib/aws-batch";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as Config from "../../../../../../config/config";
import * as path from "path";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as ServiceHelper from "../../../../../helper/service-helper";

export interface IsaacLabTrainingFunctionsProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    batchJobQueue: batch.IJobQueue;
    batchJobDefinition: batch.IJobDefinition;
}

export class IsaacLabTrainingFunctions extends Construct {
    public readonly openPipelineFunction: lambda.Function;
    public readonly executeBatchJobFunction: lambda.Function;
    public readonly closePipelineFunction: lambda.Function;
    public readonly handleErrorFunction: lambda.Function;
    public readonly vamsExecuteFunction: lambda.Function;

    constructor(scope: Construct, id: string, props: IsaacLabTrainingFunctionsProps) {
        super(scope, id);

        const lambdaPath = path.join(
            __dirname,
            "../../../../../../../backendPipelines/simulation/isaacLabTraining/lambda"
        );

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        const commonProps = {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            timeout: cdk.Duration.minutes(5),
            memorySize: 256,
            layers: [props.lambdaCommonBaseLayer],
            vpc: props.config.app.useGlobalVpc.useForAllLambdas ? props.vpc : undefined,
            vpcSubnets: props.config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: props.pipelineSubnets }
                : undefined,
            securityGroups: props.config.app.useGlobalVpc.useForAllLambdas
                ? props.pipelineSecurityGroups
                : undefined,
        };

        const stackIdentifier = `${props.config.name}-${props.config.app.baseStackName}`;

        // OpenPipeline - builds job config, reads config from S3
        this.openPipelineFunction = new lambda.Function(this, "OpenPipelineFunction", {
            ...commonProps,
            functionName: `${stackIdentifier}-isaaclab-open`,
            handler: "openPipeline.lambda_handler",
            code: lambda.Code.fromAsset(lambdaPath),
        });

        // Grant S3 read access to openPipeline for reading config files from all asset buckets
        s3AssetBuckets.getS3AssetBucketRecords().forEach((record) => {
            record.bucket.grantRead(this.openPipelineFunction);
        });

        // ExecuteBatchJob - internal function that submits Batch job
        this.executeBatchJobFunction = new lambda.Function(this, "ExecuteBatchJobFunction", {
            ...commonProps,
            functionName: `${stackIdentifier}-isaaclab-execute-batch`,
            handler: "executeBatchJob.lambda_handler",
            code: lambda.Code.fromAsset(lambdaPath),
            environment: {
                BATCH_JOB_QUEUE: props.batchJobQueue.jobQueueName,
                BATCH_JOB_DEFINITION: props.batchJobDefinition.jobDefinitionName,
            },
        });

        // Grant Batch permissions
        this.executeBatchJobFunction.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:SubmitJob", "batch:DescribeJobs"],
                resources: [
                    `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-queue/${
                        props.batchJobQueue.jobQueueName
                    }`,
                    `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-definition/${
                        props.batchJobDefinition.jobDefinitionName
                    }*`,
                ],
            })
        );

        // ClosePipeline - handles completion
        this.closePipelineFunction = new lambda.Function(this, "ClosePipelineFunction", {
            ...commonProps,
            functionName: `${stackIdentifier}-isaaclab-close`,
            handler: "closePipeline.lambda_handler",
            code: lambda.Code.fromAsset(lambdaPath),
        });

        // HandleError - notifies external SFN of failures (timeout, OOM, etc.)
        this.handleErrorFunction = new lambda.Function(this, "HandleErrorFunction", {
            ...commonProps,
            functionName: `${stackIdentifier}-isaaclab-handle-error`,
            handler: "handleError.lambda_handler",
            code: lambda.Code.fromAsset(lambdaPath),
        });

        // Grant Step Functions callback permissions for error handler
        this.handleErrorFunction.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
                resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
            })
        );

        // VamsExecute - VAMS entry point, starts internal SFN directly
        this.vamsExecuteFunction = new lambda.Function(this, "VamsExecuteFunction", {
            ...commonProps,
            functionName: `${stackIdentifier}-isaaclab-vams-execute`,
            handler: "vamsExecuteIsaacLabPipeline.lambda_handler",
            code: lambda.Code.fromAsset(lambdaPath),
            // STATE_MACHINE_ARN will be added by the construct after SFN creation
        });
    }
}
