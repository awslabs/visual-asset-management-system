/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Duration } from "aws-cdk-lib";
import { JsonPath, TaskInput } from "aws-cdk-lib/aws-stepfunctions";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../../config/config";
import { generateUniqueNameHash } from "../../../helper/security";

export function buildUploadAssetWorkflow(
    scope: Construct,
    config: Config.Config,
    uploadAssetFunction: lambda.Function,
    updateMetadataFunction: lambda.Function,
    executeWorkflowFunction: lambda.Function,
    assetStagingBucket?: s3.IBucket
    //assetVisualizerStagingBucket?: s3.IBucket
): sfn.StateMachine {
    const callUploadAssetLambdaTask = new tasks.LambdaInvoke(scope, "Upload Asset Task", {
        lambdaFunction: uploadAssetFunction,
        payload: TaskInput.fromJsonPathAt("$.uploadAssetBody"),
        resultPath: "$.uploadAssetResult",
        outputPath: "$",
    });

    const callUpdateMetadataTask = new tasks.LambdaInvoke(scope, "Update Metadata Task", {
        lambdaFunction: updateMetadataFunction,
        payload: TaskInput.fromJsonPathAt("$.updateMetadataBody"),
        resultPath: "$.updateMetadataResult",
        outputPath: "$",
    });
    const map = new sfn.Map(scope, "Map State", {
        maxConcurrency: 1,
        itemsPath: sfn.JsonPath.stringAt("$.executeWorkflowBody"),
    });
    const callExecuteWorkflowTask = new tasks.LambdaInvoke(scope, "Call Execute Workflows Task", {
        lambdaFunction: executeWorkflowFunction,
    });
    map.itemProcessor(callExecuteWorkflowTask);

    const pass = new sfn.Pass(scope, "No metadata included");
    const updateMetadataChoice = new sfn.Choice(scope, "Is metadata included?")
        .when(sfn.Condition.isNotNull("$.updateMetadataBody"), callUpdateMetadataTask)
        .when(sfn.Condition.isNull("$.updateMetadataBody"), pass)
        .afterwards();

    const passE = new sfn.Pass(scope, "No workflows included");
    const callExecuteWorkflowChoice = new sfn.Choice(scope, "Are workflows included?")
        .when(sfn.Condition.isNotNull("$.executeWorkflowBody"), map)
        .when(sfn.Condition.isNull("$.executeWorkflowBody"), passE)
        .afterwards();

    //Setup staging buckets
    let assetStagingCopyObjectChoice = undefined;
    //let assetVisualizerStagingCopyObjectChoice = undefined
    if (assetStagingBucket) {
        const copyObjectTask = new tasks.CallAwsService(scope, "S3 Copy Object", {
            service: "s3",
            action: "copyObject",
            iamResources: [assetStagingBucket.arnForObjects("*")],
            inputPath: "$.copyObjectBody",
            parameters: {
                Bucket: JsonPath.stringAt("$.bucket"),
                Key: JsonPath.stringAt("$.key"),
                CopySource: JsonPath.stringAt("$.copySource"),
            },
            resultPath: JsonPath.DISCARD,
            outputPath: "$",
        });
        const passCopy = new sfn.Pass(scope, "Object not provided");
        assetStagingCopyObjectChoice = new sfn.Choice(scope, "Is Object included?")
            .when(sfn.Condition.isNotNull("$.copyObjectBody"), copyObjectTask)
            .when(sfn.Condition.isNull("$.copyObjectBody"), passCopy)
            .afterwards();
    }

    // if (assetVisualizerStagingBucket) {
    //     const copyObjectTask = new tasks.CallAwsService(scope, "S3 Copy Object", {
    //         service: "s3",
    //         action: "copyObject",
    //         iamResources: [assetVisualizerStagingBucket.arnForObjects("*")],
    //         inputPath: "$.copyObjectBody",
    //         parameters: {
    //             Bucket: JsonPath.stringAt("$.bucket"),
    //             Key: JsonPath.stringAt("$.key"),
    //             CopySource: JsonPath.stringAt("$.copySource"),
    //         },
    //         resultPath: JsonPath.DISCARD,
    //         outputPath: "$",
    //     });
    //     const passCopy = new sfn.Pass(scope, "Object not provided");
    //     assetVisualizerStagingCopyObjectChoice = new sfn.Choice(scope, "Is Object included?")
    //         .when(sfn.Condition.isNotNull("$.copyObjectBody"), copyObjectTask)
    //         .when(sfn.Condition.isNull("$.copyObjectBody"), passCopy)
    //         .afterwards();
    // }

    let definition;
    // if (assetStagingCopyObjectChoice && assetVisualizerStagingCopyObjectChoice) {
    //     definition = assetStagingCopyObjectChoice
    //         .next(assetVisualizerStagingCopyObjectChoice)
    //         .next(callUploadAssetLambdaTask)
    //         .next(updateMetadataChoice)
    //         .next(callExecuteWorkflowChoice);
    // } else
    if (assetStagingCopyObjectChoice) {
        definition = assetStagingCopyObjectChoice
            .next(callUploadAssetLambdaTask)
            .next(updateMetadataChoice)
            .next(callExecuteWorkflowChoice);
    } else {
        definition = callUploadAssetLambdaTask
            .next(updateMetadataChoice)
            .next(callExecuteWorkflowChoice);
    }

    const logGroup = new logs.LogGroup(scope, "UploadAssetWorkflowLogs", {
        logGroupName:
            "/aws/vendedlogs/VAMSUploadAssetWorkflowLogs" +
            generateUniqueNameHash(
                config.env.coreStackName,
                config.env.account,
                "UploadAssetWorkflowLogs",
                10
            ),
        retention: logs.RetentionDays.TEN_YEARS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    return new sfn.StateMachine(scope, "UploadAssetWorkflow", {
        definitionBody: sfn.DefinitionBody.fromChainable(definition),
        timeout: Duration.minutes(10),
        logs: {
            level: sfn.LogLevel.ALL,
            destination: logGroup,
        },
        tracingEnabled: true, //This was pointed out by CDK-Nag
    });
}
