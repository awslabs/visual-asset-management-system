/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { Construct } from "constructs";
export enum WAFScope {
    CLOUDFRONT = "CLOUDFRONT",
    REGIONAL = "REGIONAL",
}

/**
 * WAF policy configuration loaded from config/policy/wafPolicyConfig.json.
 * When absent (undefined), the construct applies the legacy default rules.
 */
export interface WafPolicyConfig {
    managedRuleGroups?: Array<{
        name: string;
        vendorName: string;
        managedRuleGroupName: string;
        priority: number;
        block?: boolean; // true => the group's own block actions apply; false => count-only
    }>;
    rateBasedRules?: Array<{
        name: string;
        priority: number;
        limit: number; // requests per 5-minute window per aggregate key
        aggregateKeyType?: string; // "IP" (default) or "FORWARDED_IP"
    }>;
}

export interface Wafv2BasicConstructProps extends cdk.StackProps {
    readonly wafScope?: WAFScope;
    readonly rules?: Array<wafv2.CfnWebACL.RuleProperty | cdk.IResolvable> | cdk.IResolvable;
    readonly wafPolicy?: WafPolicyConfig;
    readonly stackName?: string;
    readonly env?: cdk.Environment;
}

/**
 * Legacy default rules: a single AWS Common Rule Set in count-only mode. Applied only
 * when no wafPolicy config is supplied, preserving the historical behavior for existing
 * deployments that do not ship a config/policy/wafPolicyConfig.json.
 */
const legacyDefaultRules: Array<wafv2.CfnWebACL.RuleProperty> = [
    {
        priority: 1,
        overrideAction: { count: {} },
        visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: "AWS-AWSManagedRulesCommonRuleSet",
        },
        name: "AWS-AWSManagedRulesCommonRuleSet",
        statement: {
            managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesCommonRuleSet",
            },
        },
    },
];

/**
 * Build WAF rules from a policy config: managed rule groups (block or count-only per
 * `block`) plus rate-based rules for L7 DDoS / brute-force throttling.
 */
function buildRulesFromPolicy(policy: WafPolicyConfig): Array<wafv2.CfnWebACL.RuleProperty> {
    const rules: Array<wafv2.CfnWebACL.RuleProperty> = [];

    for (const group of policy.managedRuleGroups || []) {
        rules.push({
            name: group.name,
            priority: group.priority,
            // block=true lets the managed group's own block actions take effect;
            // block=false forces count-only (monitor) mode.
            overrideAction: group.block === false ? { count: {} } : { none: {} },
            statement: {
                managedRuleGroupStatement: {
                    vendorName: group.vendorName,
                    name: group.managedRuleGroupName,
                },
            },
            visibilityConfig: {
                sampledRequestsEnabled: true,
                cloudWatchMetricsEnabled: true,
                metricName: group.name,
            },
        });
    }

    for (const rateRule of policy.rateBasedRules || []) {
        rules.push({
            name: rateRule.name,
            priority: rateRule.priority,
            action: { block: {} },
            statement: {
                rateBasedStatement: {
                    limit: rateRule.limit,
                    aggregateKeyType: rateRule.aggregateKeyType || "IP",
                },
            },
            visibilityConfig: {
                sampledRequestsEnabled: true,
                cloudWatchMetricsEnabled: true,
                metricName: rateRule.name,
            },
        });
    }

    return rules;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Wafv2BasicConstructProps> = {
    wafScope: WAFScope.CLOUDFRONT,
    stackName: "",
    env: {},
};

/**
 * Deploys the notification handlers
 */
export class Wafv2BasicConstruct extends Construct {
    public webacl: wafv2.CfnWebACL;

    constructor(parent: Construct, name: string, props: Wafv2BasicConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const wafScopeString = props.wafScope!.toString();

        // Rule precedence: explicit props.rules > policy config > legacy default.
        // A populated config/policy/wafPolicyConfig.json applies best-practice managed
        // rule groups in block mode plus rate-based DDoS protection; an empty/absent
        // config falls back to the legacy count-only Common Rule Set.
        const resolvedRules =
            props.rules ||
            (props.wafPolicy ? buildRulesFromPolicy(props.wafPolicy) : legacyDefaultRules);

        /*
        if (props.wafScope === WAFScope.CLOUDFRONT && props.env?.region !== "us-east-1") {
            throw new Error(
                "Only supported region for WAFv2 scope when set to CLOUDFRONT is us-east-1. " +
                    "see - https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-wafv2-webacl.html"
            );
        } */

        const webacl = new wafv2.CfnWebACL(this, "webacl", {
            description: "Basic WAF",
            defaultAction: {
                allow: {},
            },
            rules: resolvedRules,
            scope: wafScopeString,
            visibilityConfig: {
                cloudWatchMetricsEnabled: true,
                metricName: "WAFACLGlobal",
                sampledRequestsEnabled: true,
            },
        });

        this.webacl = webacl;
    }
}
