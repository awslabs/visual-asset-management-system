/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import * as Config from "../../../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../../../../../helper/security";
import { grantReadWritePermissionsToAllAssetBuckets } from "../../../../../helper/security";
import { suppressCdkNagErrorsByGrantReadWrite } from "../../../../../helper/security";
import { suppressCdkNagLambda } from "../../../../../helper/security";

// The conversion loads the whole model into memory before it writes it, so the function's memory sets
// the largest file it converts: the container's size check (backendPipelines/conversion/3dBasic/
// lambdaContainer/size_check.py) derives every limit from AWS_LAMBDA_FUNCTION_MEMORY_SIZE. This
// function runs at the Lambda maximum, 10,240 MB, its own setting rather than the shared
// Config.LAMBDA_MEMORY_SIZE (5,308 MB): at 5,308 MB a STEP or 3MF input was limited to about 40 MB and
// a USD output of a GLB to about 107 MB. A run bills at the configured memory for its duration.
export const CONVERSION_MEMORY_SIZE_MB = 10240;

// The conversion stages the downloaded input and the exported output on local disk, and an
// uncompressed export (OBJ text from a binary PLY) is up to 3.5 times its input. The size check
// takes the smaller of the memory and the disk limit; 6 GiB keeps disk above every memory limit at
// 10,240 MB (the largest, OBJ at about 991 MB, needs about 4.4 GiB with the headroom), so memory stays
// the binding constraint.
export const CONVERSION_EPHEMERAL_STORAGE = cdk.Size.gibibytes(6);

export function buildVamsExecute3dBasicConversionPipelineFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecute3dBasicConversion";
    // STEP input installs cascadio (Open CASCADE) into the image; the default image holds none of it,
    // and the handler refuses a STEP run before download when the variable is not "true".
    const enableStepInput = config.app.pipelines.useConversion3dBasic.enableStepInput === true;

    const fun = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/3dBasic/lambdaContainer"
            ),
            {
                platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64, //Fix to the LINUX_AMD64 platform to standardize instruction set across all loads
                buildArgs: { ENABLE_STEP_INPUT: enableStepInput ? "true" : "false" },
            }
        ),
        // Must match the image: the Dockerfile builds FROM --platform=linux/amd64 and the asset sets
        // Platform.LINUX_AMD64. Lambda runs an image only on the architecture the function declares.
        architecture: lambda.Architecture.X86_64,
        timeout: Duration.minutes(15),
        memorySize: CONVERSION_MEMORY_SIZE_MB,
        ephemeralStorageSize: CONVERSION_EPHEMERAL_STORAGE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: { ENABLE_STEP_INPUT: enableStepInput ? "true" : "false" },
    });

    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}
