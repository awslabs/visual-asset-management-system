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
import { buildVamsExecuteMeshCadMetadataExtractionPipelineFunction } from "../lambdaBuilder/conversionMeshCadMetadataExtractionFunctions";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../../../../config/config";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";

export interface ConversionMeshCadMetadataExtractionConstructProps extends cdk.StackProps {
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

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables).
        if (
            props.config.app.pipelines.useConversionCadMeshMetadataExtraction
                .autoRegisterWithVAMS === true
        ) {
            new VamsSchemaRegistration(this, "MeshCadMetadataExtractionRegistration", {
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
                    "meshCadMetadataExtraction",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName:
                        pipelineConversionMeshCadMetadataExtractionLambdaFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "metadata-extraction-cad-mesh",
                    workflowId: "metadata-extraction-cad-mesh",
                },
                triggerEnabled:
                    props.config.app.pipelines.useConversionCadMeshMetadataExtraction
                        .autoRegisterAutoTriggerOnFileUpload === true,
            });
        }
    }
}
