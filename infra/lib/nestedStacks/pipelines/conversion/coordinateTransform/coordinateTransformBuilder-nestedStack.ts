/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as Config from "../../../../../config/config";
import {
    CoordinateTransformConstruct,
    CoordinateTransformConstructProps,
} from "./constructs/coordinateTransform-construct";

export interface CoordinateTransformBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    assetAuxiliaryBucket: s3.IBucket;
    kmsKey?: kms.IKey;
    importGlobalPipelineWorkflowFunctionName: string;
}

export class CoordinateTransformBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(
        parent: Construct,
        name: string,
        props: CoordinateTransformBuilderNestedStackProps
    ) {
        super(parent, name);

        const coordinateTransformPipeline = new CoordinateTransformConstruct(
            this,
            "CoordinateTransformPipeline",
            {
                config: props.config,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                assetAuxiliaryBucket: props.assetAuxiliaryBucket,
                kmsKey: props.kmsKey,
                importGlobalPipelineWorkflowFunctionName:
                    props.importGlobalPipelineWorkflowFunctionName,
            }
        );

        this.pipelineVamsLambdaFunctionName =
            coordinateTransformPipeline.pipelineVamsLambdaFunctionName;
    }
}
