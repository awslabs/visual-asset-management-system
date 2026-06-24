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
import * as lambda from "aws-cdk-lib/aws-lambda";
import { execSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

export type LambdaLayersBuilderNestedStackProps = cdk.StackProps;

/**
 * Default input properties
 */
const defaultProps: Partial<LambdaLayersBuilderNestedStackProps> = {};

/**
 * Check if Docker is available on the system.
 * Used to decide whether to use DockerImage.fromBuild (which spawns Docker
 * at construction time) or a placeholder image for local-only bundling.
 */
function isDockerAvailable(): boolean {
    try {
        execSync("docker --version", { stdio: "ignore" });
        return true;
    } catch {
        return false;
    }
}

/**
 * Local bundling implementation that installs Lambda layer dependencies
 * without Docker by using pip with --platform and --only-binary flags
 * to download pre-built manylinux wheels for Lambda's x86_64 runtime.
 *
 * Requires: pip (any version) and an existing requirements.txt in the
 * layer source directory. Poetry is NOT required — the checked-in
 * requirements.txt is used directly.
 */
class LocalLayerBundling implements cdk.ILocalBundling {
    private readonly layerSourcePath: string;

    constructor(layerSourcePath: string) {
        this.layerSourcePath = layerSourcePath;
    }

    tryBundle(outputDir: string, _options: cdk.BundlingOptions): boolean {
        const sourcePath = path.resolve(this.layerSourcePath);
        const reqFile = path.join(sourcePath, "requirements.txt");

        if (!fs.existsSync(reqFile)) {
            console.log(
                `Local bundling: No requirements.txt in ${sourcePath}, ` +
                    `falling back to Docker.`
            );
            return false;
        }

        try {
            const pythonOutputDir = path.join(outputDir, "python");
            fs.mkdirSync(pythonOutputDir, { recursive: true });

            // Install dependencies for Linux x86_64 using pre-built wheels.
            // Use manylinux_2_28 (Lambda runtime glibc 2.28+) for packages
            // like cryptography that only publish newer manylinux wheels.
            execSync(
                `pip install -r "${reqFile}" ` +
                    `--platform manylinux_2_28_x86_64 ` +
                    `--platform manylinux2014_x86_64 ` +
                    `--implementation cp ` +
                    `--python-version 3.12 ` +
                    `--only-binary=:all: ` +
                    `--target "${pythonOutputDir}" ` +
                    `--upgrade --quiet`,
                { cwd: sourcePath, stdio: "inherit" }
            );

            // Second pass: install pure-Python packages that have no
            // pre-built manylinux wheels (they are platform-independent).
            // Do NOT use --upgrade here — it would overwrite the correct
            // Linux binaries from pass 1 with host-platform (Windows) builds.
            execSync(
                `pip install -r "${reqFile}" ` +
                    `--target "${pythonOutputDir}" ` +
                    `--quiet --no-deps`,
                { cwd: sourcePath, stdio: "inherit" }
            );

            // Copy source files into the layer output (mirrors Docker rsync)
            const copySource = (src: string, dest: string) => {
                if (!fs.existsSync(src)) return;
                const entries = fs.readdirSync(src, { withFileTypes: true });
                for (const entry of entries) {
                    const srcPath = path.join(src, entry.name);
                    const destPath = path.join(dest, entry.name);
                    if (entry.name === "__pycache__" || entry.name === "tests")
                        continue;
                    if (entry.name === "requirements.txt") continue;
                    if (entry.name === "poetry.lock") continue;
                    if (entry.name === "pyproject.toml") continue;
                    if (entry.isDirectory()) {
                        fs.mkdirSync(destPath, { recursive: true });
                        copySource(srcPath, destPath);
                    } else {
                        fs.copyFileSync(srcPath, destPath);
                    }
                }
            };
            copySource(sourcePath, pythonOutputDir);

            console.log(
                `Local bundling succeeded for layer: ${this.layerSourcePath}`
            );
            return true;
        } catch (e) {
            console.log(
                `Local bundling failed for ${this.layerSourcePath}, ` +
                    `falling back to Docker: ${e}`
            );
            return false;
        }
    }
}

/**
 * Returns the Docker bundling image for Lambda layers.
 * When Docker is not available, returns a placeholder image (local bundling
 * handles the actual work). DockerImage.fromBuild() is only called when
 * Docker exists because it spawns `docker build` at construction time.
 */
function getLayerBundlingImage(): cdk.DockerImage {
    if (isDockerAvailable()) {
        return cdk.DockerImage.fromBuild("./config/docker", {
            file: "Dockerfile-customDependencyBuildConfig",
            buildArgs: {
                IMAGE: LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
            },
        });
    }
    return cdk.DockerImage.fromRegistry(
        LAMBDA_PYTHON_RUNTIME.bundlingImage.image
    );
}

export class LambdaLayersBuilderNestedStack extends NestedStack {
    public lambdaCommonBaseLayer: lambda.LayerVersion;
    public lambdaAuthorizerLayer: lambda.LayerVersion;

    constructor(parent: Construct, name: string, props: LambdaLayersBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const bundlingImage = getLayerBundlingImage();

        //Deploy Common Base Lambda Layer
        this.lambdaCommonBaseLayer = new lambda.LayerVersion(this, "VAMSLayerBase", {
            layerVersionName: "vams_layer_base",
            code: lambda.Code.fromAsset("../backend/lambdaLayers/base", {
                bundling: {
                    local: new LocalLayerBundling("../backend/lambdaLayers/base"),
                    image: bundlingImage,
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                    platform: "linux/amd64",
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
                    local: new LocalLayerBundling("../backend/lambdaLayers/authorizer"),
                    image: bundlingImage,
                    user: "root",
                    command: ["bash", "-c", layerBundlingCommand()],
                    platform: "linux/amd64",
                },
            }),
            compatibleRuntimes: [LAMBDA_PYTHON_RUNTIME],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });
    }
}
