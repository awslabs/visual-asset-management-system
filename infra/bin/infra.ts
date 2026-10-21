#!/usr/bin/env node

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { CoreVAMSStack } from "../lib/core-stack";
import { buildWafStacks } from "../lib/cf-waf-stack";
import { AwsSolutionsChecks, NagSuppressions, NIST80053R5Checks } from "cdk-nag";
import { Aspects, Annotations } from "aws-cdk-lib";
import * as Config from "../config/config";
import { STACK_CORE_DESCRIPTION } from "../config/config";
import * as Service from "../lib/helper/service-helper";
import {
    buildBootstrapSynthesizer,
    applyVamsStackRoleCustomization,
} from "../lib/helper/iamRoleCustomization";

const app = new cdk.App();

//Set stack configuration
const config = Config.getConfig(app);
Service.SetConfig(config);

console.log("DEPLOYMENT CONFIGURATION 👉", config);

//Optional IAM role customization for restricted environments (advanced).
//VAMS stack role customization is applied at the App level so the single
//iam-policy-report covers the WAF stack, the core stack, and all nested stacks.
applyVamsStackRoleCustomization(app, config);

if (config.enableCdkNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
}

//Core VAMS stack stackname
const vamsCoreStackName = `${config.name}-core-${config.app.baseStackName}`;
config.env.coreStackName = vamsCoreStackName;

// let ssmWafArn: string = "";

//Deploy with WAF?
const wafStacks = buildWafStacks(app, config);
if (wafStacks) {
    //Core VAMS Stack
    const coreVamsStack = new CoreVAMSStack(app, vamsCoreStackName, {
        stackName: vamsCoreStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        ssmWafArnRegional: wafStacks.regional.wafArn,
        ssmWafArnCloudfront: wafStacks.cloudfront ? wafStacks.cloudfront.wafArn : "",
        config: config,
        description: STACK_CORE_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });

    coreVamsStack.addStackDependency(wafStacks.regional);
    if (wafStacks.cloudfront) {
        coreVamsStack.addStackDependency(wafStacks.cloudfront);
    }

    //Stack level NAG supressions
    if (config.app.govCloud.enabled) {
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
                {
                    // NOTE: cdk-nag >=2.36 removed AwsSolutions-COG3 (AdvancedSecurityMode) and
                    // replaced it with AwsSolutions-COG8 (user pool feature plan / Plus tier).
                    // Without this entry the COG3 suppression above is inert and synthesis fails
                    // with an error on every govCloud/EU Sovereign Cloud deployment.
                    id: "AwsSolutions-COG8",
                    reason: "Cognito Plus tier / advanced security feature plan does not exist in GovCloud or the EU Sovereign Cloud",
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
        ssmWafArnRegional: "",
        ssmWafArnCloudfront: "",
        config: config,
        description: STACK_CORE_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });

    //Stack level NAG supressions
    if (config.app.govCloud.enabled) {
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
                {
                    // NOTE: cdk-nag >=2.36 removed AwsSolutions-COG3 (AdvancedSecurityMode) and
                    // replaced it with AwsSolutions-COG8 (user pool feature plan / Plus tier).
                    // Without this entry the COG3 suppression above is inert and synthesis fails
                    // with an error on every govCloud/EU Sovereign Cloud deployment.
                    id: "AwsSolutions-COG8",
                    reason: "Cognito Plus tier / advanced security feature plan does not exist in GovCloud or the EU Sovereign Cloud",
                },
            ],
            true
        );
    }
}

app.synth();
