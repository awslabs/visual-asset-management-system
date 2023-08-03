#!/usr/bin/env node
import { DeploymentPipeline, Platform } from "@amzn/pipelines";
import { App } from "aws-cdk-lib";

// Set up your CDK App
const app = new App();

const applicationAccount = "617744503547";

const pipeline = new DeploymentPipeline(app, "Pipeline", {
    account: applicationAccount,
    pipelineName: "VAMSComment",
    versionSet: "VAMSComment/development", // The version set you created
    versionSetPlatform: Platform.AL2_X86_64,
    trackingVersionSet: "live", // Or any other version set you prefer
    bindleGuid: "amzn1.bindle.resource.igbc4hweobabuc6sqh7q",
    description: "Simple CDK Pipeline",
    pipelineId: "5688982",
    selfMutate: true,
});
