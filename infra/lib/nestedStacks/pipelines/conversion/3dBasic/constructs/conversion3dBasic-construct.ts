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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";

export interface Conversion3dBasicConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
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

        // Create custom resource to automatically register pipeline and workflow
        if (props.config.app.pipelines.useConversion3dBasic.autoRegisterWithVAMS === true) {
            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:aws:lambda:${region}:${account}:function:${props.importGlobalPipelineWorkflowFunctionName}`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });
            const currentTimestamp = new Date().toISOString();

            // Register STL to OBJ conversion pipeline and workflow
            new cdk.CustomResource(this, "Conversion3dBasicStlToObjPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "conversion-3d-basic-to-obj",
                    pipelineDescription:
                        "3D Basic Conversion Pipeline - X to OBJ format conversion using Trimesh library. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".obj",
                    waitForCallback: "Disabled", // Synchronous pipeline
                    lambdaName: pipelineConversion3dBasicLambdaFunction.functionName,
                    taskTimeout: "900", // 15 minutes (lambda limit)
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "conversion-3d-basic-to-obj",
                    workflowDescription:
                        "Automated workflow for X to OBJ conversion using 3D Basic Conversion Pipeline. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    autoTriggerOnFileExtensionsUpload: "",
                },
            });

            // Register OBJ to STL conversion pipeline and workflow
            new cdk.CustomResource(this, "Conversion3dBasicObjToStlPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "conversion-3d-basic-to-stl",
                    pipelineDescription:
                        "3D Basic Conversion Pipeline -  X to STL format conversion using Trimesh library. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".stl",
                    waitForCallback: "Disabled", // Synchronous pipeline
                    lambdaName: pipelineConversion3dBasicLambdaFunction.functionName,
                    taskTimeout: "900", // 15 minutes (lambda limit)
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "conversion-3d-basic-to-stl",
                    workflowDescription:
                        "Automated workflow for  X to STL conversion using 3D Basic Conversion Pipeline. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    autoTriggerOnFileExtensionsUpload: "",
                },
            });

            // Register PLY to GLTF conversion pipeline and workflow
            new cdk.CustomResource(this, "Conversion3dBasicPlyToGltfPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "conversion-3d-basic-to-gltf",
                    pipelineDescription:
                        "3D Basic Conversion Pipeline - X to GLTF format conversion using Trimesh library. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".gltf",
                    waitForCallback: "Disabled", // Synchronous pipeline
                    lambdaName: pipelineConversion3dBasicLambdaFunction.functionName,
                    taskTimeout: "900", // 15 minutes (lambda limit)
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "conversion-3d-basic-to-gltf",
                    workflowDescription:
                        "Automated workflow for X to GLTF conversion using 3D Basic Conversion Pipeline. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    autoTriggerOnFileExtensionsUpload: "",
                },
            });

            // Register GLTF to GLB conversion pipeline and workflow
            new cdk.CustomResource(this, "Conversion3dBasicGltfToGlbPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "conversion-3d-basic-to-glb",
                    pipelineDescription:
                        "3D Basic Conversion Pipeline -  X to GLB format conversion using Trimesh library. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".glb",
                    waitForCallback: "Disabled", // Synchronous pipeline
                    lambdaName: pipelineConversion3dBasicLambdaFunction.functionName,
                    taskTimeout: "900", // 15 minutes (lambda limit)
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "conversion-3d-basic-to-glb",
                    workflowDescription:
                        "Automated workflow for X to GLB conversion using 3D Basic Conversion Pipeline. X can be STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ.",
                    autoTriggerOnFileExtensionsUpload: "",
                },
            });

            //Nag supression
            NagSuppressions.addResourceSuppressions(
                importProvider,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "* Wildcard permissions needed for pipelineWorkflow lambda import and execution for custom resource",
                    },
                ],
                true
            );
        }
    }
}
