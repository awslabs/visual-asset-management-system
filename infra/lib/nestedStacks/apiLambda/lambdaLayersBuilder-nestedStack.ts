/* eslint-disable no-useless-escape */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { LAMBDA_PYTHON_RUNTIME } from "../../../config/config";
//import * as pylambda from "@aws-cdk/aws-lambda-python-alpha";
import * as lambda from "aws-cdk-lib/aws-lambda";

export type LambdaLayersBuilderNestedStackProps = cdk.StackProps;

/**
 * Default input properties
 */
const defaultProps: Partial<LambdaLayersBuilderNestedStackProps> = {};

export class LambdaLayersBuilderNestedStack extends NestedStack {
    public lambdaCommonBaseLayer: lambda.LayerVersion;
    public lambdaCommonServiceSDKLayer: lambda.LayerVersion;

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
                    command: ["bash", "-c", this.layerBundlingCommand()],
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        //Deploy Common Service SDK Lambda Layer ../backend/lambdaLayers/serviceSDK
        this.lambdaCommonServiceSDKLayer = new lambda.LayerVersion(this, "VAMSLayerServiceSDK", {
            layerVersionName: "vams_layer_servicesdk",
            code: lambda.Code.fromAsset("../backend/lambdaLayers/serviceSDK", {
                bundling: {
                    image: cdk.DockerImage.fromBuild("./config/docker", {
                        file: "Dockerfile-customDependencyBuildConfig",
                        buildArgs: {
                            IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                        },
                    }),
                    user: "root",
                    command: ["bash", "-c", this.layerBundlingCommand()],
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });
    }

    layerBundlingCommand(): string {
        //Command to install layer dependencies from poetry files and remove unneeded cache, tests, and boto libraries (automatically comes with lambda python container base)
        //Note: The removals drastically reduce layer sizes
        return [
            "pip install --upgrade pip",
            "pip install poetry",
            "poetry export --without-hashes --format=requirements.txt > requirements.txt",
            "pip install -r requirements.txt -t /asset-output/python",
            "rsync -rLv ./ /asset-output/python",
            "cd /asset-output",
            "find . -type d -name __pycache__ -prune -exec rm -rf {} +",
            "find . -type d -name tests -prune -exec rm -rf {} +",
            //'find . -type d -name *boto* -prune -exec rm -rf {} +' //Exclude for now to not break version dependency chain
        ].join(" && ");
    }
}
