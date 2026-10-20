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
import { Conversion3dBasicCodeBuildConstruct } from "./conversion3dBasicCodeBuild-construct";

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

        // CodeBuild-based container build (when useCodeBuild is true)
        let codeBuildConstruct: Conversion3dBasicCodeBuildConstruct | undefined;
        if (props.config.app.pipelines.useConversion3dBasic.useCodeBuild === true) {
            codeBuildConstruct = new Conversion3dBasicCodeBuildConstruct(
                this,
                "Conversion3dBasicCodeBuild",
                { config: props.config, kmsKey: props.storageResources.encryption.kmsKey }
            );

            new CfnOutput(this, "Conversion3dBasicCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for the 3D Basic Conversion container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        //Build Lambda VAMS Execution Function
        const pipelineConversion3dBasicLambdaFunction =
            buildVamsExecute3dBasicConversionPipelineFunction(
                this,
                props.storageResources.s3.assetAuxiliaryBucket,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey,
                codeBuildConstruct
                    ? {
                          repository: codeBuildConstruct.repository,
                          tag: codeBuildConstruct.imageTag,
                      }
                    : undefined
            );

        // Lambda resolves the image when it creates or updates the function, so the function waits
        // for the build trigger, which completes only once the build has pushed the image.
        if (codeBuildConstruct) {
            const cfnFunction = pipelineConversion3dBasicLambdaFunction.node
                .defaultChild as cdk.CfnResource;
            cfnFunction.node.addDependency(codeBuildConstruct.buildTrigger);
        }

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "Conversion3dBasicLambdaExecutionFunctionName", {
            value: pipelineConversion3dBasicLambdaFunction.functionName,
            description: "The 3dBasic Conversion Lambda Function Name to use in a VAMS Pipeline",
        });

        this.pipelineVamsLambdaFunctionName = pipelineConversion3dBasicLambdaFunction.functionName;

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
