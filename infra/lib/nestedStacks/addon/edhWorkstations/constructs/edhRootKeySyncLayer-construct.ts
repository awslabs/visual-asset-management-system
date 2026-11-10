/*
 * The EDH Workstations add-on's root key sync layer: `cryptography` and its runtime dependencies only.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import { layerBundlingCommand } from "../../../../helper/lambda";

/** The layer's Poetry project, relative to `infra/` as every VAMS layer asset is. */
export const EDH_ROOT_KEY_SYNC_LAYER_PROJECT = "../backendAddons/edh/rootKeySync/lambdaLayer";

/**
 * The root key sync generates an RSA key pair for each run with `cryptography`, which the base layer does not
 * carry. This layer holds `cryptography`, `cffi` and `pycparser` only, pinned in its own Poetry project to the
 * versions the core authorizer layer uses, and none of the base layer's packages, so the two layers the sync
 * carries share no package file or package metadata. It is bundled exactly as the core layers are
 * (`lambdaLayersBuilder-nestedStack.ts`): the same build image, `layerBundlingCommand`, linux/amd64 platform
 * and runtime. Built only while the sync is on, and attached to the sync function alone.
 */
export class EdhRootKeySyncLayerConstruct extends Construct {
    public readonly layer: lambda.LayerVersion;

    constructor(scope: Construct, id: string) {
        super(scope, id);

        this.layer = new lambda.LayerVersion(this, "CryptographyLayer", {
            code: lambda.Code.fromAsset(EDH_ROOT_KEY_SYNC_LAYER_PROJECT, {
                bundling: {
                    image: cdk.DockerImage.fromBuild("./config/docker", {
                        file: "Dockerfile-customDependencyBuildConfig",
                        buildArgs: {
                            IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                        },
                    }),
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                    platform: "linux/amd64", //Fix to the LINUX_AMD64 platform to standardize instruction set across all loads
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            description: "cryptography for the VAMS EDH Workstations root key sync",
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });
    }
}
