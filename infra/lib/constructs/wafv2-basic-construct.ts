/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import * as logs from "aws-cdk-lib/aws-logs";
import { generateUniqueNameHash } from "../helper/security";
import { API_CORS_ALLOW_HEADERS, API_CORS_ALLOW_METHODS } from "../helper/apiCorsHeaders";
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

// Headers on the throttle response. WAF answers a blocked request itself, before API Gateway,
// so none of the CORS headers the API emits — on the OPTIONS preflight (buildOpenApiSpec.ts) or
// on `GatewayResponseDefault4XX/5XX` (rest-api-gateway-construct.ts) — are added to it. On the
// default CloudFront topology that does not matter: the web app calls `/api/*` on its own
// distribution, so the request is same-origin and the browser reads the 429 without a CORS
// check. Where the API is called cross-origin — the ALB topology (web app on the ALB host, API on
// the execute-api host) or a client calling the execute-api endpoint directly from another
// origin — a browser exposes the 429 only when the response carries CORS headers; otherwise the
// web apiClient sees `TypeError: Failed to fetch` instead of the 429 it retries on. The
// allow-headers and allow-methods values are the API's own (helper/apiCorsHeaders.ts), so the
// two cannot drift. A throttled OPTIONS preflight still fails regardless of these headers: WAF
// answers it before the API's MOCK integration, and a preflight needs a success status.
// Retry-After is the delta the apiClient sleeps before its single retry — a short value is
// right for a rule that counts over a 5-minute window — and Access-Control-Expose-Headers
// is what lets a cross-origin caller read it (Retry-After is not CORS-safelisted). WAF custom
// response headers are literal strings only, and WAF sets Content-Type itself from the body's
// contentType, so it is not listed here.
const WAF_RATE_LIMIT_RESPONSE_HEADERS: ReadonlyArray<{ name: string; value: string }> = [
    { name: "Access-Control-Allow-Origin", value: "*" },
    { name: "Access-Control-Allow-Headers", value: API_CORS_ALLOW_HEADERS },
    { name: "Access-Control-Allow-Methods", value: API_CORS_ALLOW_METHODS },
    { name: "Access-Control-Expose-Headers", value: "Retry-After" },
    { name: "Retry-After", value: "1" },
];

