/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, Names, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { buildVamsExecute3dBasicConversionPipelineFunction } from "../lambdaBuilder/conversion3dBasicFunctions";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import { layerBundlingCommand } from "../../../../../helper/lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";

export interface Conversion3dBasicConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Conversion3dBasicConstructProps> = {
    //stackName: "",
    //env: {},
};

export class Conversion3dBasicConstruct extends NestedStack {
    public pipelineVamsLambdaFunctionName = "";

    constructor(parent: Construct, name: string, props: Conversion3dBasicConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        //Build Lambda VAMS Execution Function
        const pipelineConversion3dBasicLambdaFunction =
            buildVamsExecute3dBasicConversionPipelineFunction(
                this,
                props.storageResources.s3.assetBucket,
                props.storageResources.s3.assetAuxiliaryBucket,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey
            );

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "Conversion3dBasicLambdaExecutionFunctionName", {
            value: pipelineConversion3dBasicLambdaFunction.functionName,
            description: "The 3dBasic Conversion Lambda Function Name to use in a VAMS Pipeline",
            exportName: "Conversion3dBasicLambdaExecutionFunctionName",
        });

        this.pipelineVamsLambdaFunctionName = pipelineConversion3dBasicLambdaFunction.functionName;
    }
}
