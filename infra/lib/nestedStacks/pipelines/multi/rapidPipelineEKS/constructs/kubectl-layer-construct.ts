/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

/**
 * The kubectl download for a given EKS cluster minor version.
 *
 * Each entry is a verified `<version>/<release-date>` path under the EKS S3 bucket. The date is part of
 * the published path and differs per release, so it cannot be derived from the version — a mismatched
 * pair answers 404 and fails the layer build rather than falling back.
 *
 * kubectl supports one minor version of skew either side of the API server, so the binary has to track
 * the configured cluster version. An unmapped version throws at synth: failing there names the config
 * field, whereas letting the download 404 fails inside a Docker bundling step with no indication of why.
 */
export const KUBECTL_RELEASE_PATHS: Record<string, string> = {
    "1.28": "1.28.1/2023-09-14",
    "1.29": "1.29.0/2024-01-04",
    "1.30": "1.30.0/2024-05-12",
    "1.31": "1.31.0/2024-09-12",
    "1.32": "1.32.0/2024-12-20",
};

export function kubectlDownloadUrl(eksClusterVersion: string): string {
    const releasePath = KUBECTL_RELEASE_PATHS[eksClusterVersion];
    if (!releasePath) {
        throw new Error(
            `Configuration Error: app.pipelines.useRapidPipeline.useEks.eksClusterVersion ` +
                `"${eksClusterVersion}" has no verified kubectl download. Add its ` +
                `"<version>/<release-date>" path to KUBECTL_RELEASE_PATHS in ` +
                `kubectl-layer-construct.ts, or set a supported version ` +
                `(${Object.keys(KUBECTL_RELEASE_PATHS).join(", ")}).`
        );
    }
    return `https://s3.us-west-2.amazonaws.com/amazon-eks/${releasePath}/bin/linux/amd64/kubectl`;
}

export class KubectlLayerConstruct extends Construct {
    public readonly layer: lambda.LayerVersion;

    /**
     * @param eksClusterVersion the cluster's Kubernetes version, e.g. "1.31". kubectl supports one
     * minor version of skew either side of the API server, so the binary tracks the cluster rather
     * than carrying a fixed version that drifts as the cluster is upgraded.
     */
    constructor(scope: Construct, id: string, eksClusterVersion: string) {
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
                                // Downloaded from the commercial S3 endpoint: the EKS kubectl binaries
                                // are not published to the GovCloud, EU Sovereign or ISO partition
                                // endpoints, so a restricted-partition build host cannot reach this.
                                `curl -fLO ${kubectlDownloadUrl(eksClusterVersion)}`,
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