// The aggregation key every rate-based rule counts on, in both the CLOUDFRONT-scoped and the
// REGIONAL ACL: the request-origin address, which WAF takes from the connection and is therefore
// present on every request and not settable by the caller.
const WAF_RATE_LIMIT_AGGREGATE_KEY_TYPE = "IP";

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
        // "count" while the rest of the group stays in block mode. The shipped policy counts the
        // Common Rule Set rules that match ordinary VAMS requests: the two size rules
        // (SizeRestrictions_BODY for large multi-part upload bodies, SizeRestrictions_QUERYSTRING
        // for the SuperSplat viewer's presigned-URL "?load=" parameter) and the six rules that
        // fire on asset file keys carried in the URI path, query string, or JSON body —
        // SizeRestrictions_URIPATH (encoded keys over 1024 bytes on the stream routes),
        // RestrictedExtensions_URIPATH / _QUERYARGUMENTS (file names ending .log, .ini, .cfg,
        // .conf, ... that the backend upload blocklist admits), and GenericLFI_URIPATH /
        // _QUERYARGUMENTS / _BODY (any encoded "../"; the backend rejects ".." path segments
        // itself and resolves keys against S3, not a local filesystem).
        ruleActionOverrides?: Array<{ name: string; action: "count" | "block" | "allow" }>;
    }>;
    rateBasedRules?: Array<{
        name: string;
        priority: number;
        limit: number; // requests per 5-minute window per aggregate key
        // Counter aggregation key. "IP" — the request-origin address — is the only key the
        // construct emits, for every ACL scope; any other value is replaced by "IP" and reported
        // as a synth warning. A forwarded-IP key is not usable in either VAMS topology: WAF reads
        // the FIRST address of the named header, while CloudFront and the ALB append the
        // connection address to a caller-supplied X-Forwarded-For rather than replacing it, so
        // the first address is whatever the caller sent. WAF also omits a request that carries no
        // such header from the rule's evaluation altogether — a missing header does not reach the
        // fallback behavior, which covers only a malformed address in a header that is present.
        aggregateKeyType?: string;
        // Accepted but not emitted, for the reason described on aggregateKeyType.
        forwardedIPConfig?: {
            headerName?: string;
            fallbackBehavior?: string;
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
 * `block`) plus rate-based rules for L7 DDoS / brute-force throttling. `wafScopeString` is
 * the ACL scope the rules are being built for, reported alongside any policy warning.
 */
function buildRulesFromPolicy(
    policy: WafPolicyConfig,
    scope: Construct,
    wafScopeString: string
): Array<wafv2.CfnWebACL.RuleProperty> {
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
        // Every rate rule counts on the request-origin IP address. A policy that names a
        // different aggregation still gets the origin address, and the substitution is reported
        // at synth: a header-derived key is caller-controlled behind both CloudFront and the ALB,
        // and it exempts every request that omits the header — which is every direct
        // execute-api caller — from the rule.
        const requestedKeyType = rateRule.aggregateKeyType || WAF_RATE_LIMIT_AGGREGATE_KEY_TYPE;
        if (requestedKeyType !== WAF_RATE_LIMIT_AGGREGATE_KEY_TYPE) {
            cdk.Annotations.of(scope).addWarningV2(
                `VAMS:WafPolicy:rateBasedRules:${rateRule.name}:aggregateKeyType`,
                `WAF policy rate-based rule "${rateRule.name}" sets aggregateKeyType ` +
                    `"${requestedKeyType}" for the ${wafScopeString} web ACL. The rule is built ` +
                    `with "${WAF_RATE_LIMIT_AGGREGATE_KEY_TYPE}" instead, because a ` +
                    `header-derived client address can be set by the caller and leaves requests ` +
                    `without that header uncounted. Remove the aggregateKeyType override from ` +
                    `config/policy/wafPolicyConfig.json to clear this warning.`
            );
        }

        // Throttle blocks return a real throttle status (429) with a JSON body, not the WAF
        // default 403 — so clients can tell rate-limiting apart from an auth/permission denial.
        // The response carries CORS headers and Retry-After so a browser can read the status.
        const blockAction = {
            block: {
                customResponse: {
                    responseCode: rateRule.blockResponseCode ?? WAF_RATE_LIMIT_RESPONSE_CODE,
                    customResponseBodyKey: WAF_RATE_LIMIT_BODY_KEY,
                    responseHeaders: WAF_RATE_LIMIT_RESPONSE_HEADERS.map((h) => ({ ...h })),
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
                    aggregateKeyType: WAF_RATE_LIMIT_AGGREGATE_KEY_TYPE,
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
            (props.wafPolicy
                ? buildRulesFromPolicy(props.wafPolicy, this, wafScopeString)
                : legacyDefaultRules);

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

        // Durable request logging. `visibilityConfig` above yields CloudWatch metrics and a
        // rolling sample only, so with the managed rule groups in block mode a rejected request
        // leaves no record to correlate a report against — WAF answers it before any VAMS Lambda
        // runs, so nothing else logs it either.
        //
        // The log group name must begin with `aws-waf-logs-`; AWS WAF rejects any other
        // destination name. It is created in this construct's own scope, which places it in the
        // ACL's Region — a requirement AWS WAF enforces, and the reason a CLOUDFRONT-scoped ACL
        // (us-east-1) gets its group there rather than in the deployment's Region.
        const wafLogGroup = new logs.LogGroup(this, "WafLogs", {
            logGroupName:
                "aws-waf-logs-vams-" +
                generateUniqueNameHash(
                    props.stackName ?? "vams",
                    props.env?.account ?? "",
                    "WafLogs" + wafScopeString,
                    10
                ),
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        new wafv2.CfnLoggingConfiguration(this, "WafLoggingConfiguration", {
            resourceArn: webacl.attrArn,
            logDestinationConfigs: [wafLogGroup.logGroupArn],
            // The bearer token would otherwise be written in cleartext for every recorded
            // request. WAF redacts the value and keeps the field name.
            redactedFields: [{ singleHeader: { Name: "authorization" } }],
        });
    }
}
