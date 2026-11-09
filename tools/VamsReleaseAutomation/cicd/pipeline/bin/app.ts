#!/usr/bin/env node
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { App, Aspects } from "aws-cdk-lib";
import { AwsSolutionsChecks } from "cdk-nag";
import { loadConfig } from "../lib/config";
import { VraPipelineStack } from "../lib/vra-pipeline-stack";

const app = new App();
const configPath: string = app.node.tryGetContext("vraPipelineConfig") ?? "config/pipeline.json";
const config = loadConfig(configPath);
// `vra pipeline plan` synthesizes once more with -c vraIamReport=true: cdk.out/iam-policy-report.json lists every
// role this stack creates (the IT package). That assembly is for reading only: its role ARNs are placeholders.
const iamReport = String(app.node.tryGetContext("vraIamReport") ?? "false") === "true";
if (iamReport) config.iamRoles = { ...(config.iamRoles ?? {}), mode: "report" };

new VraPipelineStack(app, `${config.prefix ?? "vra"}-pipeline`, {
    config,
    env: { account: config.toolsAccount, region: config.region },
    description:
        "vams-release-automation optional CI/CD lane (CodePipeline + CodeBuild wrapping the vra CLI)",
    tags: { "vra:managed": "true" },
});

// cdk-nag AwsSolutions pack; findings fail synth. Suppressions live next to the resource with a reason.
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
