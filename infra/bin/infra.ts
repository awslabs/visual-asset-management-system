#!/usr/bin/env node

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { CoreVAMSStack } from "../lib/core-stack";
import { CfWafStack } from "../lib/cf-waf-stack";
import { AwsSolutionsChecks, NagSuppressions, NIST80053R5Checks } from "cdk-nag";
import { Aspects, Annotations } from "aws-cdk-lib";
import { WAFScope } from "../lib/constructs/wafv2-basic-construct";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";

const app = new cdk.App();

//Set stack configuration
const config = Config.getConfig(app);
Service.SetConfig(config);

console.log("DEPLOYMENT CONFIGURATION 👉", config);

if (config.enableCdkNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
}

//Core VAMS stack stackname
const vamsCoreStackName = `${config.name}-core-${
    config.app.baseStackName || process.env.DEMO_LABEL || "dev"
}`;
config.env.coreStackName = vamsCoreStackName;

// let ssmWafArn: string = "";

//Deploy with WAF?
if (config.app.useWaf) {
    //Deploy web access firewall to us-east-1 for cloudfront or in-region for non-cloudfront (ALB) deployments
    const wafRegion = config.app.useAlb.enabled ? config.env.region : "us-east-1";
    const wafScope = config.app.useAlb.enabled ? WAFScope.REGIONAL : WAFScope.CLOUDFRONT;

    //Web access firewall stackname
    const wafStackName = `${config.name}-waf-${
        config.app.baseStackName || process.env.DEPLOYMENT_ENV || "dev"
    }`;

    //WAF Stack
    const cfWafStack = new CfWafStack(app, wafStackName, {
        stackName: wafStackName,
        env: {
            account: config.env.account,
            region: wafRegion,
        },
        wafScope: wafScope,
    });

    // ssmWafArn = cfWafStack.wafArn;

    //Core VAMS Stack
    const coreVamsStack = new CoreVAMSStack(app, vamsCoreStackName, {
        stackName: vamsCoreStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        ssmWafArn: cfWafStack.wafArn,
        config: config,
    });

    coreVamsStack.addDependency(cfWafStack);

    //Stack level NAG supressions
    if (config.app.govCloud) {
        // Enable checks for NIST 800-53 R5
        // TODO: RE-ENABLE WHEN WORKING THROUGH ISSUES
        // Aspects.of(app).add(new NIST80053R5Checks({verbose: true}));

        // Feature check suppression
        NagSuppressions.addStackSuppressions(
            coreVamsStack,
            [
                {
                    id: "AwsSolutions-COG3",
                    reason: "Cognito AdvancedSecurityMode feature does not exist in GovCloud",
                },
            ],
            true
        );
    }
} //No Waf
else {
    const coreVamsStack = new CoreVAMSStack(app, vamsCoreStackName, {
        stackName: vamsCoreStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        ssmWafArn: "",
        config: config,
    });

    //Stack level NAG supressions
    if (config.app.govCloud) {
        // Enable checks for NIST 800-53 R5
        // TODO: RE-ENABLE WHEN WORKING THROUGH ISSUES
        // Aspects.of(app).add(new NIST80053R5Checks({verbose: true}));

        // Feature check suppression
        NagSuppressions.addStackSuppressions(
            coreVamsStack,
            [
                {
                    id: "AwsSolutions-COG3",
                    reason: "Cognito AdvancedSecurityMode feature does not exist in GovCloud",
                },
            ],
            true
        );
    }
}

app.synth();
