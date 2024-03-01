#!/usr/bin/env node

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { AwsSolutionsChecks } from "cdk-nag";
import { Aspects } from "aws-cdk-lib";
import { CodePipelineStack } from "../lib/codepipeline";

const app = new cdk.App();
const region = process.env.AWS_REGION || "us-east-1";

/** development variables **/
const enableCdkNag = true;

if (enableCdkNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
}

new CodePipelineStack(app, `vams-code-pipeline-${process.env.DEPLOYMENT_ENV || "dev"}`, {
    stackName: `vams-code-pipeline-${process.env.DEPLOYMENT_ENV || "dev"}`,
    env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: region },
});

app.synth();
