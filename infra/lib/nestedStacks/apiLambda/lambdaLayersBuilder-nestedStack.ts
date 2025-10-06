/* eslint-disable no-useless-escape */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { LAMBDA_PYTHON_RUNTIME } from "../../../config/config";
import { layerBundlingCommand } from "../../helper/lambda";
//import * as pylambda from "@aws-cdk/aws-lambda-python-alpha";
import * as lambda from "aws-cdk-lib/aws-lambda";

export type LambdaLayersBuilderNestedStackProps = cdk.StackProps;

/**
 * Default input properties
 */
const defaultProps: Partial<LambdaLayersBuilderNestedStackProps> = {};

export class LambdaLayersBuilderNestedStack extends NestedStack {
    public lambdaCommonBaseLayer: lambda.LayerVersion;
    public lambdaAuthorizerLayer: lambda.LayerVersion;

    constructor(parent: Construct, name: string, props: LambdaLayersBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Deploy Common Base Lambda Layer
        this.lambdaCommonBaseLayer = new lambda.LayerVersion(this, "VAMSLayerBase", {
            layerVersionName: "vams_layer_base",
            code: lambda.Code.fromAsset("../backend/lambdaLayers/base", {
                bundling: {
                    image: cdk.DockerImage.fromBuild("./config/docker", {
                        file: "Dockerfile-customDependencyBuildConfig",
                        buildArgs: {
                            IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                        },
                    }),
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        //Deploy Authorizer Lambda Layer ../backend/lambdaLayers/authorizer
        this.lambdaAuthorizerLayer = new lambda.LayerVersion(this, "VAMSLayerAuthorizer", {
            layerVersionName: "vams_layer_authorizer",
            code: lambda.Code.fromAsset("../backend/lambdaLayers/authorizer", {
                bundling: {
                    image: cdk.DockerImage.fromBuild("./config/docker", {
                        file: "Dockerfile-customDependencyBuildConfig",
                        buildArgs: {
                            IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                        },
                    }),
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });
    }
}
