/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

export class KubectlLayerConstruct extends Construct {
    public readonly layer: lambda.LayerVersion;

    constructor(scope: Construct, id: string) {
        super(scope, id);

        // Create the kubectl binary layer with bundling
        // This layer needs to support both PROVIDED runtimes (for our custom usage)
        // and Python runtimes (for CDK's EKS kubectl provider)
        // We use __dirname as the asset path since we only need bundling (no source files required)
        this.layer = new lambda.LayerVersion(this, "KubectlLayer", {
            code: lambda.Code.fromAsset(
                __dirname, // Use construct directory as dummy asset path
                {
                    bundling: {
                        image: cdk.DockerImage.fromRegistry(
                            "public.ecr.aws/amazonlinux/amazonlinux:2"
                        ),
                        command: [
                            "bash",
                            "-c",
                            [
                                "yum update -y",
                                "yum install -y curl unzip",
                                "mkdir -p /asset-output/bin",
                                "mkdir -p /asset-output/python",
                                "curl -LO https://s3.us-west-2.amazonaws.com/amazon-eks/1.28.1/2023-09-14/bin/linux/amd64/kubectl",
                                "chmod +x kubectl",
                                "mv kubectl /asset-output/bin/",
                                "cp /asset-output/bin/kubectl /asset-output/python/",
                                "echo 'Kubectl binary layer build complete'",
                            ].join(" && "),
                        ],
                        user: "root",
                    },
                }
            ),
            compatibleRuntimes: [
                lambda.Runtime.PROVIDED_AL2,
                lambda.Runtime.PROVIDED_AL2023,
                lambda.Runtime.PYTHON_3_11,
                lambda.Runtime.PYTHON_3_12,
                lambda.Runtime.PYTHON_3_13, // Add Python 3.13 support for EKS kubectl provider
            ],
            description:
                "kubectl binary for EKS cluster operations (supports both PROVIDED and Python runtimes)",
        });
    }
}
