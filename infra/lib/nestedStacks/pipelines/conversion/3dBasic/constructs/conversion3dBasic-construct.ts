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
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import { layerBundlingCommand } from "../../../../../helper/lambda";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";

export interface Conversion3dBasicConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
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

        //Build Lambda VAMS Execution Function
        const pipelineConversion3dBasicLambdaFunction =
            buildVamsExecute3dBasicConversionPipelineFunction(
                this,
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
        });

        this.pipelineVamsLambdaFunctionName = pipelineConversion3dBasicLambdaFunction.functionName;

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables). The
        // four former per-output-format registrations collapse to ONE pipeline + one template per target
        // format (obj/stl/gltf/glb); the target format is selected per execution via the template.
        if (props.config.app.pipelines.useConversion3dBasic.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "Conversion3dBasicRegistration", {
                importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
                vamsSchemaDir: path.join(
                    __dirname,
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendPipelines",
                    "conversion",
                    "3dBasic",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName: pipelineConversion3dBasicLambdaFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "conversion-3d-basic",
                    workflowId: "conversion-3d-basic",
                },
            });
        }
    }
}
