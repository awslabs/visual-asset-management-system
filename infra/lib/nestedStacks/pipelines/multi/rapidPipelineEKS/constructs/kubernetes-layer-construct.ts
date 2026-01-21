/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import { layerBundlingCommand } from "../../../../../helper/lambda";

/**
 * Kubernetes Lambda Layer construct for EKS operations.
 * Provides the Kubernetes Python client library for Lambda functions that interact with EKS clusters.
 */
export class KubernetesLambdaLayerConstruct extends Construct {
    public readonly layer: lambda.LayerVersion;

    constructor(scope: Construct, id: string) {
        super(scope, id);

        // Create Kubernetes Lambda layer with Python client dependencies
        this.layer = new lambda.LayerVersion(this, "KubernetesLambdaLayer", {
            code: lambda.Code.fromAsset("../backendPipelines/multi/rapidPipelineEKS/lambdaLayer", {
                bundling: {
                    image: cdk.DockerImage.fromBuild("./config/docker", {
                        file: "Dockerfile-customDependencyBuildConfig",
                        buildArgs: {
                            IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                        },
                    }),
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                    platform: "linux/amd64",
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            description: "Kubernetes Python client Lambda layer for EKS operations",
        });
    }
}
