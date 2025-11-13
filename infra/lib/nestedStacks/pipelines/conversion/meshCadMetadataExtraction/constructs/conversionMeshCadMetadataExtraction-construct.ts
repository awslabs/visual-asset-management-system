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
import { buildVamsExecuteMeshCadMetadataExtractionPipelineFunction } from "../lambdaBuilder/conversionMeshCadMetadataExtractionFunctions";
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

export interface ConversionMeshCadMetadataExtractionConstructProps extends cdk.StackProps {
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
const defaultProps: Partial<ConversionMeshCadMetadataExtractionConstructProps> = {
    //stackName: "",
    //env: {},
};

export class ConversionMeshCadMetadataExtractionConstruct extends NestedStack {
    public pipelineVamsLambdaFunctionName = "";

    constructor(
        parent: Construct,
        name: string,
        props: ConversionMeshCadMetadataExtractionConstructProps
    ) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        //Build Lambda VAMS Execution Function
        const pipelineConversionMeshCadMetadataExtractionLambdaFunction =
            buildVamsExecuteMeshCadMetadataExtractionPipelineFunction(
                this,
                props.storageResources.s3.assetAuxiliaryBucket,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey
            );

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "ConversionMeshCadMetadataExtractionLambdaExecutionFunctionName", {
            value: pipelineConversionMeshCadMetadataExtractionLambdaFunction.functionName,
            description:
                "The Mesh/Cad Metadata Extraction Lambda Function Name to use in a VAMS Pipeline",
        });

        this.pipelineVamsLambdaFunctionName =
            pipelineConversionMeshCadMetadataExtractionLambdaFunction.functionName;

        // Create custom resource to automatically register pipeline and workflow
        if (
            props.config.app.pipelines.useConversionCadMeshMetadataExtraction
                .autoRegisterWithVAMS === true
        ) {
            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:aws:lambda:${region}:${account}:function:${props.importGlobalPipelineWorkflowFunctionName}`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });
            const currentTimestamp = new Date().toISOString();

            // Register meshCad metadata extraction  pipeline and workflow
            new cdk.CustomResource(this, "ConversionMetadataExtractionCadMeshPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "metadata-extraction-cad-mesh",
                    pipelineDescription:
                        "Basic Metadata Attribute Extraction (File-level metadata) - using Trimesh and CADQuery library. Supported files are STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ, STP, DXF.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".all",
                    waitForCallback: "Disabled", // Synchronous pipeline
                    lambdaName:
                        pipelineConversionMeshCadMetadataExtractionLambdaFunction.functionName,
                    taskTimeout: "900", // 15 minutes (lambda limit)
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "metadata-extraction-cad-mesh",
                    workflowDescription:
                        "Basic Metadata Attribute Extraction (File-level metadata) - using Trimesh and CADQuery library. Supported files are STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, XYZ, STP, DXF.",
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
