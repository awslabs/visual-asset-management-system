/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Lambda function builders for RapidPipeline EKS pipeline.
 * Follows VAMS CDK development workflow patterns.
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../../../../../nestedStacks/storage/storageBuilder-nestedStack";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagErrorsByGrantReadWrite,
} from "../../../../../helper/security";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";

/**
 * Build the VAMS Execute RapidPipeline EKS Lambda function.
 * This function is the entry point for pipeline execution from VAMS workflows.
 *
 * @param scope - CDK construct scope
 * @param lambdaCommonBaseLayer - Common Lambda layer
 * @param storageResources - Storage resources (S3, DynamoDB, KMS)
 * @param openPipelineFunction - Reference to the open pipeline Lambda function
 * @param config - VAMS configuration
 * @param vpc - VPC for Lambda function
 * @param subnets - Subnets for Lambda function
 * @param securityGroups - Security groups for Lambda function
 * @returns Lambda function
 */
export function buildVamsExecuteRapidPipelineEKSFunction(
    scope: Construct,
    lambdaCommonBaseLayer: lambda.ILayerVersion,
    storageResources: storageResources,
    openPipelineFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[]
): lambda.Function {
    const name = "VamsExecuteHandler";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipelineEKS/lambda`)
        ),
        handler: `vamsExecuteRapidPipelineEKS.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: 256,

        // VPC Configuration
        vpc: vpc,
        vpcSubnets: { subnets: subnets },
        securityGroups: securityGroups,

        environment: {
            // Reference to open pipeline function
            OPEN_PIPELINE_FUNCTION_NAME_EKS: openPipelineFunction.functionName,

            // Allowed file extensions
            ALLOWED_INPUT_FILEEXTENSIONS: ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",
        },
    });

    // Grant invoke permission to open pipeline function
    openPipelineFunction.grantInvoke(fun);

    // Grant S3 permissions for all asset buckets using new pattern
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    assetBucketRecords.forEach((record) => {
        record.bucket.grantReadWrite(fun);
    });

    // Grant auxiliary bucket access
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // Apply global environment and permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);

    // CDK Nag Suppressions
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Build the Open Pipeline EKS Lambda function.
 * This function starts the Step Functions state machine for EKS pipeline execution.
 *
 * @param scope - CDK construct scope
 * @param lambdaCommonBaseLayer - Common Lambda layer
 * @param storageResources - Storage resources (S3, DynamoDB, KMS)
 * @param stateMachineName - Name of the Step Functions state machine (ARN will be added later)
 * @param config - VAMS configuration
 * @param vpc - VPC for Lambda function
 * @param subnets - Subnets for Lambda function
 * @param securityGroups - Security groups for Lambda function
 * @returns Lambda function
 */
export function buildOpenPipelineEKSFunction(
    scope: Construct,
    lambdaCommonBaseLayer: lambda.ILayerVersion,
    storageResources: storageResources,
    stateMachine: sfn.StateMachine,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[]
): lambda.Function {
    const name = "OpenPipelineHandler";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipelineEKS/lambda`)
        ),
        handler: `openPipeline.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: 256,

        // VPC Configuration
        vpc: vpc,
        vpcSubnets: { subnets: subnets },
        securityGroups: securityGroups,

        environment: {
            // Full state machine ARN with correct partition (provided by CDK)
            STATE_MACHINE_ARN: stateMachine.stateMachineArn,

            // Allowed file extensions
            ALLOWED_INPUT_FILEEXTENSIONS: ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",
        },
    });

    // Grant Step Functions permissions to start state machine execution
    stateMachine.grantStartExecution(fun);

    // Grant S3 permissions for all asset buckets using new pattern
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    assetBucketRecords.forEach((record) => {
        record.bucket.grantReadWrite(fun);
    });

    // Grant auxiliary bucket access
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // Apply global environment and permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);

    // CDK Nag Suppressions
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Build the Consolidated Handler Lambda function.
 * This function handles multiple EKS pipeline operations (construct, run, check, end).
 *
 * @param scope - CDK construct scope
 * @param lambdaCommonBaseLayer - Common Lambda layer
 * @param kubernetesLayer - Kubernetes Python client Lambda layer
 * @param storageResources - Storage resources (S3, DynamoDB, KMS)
 * @param clusterName - EKS cluster name
 * @param stateMachineName - Name of the Step Functions state machine (ARN will be added later)
 * @param config - VAMS configuration
 * @param vpc - VPC for Lambda function
 * @param subnets - Subnets for Lambda function
 * @param securityGroups - Security groups for Lambda function
 * @returns Lambda function
 */
export function buildConsolidatedHandlerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: lambda.ILayerVersion,
    kubernetesLayer: lambda.ILayerVersion,
    storageResources: storageResources,
    clusterName: string,
    serviceAccountName: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[]
): lambda.Function {
    const name = "ConsolidatedHandler";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipelineEKS/lambda`)
        ),
        handler: `consolidated_handler.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [kubernetesLayer, lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: 1024,

        // VPC Configuration - Required for EKS cluster access
        vpc: vpc,
        vpcSubnets: { subnets: subnets },
        securityGroups: securityGroups,

        environment: {
            // EKS cluster configuration
            EKS_CLUSTER_NAME: clusterName,
            KUBERNETES_NAMESPACE: "default",
            KUBERNETES_SERVICE_ACCOUNT: serviceAccountName,

            // Container image URI
            CONTAINER_IMAGE_URI:
                config.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI ||
                "CONTAINER_IMAGE_PLACEHOLDER",

            // Allowed file extensions
            ALLOWED_INPUT_FILEEXTENSIONS: ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",

            // Test mode
            TEST_MODE: "false",

            // Python path
            PYTHONPATH: "/opt/python:/opt:/var/task:/var/runtime/lib",
        },
    });

    // Grant EKS permissions - comprehensive permissions for cluster access
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: [
                "eks:DescribeCluster",
                "eks:ListClusters",
                "eks:AccessKubernetesApi",
                "eks:GetToken",
                "eks:ListAccessEntries",
                "eks:ListUpdates",
                "eks:DescribeUpdate",
            ],
            resources: ["*"],
            effect: iam.Effect.ALLOW,
        })
    );

    // Grant STS permissions for EKS authentication
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sts:GetCallerIdentity"],
            resources: ["*"], // GetCallerIdentity doesn't support resource-level permissions
        })
    );

    // Grant permissions to pass role for EKS auth
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["iam:PassRole"],
            resources: [fun.role!.roleArn],
        })
    );

    // Grant Step Functions callback permissions for waitForTaskToken pattern
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:stateMachine:vams-*`,
                `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:stateMachine:rapid-pipeline-*`,
            ],
        })
    );

    // Grant S3 permissions for all asset buckets using new pattern
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    assetBucketRecords.forEach((record) => {
        record.bucket.grantReadWrite(fun);
    });

    // Grant auxiliary bucket access
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // Grant KMS permissions
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Apply global environment and permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);

    // CDK Nag Suppressions
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
