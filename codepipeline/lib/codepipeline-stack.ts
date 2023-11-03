/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { CodePipeline, CodePipelineSource, ShellStep } from "aws-cdk-lib/pipelines";
import { CodePipelineStage } from "./codepipeline-stage";
import { NagSuppressions } from "cdk-nag/lib/nag-suppressions";
import { BuildEnvironmentVariableType, BuildSpec, ComputeType } from "aws-cdk-lib/aws-codebuild";

export class CodepipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = props?.env?.region || "us-east-1";
    const stackName = process.env.STACK_NAME || this.node.tryGetContext("stack-name");
    const pipelineName = props?.stackName;
    const dockerDefaultPlatform = process.env.DOCKER_DEFAULT_PLATFORM || "linux/amd64";
    const adminEmailAddress =
        process.env.VAMS_ADMIN_EMAIL || this.node.tryGetContext("adminEmailAddress");
    const repositoryOwner = process.env.REPO_OWNER || this.node.tryGetContext("repo-owner");
    const repository = repositoryOwner + "/visual-asset-management-system";
    const connectionArn =
        process.env.CONNECTION_ARN || this.node.tryGetContext("connection-arn");

        const pipeline = new CodePipeline(this, "Pipeline", {
          // The pipeline name
          pipelineName: pipelineName,
          // How it will be built and synthesized
          synth: new ShellStep("Synth", {
              // Where the source can be found
              input: CodePipelineSource.connection(repository, "codepipeline", {
                  connectionArn: connectionArn, // Created using the AWS console
              }),
              env: {
                  DOCKER_DEFAULT_PLATFORM: "linux/amd64",
                  STACK_NAME: stackName,
                  VAMS_ADMIN_EMAIL: adminEmailAddress,
                  CONNECTION_ARN: connectionArn,
                  REPO_OWNER: repositoryOwner,
              },
              installCommands: ["cd web", "yarn install", "npm run build", "npm run test"],
              // Install dependencies, run tests, build and run cdk synth
              commands: ["cd ../infra", "npm install", "npm run build", "npm install -g esbuild ", "npx cdk synth"],
              primaryOutputDirectory: "infra/cdk.out",
          }),
          codeBuildDefaults: {
              buildEnvironment: {
                  computeType: ComputeType.MEDIUM,
                  privileged: true
              },
          },
          selfMutation: true,
          selfMutationCodeBuildDefaults: {
              buildEnvironment: {
                  environmentVariables: {
                      CONNECTION_ARN: {
                          type: BuildEnvironmentVariableType.PLAINTEXT,
                          value: connectionArn,
                      },
                      REPO_OWNER: {
                          type: BuildEnvironmentVariableType.PLAINTEXT,
                          value: repositoryOwner,
                      },
                  },
              },
              partialBuildSpec: BuildSpec.fromObject({
                  version: "0.2",
                  phases: {
                      build: {
                          commands: [
                              `cd codepipeline && cdk synth`,
                              `cdk -a . deploy ${props?.stackName}  --require-approval=never --verbose --context repo-owner=${repositoryOwner}`
                          ],
                      },
                  },
              }),
          },
      });
      
      // This is where we add the application stages
      // preProd deploys WAF, VAMS and Snowflake stack in dev
      pipeline.addStage(
        new CodePipelineStage(this, "deploy-assets", {
            env: {
                account: process.env.CDK_DEFAULT_ACCOUNT,
                region: region,
            },
        })
      );

      //building pipeline allows CDK nag to access resources that will be deployed by the pipeline
      pipeline.buildPipeline();

      NagSuppressions.addResourceSuppressionsByPath(
        this,
        `/${props?.stackName}/Pipeline/Pipeline/ArtifactsBucket/Resource`,
        [
            {
                id: "AwsSolutions-S1",
                reason: "Need to use the default cdk generated artifacts bucket as is",
            },
        ]
      );

      NagSuppressions.addStackSuppressions(this, [
          {
              id: "AwsSolutions-IAM5",
              reason: "Using service roles created with default permissions by CDK Pipelines with required permissions. Can be customized with role prop.",
          },
          {
              id: "AwsSolutions-CB4",
              reason: "Using default settings from CodePipeline. CodeBuild project will use KMS encryption for artifacts bucket by default.",
          },
      ]);
  }
}
