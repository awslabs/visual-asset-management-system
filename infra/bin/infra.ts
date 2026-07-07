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
import { STACK_WAF_DESCRIPTION, STACK_CORE_DESCRIPTION } from "../config/config";
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
const vamsCoreStackName = `${config.name}-core-${
    config.app.baseStackName || process.env.DEMO_LABEL || "dev"
}`;
config.env.coreStackName = vamsCoreStackName;

// let ssmWafArn: string = "";

//Deploy with WAF?
if (config.app.useWaf) {
    const wafBaseName = config.app.baseStackName || process.env.DEPLOYMENT_ENV || "dev";

    // A regional-scoped web ACL is ALWAYS created in the core region when WAF is enabled.
    // It is the ACL that attaches to the API Gateway stage (regional or private) and, for
    // ALB deployments, to the ALB. API Gateway and ALB both require a REGIONAL WAFV2 ACL
    // in the same Region as the resource, so a CloudFront-scoped ACL (us-east-1) cannot
    // protect them.
    //
    // Naming for backwards compatibility: when CloudFront is disabled, the existing WAF
    // stack is already regional and named "{name}-waf-{base}", so the regional stack keeps
    // that exact name (in-place update, no replacement). When CloudFront is enabled, the
    // existing "{name}-waf-{base}" stack is the CloudFront/us-east-1 ACL, so the regional
    // stack is additive under "{name}-waf-regional-{base}".
    const regionalWafStackName = config.app.useCloudFront.enabled
        ? `${config.name}-waf-regional-${wafBaseName}`
        : `${config.name}-waf-${wafBaseName}`;

    const regionalWafStack = new CfWafStack(app, regionalWafStackName, {
        stackName: regionalWafStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        wafScope: WAFScope.REGIONAL,
        wafPolicy: config.wafPolicyJSON,
        description: STACK_WAF_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });

    // CloudFront requires a CLOUDFRONT-scoped ACL in us-east-1. Only created when CloudFront
    // is enabled. Keeps the historical "{name}-waf-{base}" name so existing CloudFront
    // deployments' WAF stack is unchanged (in-place update, no replacement).
    let cloudfrontWafStack: CfWafStack | undefined;
    if (config.app.useCloudFront.enabled) {
        const cloudfrontWafStackName = `${config.name}-waf-${wafBaseName}`;
        cloudfrontWafStack = new CfWafStack(app, cloudfrontWafStackName, {
            stackName: cloudfrontWafStackName,
            env: {
                account: config.env.account,
                region: "us-east-1",
            },
            wafScope: WAFScope.CLOUDFRONT,
            wafPolicy: config.wafPolicyJSON,
            description: STACK_WAF_DESCRIPTION,
            synthesizer: buildBootstrapSynthesizer(config),
        });
    }

    //Core VAMS Stack
    const coreVamsStack = new CoreVAMSStack(app, vamsCoreStackName, {
        stackName: vamsCoreStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        ssmWafArnRegional: regionalWafStack.wafArn,
        ssmWafArnCloudfront: cloudfrontWafStack ? cloudfrontWafStack.wafArn : "",
        config: config,
        description: STACK_CORE_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });

    coreVamsStack.addDependency(regionalWafStack);
    if (cloudfrontWafStack) {
        coreVamsStack.addDependency(cloudfrontWafStack);
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
            ],
            true
        );
    }
}

app.synth();
