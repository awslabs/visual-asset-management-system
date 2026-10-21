/**
 * Copyright 2022 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   http://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
    WafScopedRuleExclusion,
} from "./constructs/wafv2-basic-construct";
import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as Config from "../config/config";
import { STACK_WAF_DESCRIPTION } from "../config/config";
import { buildBootstrapSynthesizer } from "./helper/iamRoleCustomization";
import { addonWafRuleExclusions } from "./nestedStacks/addon/addonWafExclusions";

interface EnvProps {
    env?: cdk.Environment;
    stackName: string;
    wafScope?: WAFScope;
    wafPolicy?: WafPolicyConfig;
    scopedRuleExclusions?: WafScopedRuleExclusion[];
    description: string;
    synthesizer?: cdk.IStackSynthesizer;
}

export class CfWafStack extends cdk.Stack {
    public ssmWafArnParameterName: string;
    public wafArn: string;

    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, { ...props, crossRegionReferences: true });

        // ssm parameter name must be unique in a region
        this.ssmWafArnParameterName = "waf_acl_arn_" + this.stackName;

        const wafv2CF = new Wafv2BasicConstruct(this, "Wafv2CF", {
            ...props,
            wafScope: props.wafScope,
            wafPolicy: props.wafPolicy,
            scopedRuleExclusions: props.scopedRuleExclusions,
        });

        new cdk.aws_ssm.StringParameter(this, "waf_acl_arn", {
            parameterName: this.ssmWafArnParameterName,
            description: "WAF ACL ARN",
            stringValue: wafv2CF.webacl.attrArn,
        });

        this.wafArn = wafv2CF.webacl.attrArn;
    }
}

export interface WafStacks {
    regional: CfWafStack;
    cloudfront?: CfWafStack;
}

/**
 * The web ACL stacks of a deployment with WAF enabled, or undefined with WAF off. The regional ACL
 * protects the API stage and, with the ALB, the load balancer; the CloudFront ACL exists only with
 * CloudFront and protects the distribution.
 */
export function buildWafStacks(app: cdk.App, config: Config.Config): WafStacks | undefined {
    if (!config.app.useWaf) {
        return undefined;
    }
    const wafBaseName = config.app.baseStackName;

    // Managed rules relaxed on add-on API routes only, in the regional ACL on the API stage.
    // Clients call the stage directly, and CloudFront's /api/* behavior forwards under the
    // stage's own /api routes, so no add-on route is reachable through the CloudFront ACL.
    const wafScopedRuleExclusions = addonWafRuleExclusions(config);

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

    const regional = new CfWafStack(app, regionalWafStackName, {
        stackName: regionalWafStackName,
        env: {
            account: config.env.account,
            region: config.env.region,
        },
        wafScope: WAFScope.REGIONAL,
        wafPolicy: config.wafPolicyJSON,
        scopedRuleExclusions: wafScopedRuleExclusions,
        description: STACK_WAF_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });

    // CloudFront requires a CLOUDFRONT-scoped ACL in us-east-1. Only created when CloudFront
    // is enabled. Keeps the historical "{name}-waf-{base}" name so existing CloudFront
    // deployments' WAF stack is unchanged (in-place update, no replacement).
    let cloudfront: CfWafStack | undefined;
    if (config.app.useCloudFront.enabled) {
        const cloudfrontWafStackName = `${config.name}-waf-${wafBaseName}`;
        cloudfront = new CfWafStack(app, cloudfrontWafStackName, {
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

    return { regional, cloudfront };
}
