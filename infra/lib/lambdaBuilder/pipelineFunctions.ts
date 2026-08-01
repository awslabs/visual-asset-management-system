/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { IAMArn, Service } from "../helper/service-helper";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    kmsKeyPolicyStatementGenerator,
    grantExternalAssetBucketKmsKeys,
    grantReadWritePermissionsToAllAssetBuckets,
} from "../helper/security";
import { PropagatedTagSource } from "aws-cdk-lib/aws-ecs";

// Auto-provisioned pipeline Lambdas are named at runtime by pipelineService with a fixed lowercase
// 'vams-' prefix that does not embed config.name, so both patterns are granted.
const BACKEND_GENERATED_NAME_PATTERN = "vams-*";

function createRoleToAttachToLambdaPipelines(scope: Construct, kmsKey?: kms.IKey) {
    const newPipelineLambdaRole = new iam.Role(scope, "lambdaPipelineRole", {
        assumedBy: Service("LAMBDA").Principal,
        inlinePolicies: {
            ReadWriteAssetBucketPolicy: new iam.PolicyDocument({
                statements: [
                    // Add permissions for all asset buckets from the global array
                    ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                        const prefix = record.prefix || "/";
                        // Build the object-level resource as {bucketArn}/{prefix}*.
                        // The object ARN always needs a '/' separator after the bucket
                        // ARN; strip any leading slash from the prefix so the root
                        // prefix ('/') yields {bucketArn}/* and a non-root prefix
                        // ('vams-assets/') yields {bucketArn}/vams-assets/*.
                        const objectPrefix = prefix.replace(/^\/+/, "");

                        return new iam.PolicyStatement({
                            effect: iam.Effect.ALLOW,
                            actions: [
                                "s3:PutObject",
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:DeleteObject",
                                "s3:GetObjectVersion",
                            ],
                            resources: [
                                record.bucket.bucketArn,
                                `${record.bucket.bucketArn}/${objectPrefix}*`,
                            ],
                        });
                    }),
                ],
            }),
        },
    });
    newPipelineLambdaRole.addManagedPolicy(
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaVPCAccessExecutionRole")
    );

    //Add KMS key use if provided
    if (kmsKey) {
        newPipelineLambdaRole.addToPolicy(kmsKeyPolicyStatementGenerator(kmsKey));
    }

    // Grant access to any external asset bucket customer managed KMS keys
    // (no-op when no external keys are configured)
    grantExternalAssetBucketKmsKeys(newPipelineLambdaRole);

    return newPipelineLambdaRole;
}

export function buildPipelineLambdaSecurityGroup(
    scope: Construct,
    vpc: ec2.IVpc,
    config: Config.Config
): ec2.ISecurityGroup | undefined {
    if (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) {
        const pipelineLambdaSecurityGroup = new ec2.SecurityGroup(scope, "VPCeSecurityGroup", {
            vpc: vpc,
            allowAllOutbound: true,
            description: "VPC Endpoints Security Group",
        });

        return pipelineLambdaSecurityGroup;
    } else {
        return undefined;
    }
}

/**
 * Pipeline service (CRUD + enable/disable/archive). Reads/writes the pipeline table and reads the
 * templates table (templates are listed on the details view). Reads the artefacts bucket for the
 * sample package used when auto-provisioning a Lambda for a Lambda-type pipeline.
 */
export function buildPipelineServiceV2Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "pipelineService";
    // A Lambda-type pipeline created through the API without referencing an existing function has one
    // provisioned for it (seeded from the sample package). The role attached to the new function, its
    // VPC placement, and the sample-package location are supplied here; the handler skips provisioning
    // when the pipeline already references a function (built-ins inject their name at import).
    const newPipelineLambdaRole = createRoleToAttachToLambdaPipelines(
        scope,
        storageResources.encryption.kmsKey
    );
    const newPipelineSubnetIds = buildPipelineLambdaSubnetIds(scope, subnets, config);
    const newPipelineLambdaSecurityGroup = buildPipelineLambdaSecurityGroup(scope, vpc, config);
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.pipelines.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY:
                "sample_lambda_pipeline/lambda_pipeline_deployment_package.zip",
            ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE: newPipelineLambdaRole.roleArn,
            LAMBDA_PYTHON_VERSION: LAMBDA_PYTHON_RUNTIME.name,
            SUBNET_IDS: newPipelineSubnetIds,
            SECURITYGROUP_IDS: newPipelineLambdaSecurityGroup
                ? newPipelineLambdaSecurityGroup.securityGroupId
                : "",
            // Reject creating/updating a DeadlineCloud pipeline when the type is disabled (its
            // workflow createJob task + callback lambda are only deployed when enabled).
            DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED: config.app.pipelines
                .deadlineCloudExecutionTypeEnabled
                ? "true"
                : "false",
        },
    });
    storageResources.dynamo.pipelineStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineTemplatesStorageTable.grantReadData(fun);
    // Saving a require-template pipeline emits a non-blocking warning when the pipeline is part of an
    // auto-triggered workflow whose trigger picked no default template for it; that check reads the
    // workflow + trigger tables.
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    // Read the sample pipeline package for auto-provisioned Lambda pipelines.
    storageResources.s3.artefactsBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    // Auto-provisioning permissions: pass the pipeline-execution role to the new function, create the
    // function (scoped to VAMS-named functions), and describe VPC networking for its VpcConfig.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:PassRole"],
            resources: [newPipelineLambdaRole.roleArn],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:CreateFunction", "lambda:UpdateFunctionConfiguration"],
            resources: [
                IAMArn("*" + config.name + "*").lambda,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).lambda,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "ec2:DescribeVpcs"],
            // ec2:Describe* actions do not support resource-level permissions.
            resources: ["*"],
        })
    );
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

/**
 * Pipeline template + tag-schema service. Reads/writes the templates + tag-schema tables, reads the
 * pipeline table (parent-object Casbin) + buckets table (default bucket), and reads/writes all asset
 * buckets so it can offload/rehydrate large template bodies + tag schemas to the default bucket
 * under pipelines/.
 */
export function buildPipelineTemplateServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "pipelineTemplateService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.pipelines.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {},
    });
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineTemplatesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineTemplateTagSchemaStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Saving a template that is chosen as a trigger default is rejected when it has a required tag
    // with no default (a headless trigger run could never supply it); that check reads the workflow
    // + trigger tables to find referencing triggers.
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    // Read/write all asset buckets (includes the default bucket) for template body + tag-schema
    // S3 offload/rehydration under pipelines/.
    grantReadWritePermissionsToAllAssetBuckets(fun);
    grantExternalAssetBucketKmsKeys(fun);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildPipelineLambdaSubnetIds(
    scope: Construct,
    subnets: ec2.ISubnet[],
    config: Config.Config
): string {
    if (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) {
        const subnetsArray: string[] = [];

        subnets.forEach((element) => {
            subnetsArray.push(element.subnetId);
        });
        return subnetsArray.join(",");
    } else {
        return "";
    }
}
