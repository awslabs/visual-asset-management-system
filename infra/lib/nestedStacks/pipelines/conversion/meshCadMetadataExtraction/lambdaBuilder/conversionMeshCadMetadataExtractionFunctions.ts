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

// The extraction stages the downloaded input on local disk, so the budget has to cover one copy of the
// CAD or mesh file plus the small metadata.json it writes — more than the 512 MB Lambda default.
//
// Disk is not the binding limit. Each format handler parses the whole file into memory, so peak memory
// is what caps the supported file size against LAMBDA_MEMORY_SIZE; this budget only stops disk from
// being the limit reached first. The figure matches the 3dBasic conversion so the two conversion
// pipelines carry one budget, not because their staging needs are the same.
const CONVERSION_EPHEMERAL_STORAGE = cdk.Size.gibibytes(4);

export function buildVamsExecuteMeshCadMetadataExtractionPipelineFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteMeshCadMetadataExtractionConversion";

    const fun = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/meshCadMetadataExtraction/lambdaContainer"
            ),
            {
                platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64, //Fix to the LINUX_AMD64 platform to standardize instruction set across all loads
            }
        ),
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ephemeralStorageSize: CONVERSION_EPHEMERAL_STORAGE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {},
    });

    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}
