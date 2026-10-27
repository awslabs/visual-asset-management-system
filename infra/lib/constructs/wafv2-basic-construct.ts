/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import * as logs from "aws-cdk-lib/aws-logs";
import { generateUniqueNameHash } from "../helper/security";
import { apiOriginVerifyCurrentValueReference } from "../helper/apiOriginVerify";
import * as Config from "../../config/config";
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

// Headers on the throttle response. The REGIONAL ACL is associated with the REST API stage, and
// API Gateway renders a WAF block — this custom response included — through its WAF_FILTERED
// gateway response, which `GatewayResponseDefault4XX` (rest-api-gateway-construct.ts) decorates
// with the API's CORS allow headers (Access-Control-Allow-Origin, Access-Control-Allow-Headers)
// on every 4XX it renders. A browser can therefore read the 429 in every topology, and the WAF
// response must not repeat those headers: API Gateway appends WAF's copy to its own, the browser
// sees `Access-Control-Allow-Origin: *, *` and rejects the response outright. Allow-Methods is
// not needed either — it matters only on a preflight, and a throttled OPTIONS is answered by WAF
// before the API's MOCK integration, so it can never be a successful preflight. What API Gateway
// does not add is Retry-After, the delta the web apiClient sleeps before its single retry (a
// short value is right for a rule that counts over a 5-minute window), and
// Access-Control-Expose-Headers, which lets a cross-origin caller read it (Retry-After is not
// CORS-safelisted). WAF custom response headers are literal strings only, and WAF sets
// Content-Type itself from the body's contentType, so it is not listed here.
const WAF_RATE_LIMIT_RESPONSE_HEADERS: ReadonlyArray<{ name: string; value: string }> = [
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

/**
 * Managed rules relaxed on a set of URI paths only. Applied on the policy path: in each policy
 * group for the named managed rule group that is in block mode, each named rule is set to count,
 * and a rule evaluated after every other rule in the ACL blocks a request carrying any of their
 * labels unless its URI path is at or below one of the prefixes of an exclusion that relaxes that
 * label. A rule the policy counts or allows keeps the policy's action and is not re-blocked; a rule
 * the policy blocks is relaxed and re-blocked like any other. Synth fails for an exclusion whose
 * shape `validateScopedRuleExclusions` rejects.
 */
export interface WafScopedRuleExclusion {
    managedRuleGroupName: string; // e.g. AWSManagedRulesCommonRuleSet
    // At least one rule: its name within the group and the label it adds when it matches (the
    // group's AvailableLabels). A rule set to count still adds its label, which the re-block rule
    // matches. AWS WAF accepts a label nothing emits, so a wrong label would leave the rule relaxed
    // on every path: synth fails for a label other than the one AWS_MANAGED_RULE_LABELS lists for
    // the rule, compared exactly (letter case included), and for a rule or group the table does not
    // list.
    rules: Array<{ name: string; label: string }>;
    // API route prefixes, each matched against the URI path the REST API stage receives. That path
    // begins with `/${Config.API_GATEWAY_STAGE_NAME}` for a request to the invoke URL, to the API
    // custom domain (base path `api`) and forwarded by CloudFront, so build each prefix from
    // `Config.API_GATEWAY_STAGE_NAME`. The CloudFront web ACL evaluates the viewer's path, which is
    // the same path: CloudFront forwards `/api/*` to the stage with no origin path. A prefix covers
    // the path itself and every path below it, at a segment boundary: `/api/x` covers `/api/x` and
    // `/api/x/y`, not `/api/xy`, and a trailing slash is ignored. The path must be at or below the
    // prefix both as received and after URL_DECODE then NORMALIZE_PATH, so neither an encoded slash
    // inside a path parameter nor a dot segment lets a request to another route fall under a prefix.
    // Synth fails for a prefix that is empty, covers the whole API or does not start with
    // `/${Config.API_GATEWAY_STAGE_NAME}/`. An exclusion with no prefix relaxes nothing.
    uriPathPrefixes: string[];
    // Name and metric name of the rule that blocks the exclusion's labels elsewhere: 1 to 128 of
    // A-Z, a-z, 0-9, - and _, not a reserved metric name, and unique among the exclusions and the
    // policy's rules.
    reapplyRuleName: string;
    // Managed rules outside the exclusion that requests under its prefixes are known to match. A
    // synth warning names each one the policy enforces: a named rule while its group runs in block
    // mode and the policy neither counts nor allows it, a whole group (no name) while it runs in
    // block mode.
    unrelaxedRules?: Array<{ managedRuleGroupName: string; name?: string }>;
}

/**
 * The label each rule of an AWS managed rule group adds when it matches, by group and rule name,
 * copied verbatim from the group's page in the AWS WAF Developer Guide:
 *   https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-baseline.html
 *     (Core rule set, Admin protection, Known bad inputs)
 *   https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-ip-rep.html
 *     (Amazon IP reputation list, Anonymous IP list)
 *   https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-use-case.html
 *     (SQL database, Linux, POSIX, Windows, PHP and WordPress)
 * A label is not derived from its rule name: the letter case differs (rule GenericRFI_BODY, label
 * ...:GenericRFI_Body; SQLiExtendedPatterns_URIPATH, ...:SQLiExtendedPatterns_UriPath) and a label
 * may drop part of the name (UserAgent_BadBots_HEADER, ...:BadBots_Header). A scoped exclusion
 * may relax only a rule listed here, and its label must equal the entry exactly. To relax another
 * rule, add it with the label its group's page documents; for another group, add the group and
 * each rule it relaxes.
 */
export const AWS_MANAGED_RULE_LABELS: Readonly<Record<string, Readonly<Record<string, string>>>> = {
    AWSManagedRulesCommonRuleSet: {
        NoUserAgent_HEADER: "awswaf:managed:aws:core-rule-set:NoUserAgent_Header",
        UserAgent_BadBots_HEADER: "awswaf:managed:aws:core-rule-set:BadBots_Header",
        SizeRestrictions_QUERYSTRING:
            "awswaf:managed:aws:core-rule-set:SizeRestrictions_QueryString",
        SizeRestrictions_Cookie_HEADER:
            "awswaf:managed:aws:core-rule-set:SizeRestrictions_Cookie_Header",
        SizeRestrictions_BODY: "awswaf:managed:aws:core-rule-set:SizeRestrictions_Body",
        SizeRestrictions_URIPATH: "awswaf:managed:aws:core-rule-set:SizeRestrictions_URIPath",
        EC2MetaDataSSRF_BODY: "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body",
        EC2MetaDataSSRF_COOKIE: "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Cookie",
        EC2MetaDataSSRF_URIPATH: "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_URIPath",
        EC2MetaDataSSRF_QUERYARGUMENTS:
            "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_QueryArguments",
        GenericLFI_QUERYARGUMENTS: "awswaf:managed:aws:core-rule-set:GenericLFI_QueryArguments",
        GenericLFI_URIPATH: "awswaf:managed:aws:core-rule-set:GenericLFI_URIPath",
        GenericLFI_BODY: "awswaf:managed:aws:core-rule-set:GenericLFI_Body",
        RestrictedExtensions_URIPATH:
            "awswaf:managed:aws:core-rule-set:RestrictedExtensions_URIPath",
        RestrictedExtensions_QUERYARGUMENTS:
            "awswaf:managed:aws:core-rule-set:RestrictedExtensions_QueryArguments",
        GenericRFI_QUERYARGUMENTS: "awswaf:managed:aws:core-rule-set:GenericRFI_QueryArguments",
        GenericRFI_BODY: "awswaf:managed:aws:core-rule-set:GenericRFI_Body",
        GenericRFI_URIPATH: "awswaf:managed:aws:core-rule-set:GenericRFI_URIPath",
        CrossSiteScripting_COOKIE: "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Cookie",
        CrossSiteScripting_QUERYARGUMENTS:
            "awswaf:managed:aws:core-rule-set:CrossSiteScripting_QueryArguments",
        CrossSiteScripting_BODY: "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body",
        CrossSiteScripting_URIPATH: "awswaf:managed:aws:core-rule-set:CrossSiteScripting_URIPath",
    },
    AWSManagedRulesAdminProtectionRuleSet: {
        AdminProtection_URIPATH: "awswaf:managed:aws:admin-protection:AdminProtection_URIPath",
    },
    AWSManagedRulesKnownBadInputsRuleSet: {
        JavaDeserializationRCE_HEADER:
            "awswaf:managed:aws:known-bad-inputs:JavaDeserializationRCE_Header",
        JavaDeserializationRCE_BODY:
            "awswaf:managed:aws:known-bad-inputs:JavaDeserializationRCE_Body",
        JavaDeserializationRCE_URIPATH:
            "awswaf:managed:aws:known-bad-inputs:JavaDeserializationRCE_URIPath",
        JavaDeserializationRCE_QUERYSTRING:
            "awswaf:managed:aws:known-bad-inputs:JavaDeserializationRCE_QueryString",
        Host_localhost_HEADER: "awswaf:managed:aws:known-bad-inputs:Host_Localhost_Header",
        PROPFIND_METHOD: "awswaf:managed:aws:known-bad-inputs:Propfind_Method",
        ExploitablePaths_URIPATH: "awswaf:managed:aws:known-bad-inputs:ExploitablePaths_URIPath",
        Log4JRCE_HEADER: "awswaf:managed:aws:known-bad-inputs:Log4JRCE_Header",
        Log4JRCE_QUERYSTRING: "awswaf:managed:aws:known-bad-inputs:Log4JRCE_QueryString",
        Log4JRCE_BODY: "awswaf:managed:aws:known-bad-inputs:Log4JRCE_Body",
        Log4JRCE_URIPATH: "awswaf:managed:aws:known-bad-inputs:Log4JRCE_URIPath",
        ReactJSRCE_BODY: "awswaf:managed:aws:known-bad-inputs:ReactJSRCE_Body",
    },
    AWSManagedRulesAmazonIpReputationList: {
        AWSManagedIPReputationList: "awswaf:managed:aws:amazon-ip-list:AWSManagedIPReputationList",
        AWSManagedReconnaissanceList:
            "awswaf:managed:aws:amazon-ip-list:AWSManagedReconnaissanceList",
        AWSManagedIPDDoSList: "awswaf:managed:aws:amazon-ip-list:AWSManagedIPDDoSList",
    },
    AWSManagedRulesAnonymousIpList: {
        AnonymousIPList: "awswaf:managed:aws:anonymous-ip-list:AnonymousIPList",
        HostingProviderIPList: "awswaf:managed:aws:anonymous-ip-list:HostingProviderIPList",
    },
    AWSManagedRulesSQLiRuleSet: {
        SQLi_QUERYARGUMENTS: "awswaf:managed:aws:sql-database:SQLi_QueryArguments",
        SQLiExtendedPatterns_QUERYARGUMENTS:
            "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_QueryArguments",
        SQLi_BODY: "awswaf:managed:aws:sql-database:SQLi_Body",
        SQLiExtendedPatterns_BODY: "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_Body",
        SQLiExtendedPatterns_HEADER: "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_Header",
        SQLiExtendedPatterns_URIPATH:
            "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_UriPath",
        SQLi_COOKIE: "awswaf:managed:aws:sql-database:SQLi_Cookie",
        SQLi_URIPATH: "awswaf:managed:aws:sql-database:SQLi_URIPath",
    },
    AWSManagedRulesLinuxRuleSet: {
        LFI_URIPATH: "awswaf:managed:aws:linux-os:LFI_URIPath",
        LFI_QUERYSTRING: "awswaf:managed:aws:linux-os:LFI_QueryString",
        LFI_HEADER: "awswaf:managed:aws:linux-os:LFI_Header",
    },
    AWSManagedRulesUnixRuleSet: {
        UNIXShellCommandsVariables_QUERYSTRING:
            "awswaf:managed:aws:posix-os:UNIXShellCommandsVariables_QueryString",
        UNIXShellCommandsVariables_BODY:
            "awswaf:managed:aws:posix-os:UNIXShellCommandsVariables_Body",
        UNIXShellCommandsVariables_HEADER:
            "awswaf:managed:aws:posix-os:UNIXShellCommandsVariables_Header",
    },
    AWSManagedRulesWindowsRuleSet: {
        WindowsShellCommands_HEADER: "awswaf:managed:aws:windows-os:WindowsShellCommands_Header",
        WindowsShellCommands_QUERYARGUMENTS:
            "awswaf:managed:aws:windows-os:WindowsShellCommands_QueryArguments",
        WindowsShellCommands_QUERYSTRING:
            "awswaf:managed:aws:windows-os:WindowsShellCommands_QueryString",
        WindowsShellCommands_URIPATH: "awswaf:managed:aws:windows-os:WindowsShellCommands_UriPath",
        WindowsShellCommands_BODY: "awswaf:managed:aws:windows-os:WindowsShellCommands_Body",
        PowerShellCommands_COOKIE: "awswaf:managed:aws:windows-os:PowerShellCommands_Cookie",
        PowerShellCommands_QUERYARGUMENTS:
            "awswaf:managed:aws:windows-os:PowerShellCommands_QueryArguments",
        PowerShellCommands_BODY: "awswaf:managed:aws:windows-os:PowerShellCommands_Body",
    },
    AWSManagedRulesPHPRuleSet: {
        PHPHighRiskMethodsVariables_HEADER:
            "awswaf:managed:aws:php-app:PHPHighRiskMethodsVariables_Header",
        PHPHighRiskMethodsVariables_QUERYSTRING:
            "awswaf:managed:aws:php-app:PHPHighRiskMethodsVariables_QueryString",
        PHPHighRiskMethodsVariables_BODY:
            "awswaf:managed:aws:php-app:PHPHighRiskMethodsVariables_Body",
        PHPHighRiskMethodsVariables_URIPATH:
            "awswaf:managed:aws:php-app:PHPHighRiskMethodsVariables_URIPath",
        PHP2Shell_BODY: "awswaf:managed:aws:php-app:PHP2Shell_Body",
        PHP2Shell_BODY_Extended: "awswaf:managed:aws:php-app:PHP2Shell_Body_Extended",
    },
    AWSManagedRulesWordPressRuleSet: {
        WordPressExploitableCommands_QUERYSTRING:
            "awswaf:managed:aws:wordpress-app:WordPressExploitableCommands_QUERYSTRING",
        WordPressExploitablePaths_URIPATH:
            "awswaf:managed:aws:wordpress-app:WordPressExploitablePaths_URIPATH",
    },
};

// AWS WAF rule names: 1 to 128 of A-Z, a-z, 0-9, - and _. The re-block rule's metric name is the
// same string, and a metric name may not be one AWS WAF reserves.
const WAF_RULE_AND_METRIC_NAME = /^[A-Za-z0-9_-]{1,128}$/;
const WAF_RESERVED_METRIC_NAMES = ["All", "Default_Action"];

export interface Wafv2BasicConstructProps extends cdk.StackProps {
    readonly wafScope?: WAFScope;
    readonly rules?: Array<wafv2.CfnWebACL.RuleProperty | cdk.IResolvable> | cdk.IResolvable;
    readonly wafPolicy?: WafPolicyConfig;
    readonly scopedRuleExclusions?: WafScopedRuleExclusion[];
    readonly stackName?: string;
    readonly env?: cdk.Environment;
    /**
     * ARN of the deployment's origin verification secret (CloudFront deployments only). Accepted for
     * the REGIONAL scope only: its rate-based rules then leave requests that carry the value
     * CloudFront adds to the CloudFront-scoped web ACL, and the header is redacted from the log.
     */
    readonly originVerifySecretArn?: string;
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

/** A statement matching any of `statements`; WAF requires an OR statement to have two or more. */
function anyOf(
    statements: Array<wafv2.CfnWebACL.StatementProperty>
): wafv2.CfnWebACL.StatementProperty {
    return statements.length === 1 ? statements[0] : { orStatement: { statements } };
}

/** A byte match of `searchString` against the URI path after `textTransformations`. */
function uriPathMatch(
    searchString: string,
    positionalConstraint: "EXACTLY" | "STARTS_WITH",
    textTransformations: Array<wafv2.CfnWebACL.TextTransformationProperty>
): wafv2.CfnWebACL.StatementProperty {
    return {
        byteMatchStatement: {
            searchString,
            fieldToMatch: { uriPath: {} },
            textTransformations,
            positionalConstraint,
        },
    };
}

/**
 * The URI path, after `textTransformations`, is `prefix` itself or a path below it: it equals the
 * prefix or starts with the prefix and a slash, so a sibling route whose segment only begins with
 * the same characters is not covered.
 */
function uriPathAtOrBelow(
    prefix: string,
    textTransformations: () => Array<wafv2.CfnWebACL.TextTransformationProperty>
): wafv2.CfnWebACL.StatementProperty {
    return {
        orStatement: {
            statements: [
                uriPathMatch(prefix, "EXACTLY", textTransformations()),
                uriPathMatch(`${prefix}/`, "STARTS_WITH", textTransformations()),
            ],
        },
    };
}

const rawPath = (): Array<wafv2.CfnWebACL.TextTransformationProperty> => [
    { priority: 0, type: "NONE" },
];
const decodedNormalizedPath = (): Array<wafv2.CfnWebACL.TextTransformationProperty> => [
    { priority: 0, type: "URL_DECODE" },
    { priority: 1, type: "NORMALIZE_PATH" },
];

/** A prefix as the re-block rule matches it: without trailing slashes. */
function canonicalPrefix(prefix: string): string {
    return prefix.replace(/\/+$/, "");
}

/** Labels re-blocked together because the same prefixes exempt them. */
interface ScopedExclusionClause {
    labels: string[];
    prefixes: string[];
}

/**
 * The rule re-applying a scoped exclusion's relaxed rules off their paths: block a request carrying
 * a clause's label unless its URI path is at or below one of the clause's prefixes both as received
 * and once URL decoded and then normalized. API Gateway routes on the path as received, where an
 * encoded slash stays inside one path parameter, and normalization resolves `..` segments, so a
 * request reaching another route through either is not taken for one under the prefix.
 */
function buildScopedExclusionRule(
    name: string,
    clauses: ScopedExclusionClause[],
    priority: number
): wafv2.CfnWebACL.RuleProperty {
    const clauseStatement = (clause: ScopedExclusionClause): wafv2.CfnWebACL.StatementProperty => ({
        andStatement: {
            statements: [
                anyOf(
                    clause.labels.map((key) => ({ labelMatchStatement: { scope: "LABEL", key } }))
                ),
                {
                    notStatement: {
                        statement: anyOf(
                            clause.prefixes.map((prefix) => ({
                                andStatement: {
                                    statements: [
                                        uriPathAtOrBelow(prefix, rawPath),
                                        uriPathAtOrBelow(prefix, decodedNormalizedPath),
                                    ],
                                },
                            }))
                        ),
                    },
                },
            ],
        },
    });
    return {
        name,
        priority,
        action: { block: {} },
        statement: anyOf(clauses.map(clauseStatement)),
        visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: name,
        },
    };
}

/** `key` is an own property of `record`, not one it inherits from Object.prototype (`toString`). */
function hasOwn(record: object, key: string): boolean {
    return Object.prototype.hasOwnProperty.call(record, key);
}

/**
 * Fails synth for a scoped exclusion AWS WAF would reject at deploy time or that would not do what
 * it says: a re-block rule name that is not a valid rule and metric name, repeats another
 * exclusion's or names a policy rule; an exclusion relaxing no rule; a label other than the named
 * rule's documented label in AWS_MANAGED_RULE_LABELS, compared exactly (AWS WAF accepts a label
 * nothing emits, which would leave the rule relaxed on every path), or a rule or group that table
 * does not list; and a prefix that is empty, covers the whole API or lies outside the API stage
 * path (which would never match).
 */
function validateScopedRuleExclusions(
    policy: WafPolicyConfig,
    exclusions: Array<WafScopedRuleExclusion>
): void {
    const stagePath = `/${Config.API_GATEWAY_STAGE_NAME}`;
    const policyRuleNames = [
        ...(policy.managedRuleGroups || []).map((g) => g.name),
        ...(policy.rateBasedRules || []).map((r) => r.name),
    ];
    const seen = new Set<string>();
    for (const exclusion of exclusions) {
        const name = exclusion.reapplyRuleName;
        const subject = `Wafv2BasicConstruct: scoped rule exclusion ${JSON.stringify(name)}`;
        if (
            typeof name !== "string" ||
            !WAF_RULE_AND_METRIC_NAME.test(name) ||
            WAF_RESERVED_METRIC_NAMES.includes(name)
        ) {
            throw new Error(
                `${subject}: reapplyRuleName is not a valid AWS WAF rule and metric name. Use 1 ` +
                    `to 128 of A-Z, a-z, 0-9, - and _, other than ` +
                    `${WAF_RESERVED_METRIC_NAMES.join(" and ")}.`
            );
        }
        if (seen.has(name)) {
            throw new Error(
                `Wafv2BasicConstruct: more than one scoped rule exclusion is named "${name}"; ` +
                    `each reapplyRuleName must be unique in the web ACL.`
            );
        }
        seen.add(name);
        if (policyRuleNames.includes(name)) {
            throw new Error(
                `${subject}: reapplyRuleName is also the name of a rule in the WAF policy ` +
                    `(config/policy/wafPolicyConfig.json); rule names must be unique in the web ACL.`
            );
        }

        if (!exclusion.rules || exclusion.rules.length === 0) {
            throw new Error(`${subject} relaxes no rule; list at least one { name, label }.`);
        }
        const group = exclusion.managedRuleGroupName;
        const groupLabels = hasOwn(AWS_MANAGED_RULE_LABELS, group)
            ? AWS_MANAGED_RULE_LABELS[group]
            : undefined;
        if (!groupLabels) {
            throw new Error(
                `${subject} names managed rule group "${group}", which is not in ` +
                    `AWS_MANAGED_RULE_LABELS, so its labels cannot be checked. Add the group and ` +
                    `the label of each rule to relax, as its page in the AWS WAF Developer Guide ` +
                    `documents it, to AWS_MANAGED_RULE_LABELS in ` +
                    `infra/lib/constructs/wafv2-basic-construct.ts.`
            );
        }
        for (const rule of exclusion.rules) {
            const expected = hasOwn(groupLabels, rule.name) ? groupLabels[rule.name] : undefined;
            if (expected === undefined) {
                throw new Error(
                    `${subject}: rule "${rule.name}" of ${group} is not in ` +
                        `AWS_MANAGED_RULE_LABELS, so its label cannot be checked. Add the rule ` +
                        `with the label its group's page in the AWS WAF Developer Guide documents ` +
                        `to AWS_MANAGED_RULE_LABELS in infra/lib/constructs/wafv2-basic-construct.ts.`
                );
            }
            if (rule.label !== expected) {
                throw new Error(
                    `${subject}: the label "${rule.label}" of rule "${rule.name}" is not that ` +
                        `rule's label in ${group}. It must be "${expected}", exactly as documented, ` +
                        `letter case included. AWS WAF accepts a label nothing emits, so a wrong ` +
                        `one would leave the rule relaxed on every path.`
                );
            }
        }

        for (const prefix of exclusion.uriPathPrefixes) {
            if (!prefix) {
                throw new Error(`${subject} has an empty URI path prefix.`);
            }
            const canonical = canonicalPrefix(prefix);
            if (canonical === "" || canonical === stagePath) {
                throw new Error(
                    `${subject}: URI path prefix "${prefix}" covers the whole API; name a route ` +
                        `prefix below ${stagePath}/.`
                );
            }
            if (!canonical.startsWith(`${stagePath}/`)) {
                throw new Error(
                    `${subject}: URI path prefix "${prefix}" does not start with "${stagePath}/", ` +
                        `which begins the path of every request the REST API stage receives, so it ` +
                        `would never match. Build it from Config.API_GATEWAY_STAGE_NAME.`
                );
            }
        }
    }
}

/**
 * Scope-down statement for a REGIONAL rate-based rule on a CloudFront deployment: the rule counts
 * only requests that do NOT carry the deployment's origin verification value, so requests CloudFront
 * forwards are rate-limited by the CloudFront-scoped web ACL (which observes the viewer address)
 * rather than per CloudFront edge address, and direct execute-api and custom-domain requests keep the
 * regional limit.
 *
 * Both match branches resolve to the secret's `current` value. During a rotation the rotation function
 * sets the first branch to the new value and the second to the previous one, so requests from
 * CloudFront edges that still send the previous value are recognised while the distribution update
 * propagates; a later stack update resolves both branches to `current` again.
 */
function originVerifyScopeDownStatement(secretArn: string): wafv2.CfnWebACL.StatementProperty {
    const valueMatch = (): wafv2.CfnWebACL.StatementProperty => ({
        byteMatchStatement: {
            fieldToMatch: { singleHeader: { Name: Config.API_ORIGIN_VERIFY_HEADER_NAME } },
            positionalConstraint: "EXACTLY",
            searchString: apiOriginVerifyCurrentValueReference(secretArn),
            textTransformations: [{ priority: 0, type: "NONE" }],
        },
    });
    return {
        notStatement: {
            statement: { orStatement: { statements: [valueMatch(), valueMatch()] } },
        },
    };
}

/**
 * Build WAF rules from a policy config: managed rule groups (block or count-only per
 * `block`), rate-based rules for L7 DDoS / brute-force throttling, and one re-block rule per
 * scoped exclusion that relaxed a rule, after validating the exclusions. `wafScopeString` is the
 * ACL scope the rules are being built for, reported alongside any policy warning.
 * `originVerifySecretArn` adds the origin verification scope-down to every rate-based rule; the
 * re-block rules are unaffected.
 */
function buildRulesFromPolicy(
    policy: WafPolicyConfig,
    scope: Construct,
    wafScopeString: string,
    exclusions: Array<WafScopedRuleExclusion>,
    originVerifySecretArn?: string
): Array<wafv2.CfnWebACL.RuleProperty> {
    const rules: Array<wafv2.CfnWebACL.RuleProperty> = [];

    validateScopedRuleExclusions(policy, exclusions);

    // The labels each scoped exclusion relaxed, re-blocked after the other rules. An exclusion
    // with no path prefix relaxes nothing.
    const scopedExclusions = exclusions.filter((e) => e.uriPathPrefixes.length > 0);
    const relaxedLabels: string[][] = scopedExclusions.map(() => []);
    warnUnrelaxedRules(policy, scope, wafScopeString, scopedExclusions);

    for (const group of policy.managedRuleGroups || []) {
        // Per-rule action overrides (e.g. set SizeRestrictions_BODY to count) so a single rule can
        // be relaxed while the rest of the managed group stays in block mode.
        const ruleActionOverrides: Array<wafv2.CfnWebACL.RuleActionOverrideProperty> = (
            group.ruleActionOverrides || []
        ).map((o) => ({
            name: o.name,
            actionToUse:
                o.action === "count"
                    ? { count: {} }
                    : o.action === "allow"
                    ? { allow: {} }
                    : { block: {} },
        }));
        // Scoped exclusions: in a group in block mode each named rule counts, and the re-block rules
        // built after the loop block its label off the paths of every exclusion relaxing it. A rule
        // the policy counts or allows is relaxed everywhere already and keeps the policy's action; a
        // rule the policy blocks is relaxed here and re-blocked off the paths like any other.
        if (group.block !== false) {
            scopedExclusions.forEach((exclusion, index) => {
                if (exclusion.managedRuleGroupName !== group.managedRuleGroupName) return;
                for (const rule of exclusion.rules) {
                    const policyAction = (group.ruleActionOverrides || []).find(
                        (o) => o.name === rule.name
                    )?.action;
                    if (policyAction === "count" || policyAction === "allow") continue;
                    const relaxed = { name: rule.name, actionToUse: { count: {} } };
                    const existing = ruleActionOverrides.findIndex((o) => o.name === rule.name);
                    if (existing >= 0) {
                        ruleActionOverrides[existing] = relaxed;
                    } else {
                        ruleActionOverrides.push(relaxed);
                    }
                    if (!relaxedLabels[index].includes(rule.label)) {
                        relaxedLabels[index].push(rule.label);
                    }
                }
            });
        }
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
        // The response carries Retry-After, exposed to cross-origin callers, so a client can honor
        // the server's retry hint.
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
                    ...(originVerifySecretArn
                        ? {
                              scopeDownStatement:
                                  originVerifyScopeDownStatement(originVerifySecretArn),
                          }
                        : {}),
                },
            },
            visibilityConfig: {
                sampledRequestsEnabled: true,
                cloudWatchMetricsEnabled: true,
                metricName: rateRule.name,
            },
        });
    }

    // Re-block rules come after every other rule, so the relaxed rules' labels are already set.
    // A label two exclusions relax is exempt on the prefixes of both: each exclusion's rule
    // re-blocks its labels in clauses, one per set of prefixes that exempts them, so neither
    // exclusion's rule blocks the other's paths. Each exclusion keeps its own rule name.
    const labelPrefixes = new Map<string, string[]>();
    scopedExclusions.forEach((exclusion, index) => {
        for (const label of relaxedLabels[index]) {
            const prefixes = labelPrefixes.get(label) || [];
            for (const prefix of exclusion.uriPathPrefixes.map(canonicalPrefix)) {
                if (!prefixes.includes(prefix)) prefixes.push(prefix);
            }
            labelPrefixes.set(label, prefixes);
        }
    });
    const prefixSetKey = (prefixes: string[]): string => JSON.stringify([...prefixes].sort());
    let reapplyPriority = Math.max(0, ...rules.map((r) => r.priority)) + 1;
    scopedExclusions.forEach((exclusion, index) => {
        const clauses: ScopedExclusionClause[] = [];
        for (const label of relaxedLabels[index]) {
            const prefixes = labelPrefixes.get(label) || [];
            const clause = clauses.find((c) => prefixSetKey(c.prefixes) === prefixSetKey(prefixes));
            if (clause) {
                clause.labels.push(label);
            } else {
                clauses.push({ labels: [label], prefixes });
            }
        }
        if (clauses.length) {
            rules.push(
                buildScopedExclusionRule(exclusion.reapplyRuleName, clauses, reapplyPriority++)
            );
        }
    });

    return rules;
}

