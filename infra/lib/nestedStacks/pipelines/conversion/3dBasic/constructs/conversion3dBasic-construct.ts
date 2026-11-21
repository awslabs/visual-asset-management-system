/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { buildVamsExecute3dBasicConversionPipelineFunction } from "../lambdaBuilder/conversion3dBasicFunctions";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../../../../config/config";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import {
    Conversion3dBasicBatchTier,
    CONVERSION_3D_BASIC_REGISTERED_TASK_TIMEOUT_SECONDS,
} from "./conversion3dBasicBatchTier-construct";

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

        // With the Batch tier on, the function the workflow calls is the launcher: it starts the state
        // machine, which runs the converter Lambda above first and continues a refused run on AWS Batch.
        // With it off nothing below is created and the registration is what it was.
        const batchTierEnabled = props.config.app.pipelines.useConversion3dBasic.batchTier.enabled;
        let registeredFunctionName = pipelineConversion3dBasicLambdaFunction.functionName;
        if (batchTierEnabled) {
            const batchTier = new Conversion3dBasicBatchTier(this, "BatchTier", {
                config: props.config,
                storageResources: props.storageResources,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                converterFunction: pipelineConversion3dBasicLambdaFunction,
            });
            registeredFunctionName = batchTier.launcherFunction.functionName;
            this.pipelineVamsLambdaFunctionName = registeredFunctionName;
            new CfnOutput(this, "Conversion3dBasicLauncherFunctionName", {
                value: registeredFunctionName,
                description:
                    "The 3dBasic Conversion launcher Lambda Function Name (Batch tier) to use in a VAMS Pipeline",
            });
        }

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables). One
        // pipeline with one template per target format (obj/stl/gltf/glb); the target format is
        // selected per execution via the template.
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
                    lambdaName: registeredFunctionName,
                },
                // The launcher answers through a task token, so the pipeline is registered to wait for
                // the callback, for as long as the Batch tier's state machine may run.
                executionConfigOverrides: batchTierEnabled
                    ? {
                          waitForCallback: "Enabled",
                          taskTimeout: CONVERSION_3D_BASIC_REGISTERED_TASK_TIMEOUT_SECONDS,
                      }
                    : undefined,
                idOverrides: {
                    pipelineId: "conversion-3d-basic",
                    workflowId: "conversion-3d-basic",
                },
            });
        }
    }
}
