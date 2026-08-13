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

// Shared custom-response for rate-based (throttle) blocks: HTTP 429 Too Many Requests with a
// small JSON body, registered once on the ACL and referenced by every rate rule.
const WAF_RATE_LIMIT_RESPONSE_CODE = 429;
const WAF_RATE_LIMIT_BODY_KEY = "VamsRateLimitBody";
const WAF_RATE_LIMIT_BODY_CONTENT = JSON.stringify({
    message: "Rate limit exceeded. Please retry shortly.",
});

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
        // Per-rule action overrides within the managed group. Use to set a specific rule to
        // "count" while the rest of the group stays in block mode — e.g. SizeRestrictions_BODY,
        // which would otherwise block VAMS's large multi-part upload initialize/complete bodies,
        // and SizeRestrictions_QUERYSTRING, which caps a query string at 2048 bytes and would
        // otherwise block the SuperSplat viewer's presigned-URL "?load=" parameter.
        ruleActionOverrides?: Array<{ name: string; action: "count" | "block" | "allow" }>;
    }>;
    rateBasedRules?: Array<{
        name: string;
        priority: number;
        limit: number; // requests per 5-minute window per aggregate key
        aggregateKeyType?: string; // "IP" (default) or "FORWARDED_IP"
        // Required when aggregateKeyType is "FORWARDED_IP" so the rule keys on the real
        // client IP (from the X-Forwarded-For header) rather than the immediate TCP source.
        // Use FORWARDED_IP for CloudFront-fronted or behind-proxy deployments where the
        // regional WAF (on the ALB/API GW) would otherwise see the CloudFront/proxy IP.
        forwardedIPConfig?: {
            headerName?: string; // default "X-Forwarded-For"
            fallbackBehavior?: string; // "MATCH" or "NO_MATCH" (default) when the header is absent
        };
        // HTTP status returned when the rate rule blocks. Defaults to 429 (Too Many Requests)
        // — the correct throttle semantic, distinguishable from a 403 Casbin permission denial.
        blockResponseCode?: number;
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
        // Per-rule action overrides (e.g. set SizeRestrictions_BODY to count) so a single rule can
        // be relaxed while the rest of the managed group stays in block mode.
        const ruleActionOverrides = (group.ruleActionOverrides || []).map((o) => ({
            name: o.name,
            actionToUse:
                o.action === "count"
                    ? { count: {} }
                    : o.action === "allow"
                    ? { allow: {} }
                    : { block: {} },
        }));
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
                    ...(ruleActionOverrides.length ? { ruleActionOverrides } : {}),
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
        const aggregateKeyType = rateRule.aggregateKeyType || "IP";
        // FORWARDED_IP requires a forwardedIPConfig so WAF knows which header carries the
        // real client IP (X-Forwarded-For by default). Omit the block entirely for plain IP.
        const forwardedIPConfig =
            aggregateKeyType === "FORWARDED_IP"
                ? {
                      headerName: rateRule.forwardedIPConfig?.headerName || "X-Forwarded-For",
                      fallbackBehavior: rateRule.forwardedIPConfig?.fallbackBehavior || "NO_MATCH",
                  }
                : undefined;

        // Throttle blocks return a real throttle status (429) with a JSON body, not the WAF
        // default 403 — so clients can tell rate-limiting apart from an auth/permission denial.
        const blockAction = {
            block: {
                customResponse: {
                    responseCode: rateRule.blockResponseCode ?? WAF_RATE_LIMIT_RESPONSE_CODE,
                    customResponseBodyKey: WAF_RATE_LIMIT_BODY_KEY,
                },
            },
        };

        rules.push({
            name: rateRule.name,
            priority: rateRule.priority,
            action: blockAction,
            statement: {
                rateBasedStatement: {
                    limit: rateRule.limit,
                    aggregateKeyType,
                    ...(forwardedIPConfig ? { forwardedIpConfig: forwardedIPConfig } : {}),
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
 * Whether the policy defines any rate-based rule (so the ACL must register the shared
 * custom-response body those rules reference).
 */
function hasRateBasedRules(policy?: WafPolicyConfig): boolean {
    return !!policy && (policy.rateBasedRules?.length || 0) > 0;
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

        // Register the shared 429 throttle body when the policy defines rate-based rules
        // (rules built above reference it by key). Explicit props.rules bypass the policy path.
        const customResponseBodies =
            !props.rules && hasRateBasedRules(props.wafPolicy)
                ? {
                      [WAF_RATE_LIMIT_BODY_KEY]: {
                          contentType: "APPLICATION_JSON",
                          content: WAF_RATE_LIMIT_BODY_CONTENT,
                      },
                  }
                : undefined;

        const webacl = new wafv2.CfnWebACL(this, "webacl", {
            description: "Basic WAF",
            defaultAction: {
                allow: {},
            },
            rules: resolvedRules,
            scope: wafScopeString,
            ...(customResponseBodies ? { customResponseBodies } : {}),
            visibilityConfig: {
                cloudWatchMetricsEnabled: true,
                metricName: "WAFACLGlobal",
                sampledRequestsEnabled: true,
            },
        });

        this.webacl = webacl;
    }
}