/**
 * Reports, at synth, each managed rule an active scoped exclusion names as unrelaxed that the
 * policy enforces, since requests under the exclusion's paths that match it are still blocked.
 */
function warnUnrelaxedRules(
    policy: WafPolicyConfig,
    scope: Construct,
    wafScopeString: string,
    exclusions: Array<WafScopedRuleExclusion>
): void {
    const blockModeGroups = (policy.managedRuleGroups || []).filter((g) => g.block !== false);
    for (const exclusion of exclusions) {
        for (const entry of exclusion.unrelaxedRules || []) {
            const groups = blockModeGroups.filter(
                (g) => g.managedRuleGroupName === entry.managedRuleGroupName
            );
            const enforced = entry.name
                ? groups.some((g) => {
                      const action = (g.ruleActionOverrides || []).find(
                          (o) => o.name === entry.name
                      )?.action;
                      return action !== "count" && action !== "allow";
                  })
                : groups.length > 0;
            if (!enforced) continue;
            const subject = entry.name
                ? `the ${entry.name} rule of ${entry.managedRuleGroupName}`
                : `the ${entry.managedRuleGroupName} rule group`;
            cdk.Annotations.of(scope).addWarningV2(
                `VAMS:WafPolicy:${exclusion.reapplyRuleName}:unrelaxed:${entry.managedRuleGroupName}` +
                    (entry.name ? `:${entry.name}` : ""),
                `WAF policy enforces ${subject} in the ${wafScopeString} web ACL, which the scoped ` +
                    `rule exclusion "${exclusion.reapplyRuleName}" does not relax: a request under ` +
                    `${exclusion.uriPathPrefixes.join(", ")} that matches it is still blocked. ` +
                    `Override the rule to count, or run the group in count mode, in ` +
                    `config/policy/wafPolicyConfig.json if those requests must pass.`
            );
        }
    }
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

        if (props.originVerifySecretArn && props.wafScope !== WAFScope.REGIONAL) {
            throw new Error(
                "Wafv2BasicConstruct: originVerifySecretArn applies to the REGIONAL web ACL only; " +
                    "CloudFront adds the origin verification header after the CLOUDFRONT-scoped " +
                    "web ACL has evaluated the request."
            );
        }

        // Rule precedence: explicit props.rules > policy config > legacy default.
        // A populated config/policy/wafPolicyConfig.json applies best-practice managed
        // rule groups in block mode plus rate-based DDoS protection; an empty/absent
        // config falls back to the legacy count-only Common Rule Set. Scoped rule
        // exclusions apply to the policy rules only.
        const resolvedRules =
            props.rules ||
            (props.wafPolicy
                ? buildRulesFromPolicy(
                      props.wafPolicy,
                      this,
                      wafScopeString,
                      props.scopedRuleExclusions || [],
                      props.originVerifySecretArn
                  )
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
            // request, and on a CloudFront deployment so would the origin verification value
            // CloudFront adds to every request it forwards. WAF redacts the value and keeps the
            // field name.
            redactedFields: [
                { singleHeader: { Name: "authorization" } },
                ...(props.originVerifySecretArn
                    ? [{ singleHeader: { Name: Config.API_ORIGIN_VERIFY_HEADER_NAME } }]
                    : []),
            ],
        });
    }
}
