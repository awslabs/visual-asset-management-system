/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Managed rules relaxed on a set of URI paths only.
 *
 * A scoped rule exclusion sets named managed rules to count inside their group and adds one VAMS
 * rule that blocks on their labels everywhere except under the exclusion's URI path prefixes. The
 * enabled add-ons supply both web ACLs' exclusions through `addonWafRuleExclusions()`; the
 * fixture exclusion here has the same shape, two Common Rule Set body rules relaxed under one
 * prefix.
 *
 * What is pinned here, for both ACL scopes and driven by the shipped policy:
 *   * the relaxed rules are overridden to count and the policy's own overrides are kept;
 *   * the re-block rule is evaluated after every other rule, blocks, matches either rule's label
 *     and exempts only a path whose raw form, which API Gateway routes on, and whose URL-decoded,
 *     normalized form are both the prefix itself or a path below it, so neither a sibling route
 *     that begins with the same characters, an encoded slash inside a path parameter nor a dot
 *     segment lets a request to another route borrow the exemption (checked against a model of
 *     the statements' evaluation as well as by their exact shape);
 *   * two exclusions relaxing the same rule exempt its label on the prefixes of both, and each
 *     keeps its own re-block rule name;
 *   * synth fails for a label that is not exactly the named rule's documented label (letter case
 *     included), a rule or managed group the documented label table does not list, an exclusion
 *     relaxing no rule, an empty, whole-API or off-stage prefix, and a duplicate, policy-colliding
 *     or invalid re-block rule name;
 *   * a CfWafStack passes the exclusions to its web ACL;
 *   * with no exclusion supplied, or an empty list, the ACL is the one the policy alone produces;
 *   * a rule the policy counts or allows keeps the policy's action and is not re-blocked; a rule
 *     the policy blocks is relaxed and re-blocked like any other;
 *   * a label is re-blocked once, however many policy groups name the managed group;
 *   * an exclusion with no path prefix relaxes nothing and reports no unrelaxed rule;
 *   * the explicit-rules and legacy count-only paths ignore exclusions;
 *   * `buildWafStacks()` keeps the stack names, Regions and origin verification wiring, applies the
 *     add-on exclusions to both web ACLs, and with no add-on exclusion builds both web ACLs from the
 *     policy alone.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { buildApiOriginVerifyStack } from "../../lib/apiOriginVerify-stack";
import { buildWafStacks, CfWafStack } from "../../lib/cf-waf-stack";
import {
    AWS_MANAGED_RULE_LABELS,
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
    WafScopedRuleExclusion,
} from "../../lib/constructs/wafv2-basic-construct";
import * as addonWafExclusions from "../../lib/nestedStacks/addon/addonWafExclusions";
import { buildTemplateConfig } from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

const COMMON_RULE_SET = "AWSManagedRulesCommonRuleSet";
const SSRF_RULE = "EC2MetaDataSSRF_BODY";
const RFI_RULE = "GenericRFI_BODY";
const SSRF_LABEL = "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body";
const RFI_LABEL = "awswaf:managed:aws:core-rule-set:GenericRFI_Body";
// Blocked by the shipped policy, unlike GenericRFI_BODY, which it counts: the fixture relaxes two
// rules the policy blocks, so both are re-blocked off the prefix.
const XSS_RULE = "CrossSiteScripting_BODY";
const XSS_LABEL = "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body";
const FIXTURE_PREFIX = "/api/fixture/bodies";
const REBLOCK_RULE = "VAMS-FixtureBodyRules";
const LINUX_RULE_SET = "AWSManagedRulesLinuxRuleSet";

/** The policy that ships with the deployment, as `wafRateLimit.test.ts` reads it. */
const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "config", "policy", "wafPolicyConfig.json"), {
        encoding: "utf8",
    })
);

const clonePolicy = (): WafPolicyConfig => JSON.parse(JSON.stringify(shippedPolicy));

/**
 * The Common Rule Set overrides the shipped policy declares, as the ACL renders them. The exact set
 * is pinned by `wafCommonRuleSetOverrides.test.ts`; here it is what the exclusion must keep.
 */
const shippedCommonOverrides = (shippedPolicy.managedRuleGroups || [])
    .filter((group) => group.managedRuleGroupName === COMMON_RULE_SET)
    .flatMap((group) => group.ruleActionOverrides || [])
    .map((override) => ({
        Name: override.name,
        ActionToUse: { [override.action.charAt(0).toUpperCase() + override.action.slice(1)]: {} },
    }));

/**
 * An exclusion with the shape an add-on supplies: two Common Rule Set body rules relaxed under one
 * route prefix, naming one rule group it leaves enforced on that prefix.
 */
const fixtureExclusions: WafScopedRuleExclusion[] = [
    {
        managedRuleGroupName: COMMON_RULE_SET,
        rules: [
            { name: SSRF_RULE, label: SSRF_LABEL },
            { name: XSS_RULE, label: XSS_LABEL },
        ],
        uriPathPrefixes: [FIXTURE_PREFIX],
        reapplyRuleName: REBLOCK_RULE,
        unrelaxedRules: [{ managedRuleGroupName: LINUX_RULE_SET }],
    },
];

const synthAcl = (
    wafScope: WAFScope,
    wafPolicy?: WafPolicyConfig,
    scopedRuleExclusions?: WafScopedRuleExclusion[],
    rules?: Array<wafv2.CfnWebACL.RuleProperty>
): any => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "WafExclusionTestStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope, wafPolicy, scopedRuleExclusions, rules });
    const acls = Object.values(Template.fromStack(stack).findResources("AWS::WAFv2::WebACL"));
    expect(acls).toHaveLength(1);
    return acls[0];
};

const rulesOf = (acl: any): any[] => acl.Properties.Rules;

const ruleNamed = (acl: any, name: string): any => rulesOf(acl).find((r) => r.Name === name);

const commonRuleSetOf = (acl: any): any =>
    rulesOf(acl).find((r) => r.Statement.ManagedRuleGroupStatement?.Name === COMMON_RULE_SET);

const overridesOf = (acl: any): any[] =>
    commonRuleSetOf(acl).Statement.ManagedRuleGroupStatement.RuleActionOverrides || [];

const labelMatch = (key: string): any => ({ LabelMatchStatement: { Scope: "LABEL", Key: key } });

/** A text transformation as the template renders it. */
interface TextTransformationJson {
    Priority: number;
    Type: string;
}

/** The rendered statement shapes the re-block rules use. */
interface StatementJson {
    AndStatement?: { Statements: StatementJson[] };
    OrStatement?: { Statements: StatementJson[] };
    NotStatement?: { Statement: StatementJson };
    LabelMatchStatement?: { Scope: string; Key: string };
    ByteMatchStatement?: {
        SearchString: string;
        FieldToMatch: unknown;
        TextTransformations: TextTransformationJson[];
        PositionalConstraint: string;
    };
}

const RAW_PATH: TextTransformationJson[] = [{ Priority: 0, Type: "NONE" }];
const NORMALIZED_PATH: TextTransformationJson[] = [
    { Priority: 0, Type: "URL_DECODE" },
    { Priority: 1, Type: "NORMALIZE_PATH" },
];

const uriPathMatch = (
    searchString: string,
    positionalConstraint: string,
    textTransformations: TextTransformationJson[]
): StatementJson => ({
    ByteMatchStatement: {
        SearchString: searchString,
        FieldToMatch: { UriPath: {} },
        TextTransformations: textTransformations,
        PositionalConstraint: positionalConstraint,
    },
});

/** The path, in one form, is the prefix itself or a path below it (a segment boundary). */
const atOrBelow = (
    prefix: string,
    textTransformations: TextTransformationJson[]
): StatementJson => ({
    OrStatement: {
        Statements: [
            uriPathMatch(prefix, "EXACTLY", textTransformations),
            uriPathMatch(`${prefix}/`, "STARTS_WITH", textTransformations),
        ],
    },
});

/** The raw path and its decoded, normalized form must both be at or below the prefix. */
const prefixMatch = (prefix: string): StatementJson => ({
    AndStatement: {
        Statements: [atOrBelow(prefix, RAW_PATH), atOrBelow(prefix, NORMALIZED_PATH)],
    },
});

/**
 * A model of how AWS WAF evaluates the statements the re-block rules use, for the behaviour tables
 * below: label match, AND / OR / NOT, and an EXACTLY or STARTS_WITH byte match on the URI path after
 * NONE, URL_DECODE or NORMALIZE_PATH. NORMALIZE_PATH removes repeated slashes and resolves `.` and
 * `..` segments that are not at the start of the path, as the AWS WAF text transformation reference
 * describes. Any other statement fails the test rather than being guessed at.
 */
interface ModelRequest {
    path: string;
    labels: string[];
}

const urlDecode = (value: string): string =>
    value.replace(/%([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));

const normalizePath = (value: string): string => {
    const segments = value.replace(/\/{2,}/g, "/").split("/");
    const out: string[] = [];
    segments.forEach((segment, index) => {
        const last = index === segments.length - 1;
        if (segment === "." && index > 0) {
            if (last) out.push("");
        } else if (segment === ".." && out.length > 1) {
            out.pop();
            if (last) out.push("");
        } else {
            out.push(segment);
        }
    });
    return out.join("/");
};

const transformPath = (path: string, textTransformations: TextTransformationJson[]): string =>
    [...textTransformations]
        .sort((a, b) => a.Priority - b.Priority)
        .reduce((value, { Type }) => {
            if (Type === "NONE") return value;
            if (Type === "URL_DECODE") return urlDecode(value);
            if (Type === "NORMALIZE_PATH") return normalizePath(value);
            throw new Error(`unmodelled text transformation ${Type}`);
        }, path);

const modelMatches = (statement: StatementJson, request: ModelRequest): boolean => {
    if (statement.AndStatement) {
        return statement.AndStatement.Statements.every((s) => modelMatches(s, request));
    }
    if (statement.OrStatement) {
        return statement.OrStatement.Statements.some((s) => modelMatches(s, request));
    }
    if (statement.NotStatement) {
        return !modelMatches(statement.NotStatement.Statement, request);
    }
    if (statement.LabelMatchStatement) {
        expect(statement.LabelMatchStatement.Scope).toBe("LABEL");
        return request.labels.includes(statement.LabelMatchStatement.Key);
    }
    if (statement.ByteMatchStatement) {
        const match = statement.ByteMatchStatement;
        expect(match.FieldToMatch).toEqual({ UriPath: {} });
        const value = transformPath(request.path, match.TextTransformations);
        if (match.PositionalConstraint === "EXACTLY") return value === match.SearchString;
        if (match.PositionalConstraint === "STARTS_WITH") {
            return value.startsWith(match.SearchString);
        }
    }
    throw new Error(`unmodelled statement ${JSON.stringify(statement)}`);
};

/** The name of the first re-block rule, by priority, that blocks the request in the model. */
const reblockedBy = (acl: unknown, request: ModelRequest): string | undefined =>
    [...rulesOf(acl)]
        .filter((r) => r.Action?.Block && !r.Statement.RateBasedStatement)
        .sort((a, b) => a.Priority - b.Priority)
        .find((r) => modelMatches(r.Statement, request))?.Name;

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL with a scoped rule exclusion",
    (wafScope) => {
        const acl = synthAcl(wafScope, shippedPolicy, fixtureExclusions);
        const baseline = synthAcl(wafScope, shippedPolicy);

        test("is emitted at the scope under test", () => {
            expect(acl.Properties.Scope).toBe(wafScope.toString());
        });

        test("counts both body rules in the Common Rule Set, keeping the policy's overrides", () => {
            const overrides = overridesOf(acl);
            // Control: the shipped policy overrides rules of its own, so "keeping" them is tested.
            expect(shippedCommonOverrides.length).toBeGreaterThan(0);
            expect(overrides).toHaveLength(shippedCommonOverrides.length + 2);
            expect(overrides).toEqual(
                expect.arrayContaining([
                    ...shippedCommonOverrides,
                    { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                    { Name: XSS_RULE, ActionToUse: { Count: {} } },
                ])
            );
            // The rest of the group stays in block mode.
            expect(commonRuleSetOf(acl).OverrideAction).toEqual({ None: {} });
        });

        test("re-blocks the relaxed rules after every other rule", () => {
            const rule = ruleNamed(acl, REBLOCK_RULE);
            expect(rule).toBeDefined();
            expect(rule.Action).toEqual({ Block: {} });
            rulesOf(acl)
                .filter((r) => r.Name !== REBLOCK_RULE)
                .forEach((other) => expect(rule.Priority).toBeGreaterThan(other.Priority));
        });

        test("blocks either label unless the raw and normalized paths are at or below the prefix", () => {
            expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                AndStatement: {
                    Statements: [
                        {
                            OrStatement: {
                                Statements: [labelMatch(SSRF_LABEL), labelMatch(XSS_LABEL)],
                            },
                        },
                        { NotStatement: { Statement: prefixMatch(FIXTURE_PREFIX) } },
                    ],
                },
            });
        });

        test("records metrics and samples under its own name", () => {
            expect(ruleNamed(acl, REBLOCK_RULE).VisibilityConfig).toEqual({
                SampledRequestsEnabled: true,
                CloudWatchMetricsEnabled: true,
                MetricName: REBLOCK_RULE,
            });
        });

        test("leaves every other rule as the policy alone builds it", () => {
            expect(rulesOf(acl)).toHaveLength(rulesOf(baseline).length + 1);
            const untouched = (a: any) =>
                rulesOf(a).filter((r) => r.Name !== REBLOCK_RULE && r !== commonRuleSetOf(a));
            expect(untouched(acl)).toEqual(untouched(baseline));
        });
    }
);

describe("a CfWafStack passes its scoped rule exclusions to its web ACL", () => {
    test.each([
        [WAFScope.CLOUDFRONT, "us-east-1"],
        [WAFScope.REGIONAL, "us-west-2"],
    ] as const)("a %s-scoped CfWafStack applies them to its web ACL", (wafScope, region) => {
        const app = newTestApp();
        const stack = new CfWafStack(app, `waf-${wafScope}`, {
            stackName: `waf-${wafScope}`,
            env: { account: "111111111111", region },
            wafScope,
            wafPolicy: shippedPolicy,
            scopedRuleExclusions: fixtureExclusions,
            description: "WAF",
        });
        const acls = Object.values(Template.fromStack(stack).findResources("AWS::WAFv2::WebACL"));
        expect(acls).toHaveLength(1);
        expect(acls[0].Properties.Scope).toBe(wafScope.toString());
        expect(ruleNamed(acls[0], REBLOCK_RULE)?.Action).toEqual({ Block: {} });
        expect(overridesOf(acls[0])).toEqual(
            expect.arrayContaining([
                { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                { Name: XSS_RULE, ActionToUse: { Count: {} } },
            ])
        );
    });
});

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL without the exclusion",
    (wafScope) => {
        const baseline = synthAcl(wafScope, shippedPolicy);

        test("the policy alone emits its own rules and its own Common Rule Set overrides", () => {
            const policyRuleNames = [
                ...(shippedPolicy.managedRuleGroups || []).map((g) => g.name),
                ...(shippedPolicy.rateBasedRules || []).map((r) => r.name),
            ];
            expect(rulesOf(baseline).map((r) => r.Name)).toEqual(policyRuleNames);
            expect(overridesOf(baseline)).toEqual(shippedCommonOverrides);
        });

        test("is unchanged when the exclusion list is empty", () => {
            expect(rulesOf(synthAcl(wafScope, shippedPolicy, []))).toEqual(rulesOf(baseline));
        });
    }
);

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL whose policy already overrides a relaxed rule",
    (wafScope) => {
        const policyOverriding = (
            overrides: Array<{ name: string; action: "count" | "block" | "allow" }>
        ): WafPolicyConfig => {
            const policy = clonePolicy();
            const group = (policy.managedRuleGroups || []).find(
                (g) => g.managedRuleGroupName === COMMON_RULE_SET
            );
            // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
            group!.ruleActionOverrides = [...(group!.ruleActionOverrides || []), ...overrides];
            return policy;
        };

        test.each([
            ["count", { Count: {} }],
            ["allow", { Allow: {} }],
        ] as const)(
            "keeps the policy's %s override and re-blocks only the other label",
            (action, actionToUse) => {
                const acl = synthAcl(
                    wafScope,
                    policyOverriding([{ name: XSS_RULE, action }]),
                    fixtureExclusions
                );
                const xssOverrides = overridesOf(acl).filter((o) => o.Name === XSS_RULE);
                expect(xssOverrides).toEqual([{ Name: XSS_RULE, ActionToUse: actionToUse }]);
                expect(overridesOf(acl)).toContainEqual({
                    Name: SSRF_RULE,
                    ActionToUse: { Count: {} },
                });
                // One label: the label match stands alone, since WAF requires an OR of two or more.
                expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                    AndStatement: {
                        Statements: [
                            labelMatch(SSRF_LABEL),
                            { NotStatement: { Statement: prefixMatch(FIXTURE_PREFIX) } },
                        ],
                    },
                });
            }
        );

        test("relaxes a rule the policy overrides to block and re-blocks both labels", () => {
            const acl = synthAcl(
                wafScope,
                policyOverriding([{ name: XSS_RULE, action: "block" }]),
                fixtureExclusions
            );
            expect(overridesOf(acl).filter((o) => o.Name === XSS_RULE)).toEqual([
                { Name: XSS_RULE, ActionToUse: { Count: {} } },
            ]);
            expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                AndStatement: {
                    Statements: [
                        {
                            OrStatement: {
                                Statements: [labelMatch(SSRF_LABEL), labelMatch(XSS_LABEL)],
                            },
                        },
                        { NotStatement: { Statement: prefixMatch(FIXTURE_PREFIX) } },
                    ],
                },
            });
        });

        test("adds no re-block rule when the policy overrides both relaxed rules", () => {
            const policy = policyOverriding([
                { name: SSRF_RULE, action: "count" },
                { name: XSS_RULE, action: "count" },
            ]);
            const acl = synthAcl(wafScope, policy, fixtureExclusions);
            expect(ruleNamed(acl, REBLOCK_RULE)).toBeUndefined();
            expect(rulesOf(acl)).toEqual(rulesOf(synthAcl(wafScope, policy)));
        });
    }
);

describe("scoped rule exclusions outside the shipped policy", () => {
    const wafScope = WAFScope.REGIONAL;

    test("a group in count-only mode gets no override and no re-block rule", () => {
        const policy = clonePolicy();
        (policy.managedRuleGroups || []).forEach((g) => (g.block = false));
        expect(rulesOf(synthAcl(wafScope, policy, fixtureExclusions))).toEqual(
            rulesOf(synthAcl(wafScope, policy))
        );
    });

    test("a policy without the named group gets no re-block rule", () => {
        const policy = clonePolicy();
        policy.managedRuleGroups = (policy.managedRuleGroups || []).filter(
            (g) => g.managedRuleGroupName !== COMMON_RULE_SET
        );
        expect(rulesOf(synthAcl(wafScope, policy, fixtureExclusions))).toEqual(
            rulesOf(synthAcl(wafScope, policy))
        );
    });

    test("an exclusion with no path prefix relaxes nothing and reports no unrelaxed rule", () => {
        // A policy enforcing the fixture exclusion's unrelaxed group, so the exclusion with its
        // prefix warns (the control) and the same exclusion without one must not.
        const policy = clonePolicy();
        (policy.managedRuleGroups || []).push({
            name: "AWS-AWSManagedRulesLinuxRuleSet",
            vendorName: "AWS",
            managedRuleGroupName: LINUX_RULE_SET,
            priority: 4,
            block: true,
        });
        const unrelaxedWarnings = (exclusions: WafScopedRuleExclusion[]): unknown[] => {
            const app = newTestApp();
            const stack = new cdk.Stack(app, "WafUnrelaxedWarningStack");
            new Wafv2BasicConstruct(stack, "Waf", {
                wafScope,
                wafPolicy: policy,
                scopedRuleExclusions: exclusions,
            });
            return Annotations.fromStack(stack).findWarning(
                "*",
                Match.stringLikeRegexp("does not relax")
            );
        };
        const withoutPrefix = fixtureExclusions.map((e) => ({ ...e, uriPathPrefixes: [] }));
        expect(unrelaxedWarnings(fixtureExclusions)).toHaveLength(1);
        expect(unrelaxedWarnings(withoutPrefix)).toEqual([]);
        expect(rulesOf(synthAcl(wafScope, policy, withoutPrefix))).toEqual(
            rulesOf(synthAcl(wafScope, policy))
        );
    });

    test("a second group naming the same managed group re-blocks each label once", () => {
        const policy = clonePolicy();
        const commonRuleSet = (policy.managedRuleGroups || []).find(
            (g) => g.managedRuleGroupName === COMMON_RULE_SET
        );
        (policy.managedRuleGroups || []).push({
            ...JSON.parse(JSON.stringify(commonRuleSet)),
            name: "AWS-AWSManagedRulesCommonRuleSet-2",
            priority: 4,
        });
        const acl = synthAcl(wafScope, policy, fixtureExclusions);
        expect(ruleNamed(acl, REBLOCK_RULE).Statement.AndStatement.Statements[0]).toEqual({
            OrStatement: { Statements: [labelMatch(SSRF_LABEL), labelMatch(XSS_LABEL)] },
        });
        const commonRuleSetGroups = rulesOf(acl).filter(
            (r) => r.Statement.ManagedRuleGroupStatement?.Name === COMMON_RULE_SET
        );
        expect(commonRuleSetGroups.map((r) => r.Name)).toEqual([
            "AWS-AWSManagedRulesCommonRuleSet",
            "AWS-AWSManagedRulesCommonRuleSet-2",
        ]);
        commonRuleSetGroups.forEach((group) => {
            expect(group.Statement.ManagedRuleGroupStatement.RuleActionOverrides).toEqual(
                expect.arrayContaining([
                    { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                    { Name: XSS_RULE, ActionToUse: { Count: {} } },
                ])
            );
        });
    });

    test("the legacy count-only default ignores exclusions", () => {
        expect(rulesOf(synthAcl(wafScope, undefined, fixtureExclusions))).toEqual(
            rulesOf(synthAcl(wafScope))
        );
    });

    test("explicit rules ignore exclusions", () => {
        const explicit: Array<wafv2.CfnWebACL.RuleProperty> = [
            {
                name: "Explicit",
                priority: 1,
                overrideAction: { none: {} },
                statement: {
                    managedRuleGroupStatement: { vendorName: "AWS", name: COMMON_RULE_SET },
                },
                visibilityConfig: {
                    sampledRequestsEnabled: true,
                    cloudWatchMetricsEnabled: true,
                    metricName: "Explicit",
                },
            },
        ];
        const acl = synthAcl(wafScope, shippedPolicy, fixtureExclusions, explicit);
        expect(rulesOf(acl).map((r) => r.Name)).toEqual(["Explicit"]);
        expect(commonRuleSetOf(acl).Statement.ManagedRuleGroupStatement.RuleActionOverrides).toBe(
            undefined
        );
    });

    test("several exclusions each get a rule above every policy rule, in order", () => {
        const second: WafScopedRuleExclusion = {
            managedRuleGroupName: COMMON_RULE_SET,
            rules: [
                {
                    name: "NoUserAgent_HEADER",
                    label: "awswaf:managed:aws:core-rule-set:NoUserAgent_Header",
                },
            ],
            uriPathPrefixes: ["/api/one", "/api/two"],
            reapplyRuleName: "VAMS-Second",
        };
        const acl = synthAcl(wafScope, shippedPolicy, [...fixtureExclusions, second]);
        const maxPolicyPriority = Math.max(
            ...rulesOf(synthAcl(wafScope, shippedPolicy)).map((r) => r.Priority)
        );
        expect(ruleNamed(acl, REBLOCK_RULE).Priority).toBe(maxPolicyPriority + 1);
        expect(ruleNamed(acl, "VAMS-Second").Priority).toBe(maxPolicyPriority + 2);
        // Several prefixes: the exemption is an OR of one prefix match each.
        expect(ruleNamed(acl, "VAMS-Second").Statement).toEqual({
            AndStatement: {
                Statements: [
                    labelMatch("awswaf:managed:aws:core-rule-set:NoUserAgent_Header"),
                    {
                        NotStatement: {
                            Statement: {
                                OrStatement: {
                                    Statements: [prefixMatch("/api/one"), prefixMatch("/api/two")],
                                },
                            },
                        },
                    },
                ],
            },
        });
    });
});

describe("a scoped exclusion's prefix covers the path and the paths below it only", () => {
    const wafScope = WAFScope.REGIONAL;
    const acl = synthAcl(wafScope, shippedPolicy, fixtureExclusions);

    test("a trailing slash on the prefix builds the same rule", () => {
        const withSlash = fixtureExclusions.map((e) => ({
            ...e,
            uriPathPrefixes: [`${FIXTURE_PREFIX}/`],
        }));
        expect(rulesOf(synthAcl(wafScope, shippedPolicy, withSlash))).toEqual(rulesOf(acl));
    });

    test.each([
        [FIXTURE_PREFIX],
        [`${FIXTURE_PREFIX}/`],
        [`${FIXTURE_PREFIX}/item`],
        [`${FIXTURE_PREFIX}/item/part`],
    ])("exempts %s", (path) => {
        expect(reblockedBy(acl, { path, labels: [XSS_LABEL] })).toBeUndefined();
    });

    test.each([
        // A sibling route whose segment begins with the prefix's last segment.
        [`${FIXTURE_PREFIX}foo`],
        [`${FIXTURE_PREFIX}foo/item`],
        // Another route.
        ["/api/fixture/bodie"],
        ["/api/assets/item"],
        // Dot segments, plain and encoded, leaving the prefix.
        [`${FIXTURE_PREFIX}/../../assets`],
        [`${FIXTURE_PREFIX}/%2e%2e/%2e%2e/assets`],
        // A sibling that normalizes onto the prefix: the raw path is not under it.
        [`${FIXTURE_PREFIX}foo/../bodies/item`],
        // An encoded slash inside one path parameter, a leading double slash, another case.
        ["/api/fixture%2Fbodies/item"],
        [`/${FIXTURE_PREFIX}/item`],
        [FIXTURE_PREFIX.toUpperCase() + "/item"],
    ])("re-blocks %s", (path) => {
        expect(reblockedBy(acl, { path, labels: [XSS_LABEL] })).toBe(REBLOCK_RULE);
    });

    test("blocks nothing without a relaxed label", () => {
        expect(reblockedBy(acl, { path: "/api/assets/item", labels: [] })).toBeUndefined();
    });
});

describe("two scoped exclusions relaxing the same rule", () => {
    const wafScope = WAFScope.REGIONAL;
    const ADDON_A = "/api/addon/a";
    const ADDON_B = "/api/addon/b";
    const exclusionsSharingRfi: WafScopedRuleExclusion[] = [
        {
            managedRuleGroupName: COMMON_RULE_SET,
            rules: [
                { name: XSS_RULE, label: XSS_LABEL },
                { name: SSRF_RULE, label: SSRF_LABEL },
            ],
            uriPathPrefixes: [ADDON_A],
            reapplyRuleName: "VAMS-A",
        },
        {
            managedRuleGroupName: COMMON_RULE_SET,
            rules: [{ name: XSS_RULE, label: XSS_LABEL }],
            uriPathPrefixes: [ADDON_B],
            reapplyRuleName: "VAMS-B",
        },
    ];
    const acl = synthAcl(wafScope, shippedPolicy, exclusionsSharingRfi);

    test.each([
        [`${ADDON_A}/item`, [XSS_LABEL], undefined],
        [`${ADDON_B}/item`, [XSS_LABEL], undefined],
        ["/api/assets/item", [XSS_LABEL], "VAMS-A"],
        [`${ADDON_A}/item`, [SSRF_LABEL], undefined],
        // A label only the first exclusion relaxes stays scoped to its own prefix.
        [`${ADDON_B}/item`, [SSRF_LABEL], "VAMS-A"],
        [`${ADDON_B}/item`, [XSS_LABEL, SSRF_LABEL], "VAMS-A"],
    ] as const)("a request to %s carrying %j is re-blocked by %s", (path, labels, rule) => {
        expect(reblockedBy(acl, { path, labels: [...labels] })).toBe(rule);
    });

    test("each re-block rule exempts a shared label on the prefixes of every exclusion relaxing it", () => {
        const sharedRfi = {
            AndStatement: {
                Statements: [
                    labelMatch(XSS_LABEL),
                    {
                        NotStatement: {
                            Statement: {
                                OrStatement: {
                                    Statements: [prefixMatch(ADDON_A), prefixMatch(ADDON_B)],
                                },
                            },
                        },
                    },
                ],
            },
        };
        expect(ruleNamed(acl, "VAMS-A").Statement).toEqual({
            OrStatement: {
                Statements: [
                    sharedRfi,
                    {
                        AndStatement: {
                            Statements: [
                                labelMatch(SSRF_LABEL),
                                { NotStatement: { Statement: prefixMatch(ADDON_A) } },
                            ],
                        },
                    },
                ],
            },
        });
        expect(ruleNamed(acl, "VAMS-B").Statement).toEqual(sharedRfi);
    });

    test("keeps each exclusion's rule and metric name, in exclusion order after every policy rule", () => {
        const maxPolicyPriority = Math.max(
            ...rulesOf(synthAcl(wafScope, shippedPolicy)).map((r) => r.Priority)
        );
        ["VAMS-A", "VAMS-B"].forEach((name, index) => {
            const rule = ruleNamed(acl, name);
            expect(rule.Priority).toBe(maxPolicyPriority + 1 + index);
            expect(rule.Action).toEqual({ Block: {} });
            expect(rule.VisibilityConfig.MetricName).toBe(name);
        });
        expect(rulesOf(synthAcl(wafScope, shippedPolicy, exclusionsSharingRfi))).toEqual(
            rulesOf(acl)
        );
    });
});

describe("a scoped exclusion is validated at synth", () => {
    const wafScope = WAFScope.REGIONAL;
    const withExclusion = (change: Partial<WafScopedRuleExclusion>): WafScopedRuleExclusion[] => [
        { ...fixtureExclusions[0], ...change },
    ];
    const synthWith = (exclusions: WafScopedRuleExclusion[]) => () =>
        synthAcl(wafScope, shippedPolicy, exclusions);

    test("the fixture exclusion is accepted (control)", () => {
        expect(synthWith(fixtureExclusions)).not.toThrow();
    });

    describe("each label must be the named rule's documented label, exactly", () => {
        const rejectsLabel = (
            name: string,
            label: string,
            managedRuleGroupName = COMMON_RULE_SET
        ) =>
            expect(
                synthWith(withExclusion({ managedRuleGroupName, rules: [{ name, label }] }))
            ).toThrow(new RegExp(`label "${label}" of rule "${name}" is not that rule's label`));

        // The review's example: the rule name's letter case in place of the label's. AWS WAF would
        // accept it, count GenericRFI_BODY on every path and never re-block it.
        test("rejects the rule name's letter case in the label (GenericRFI_BODY for GenericRFI_Body)", () => {
            rejectsLabel(RFI_RULE, "awswaf:managed:aws:core-rule-set:GenericRFI_BODY");
        });

        test.each([
            ["the rule name used as the label", RFI_RULE, RFI_RULE, COMMON_RULE_SET],
            [
                "a lowercase label",
                RFI_RULE,
                "awswaf:managed:aws:core-rule-set:genericrfi_body",
                COMMON_RULE_SET,
            ],
            [
                "another rule's letter case (URIPath where the group documents UriPath)",
                "SQLiExtendedPatterns_URIPATH",
                "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_URIPath",
                "AWSManagedRulesSQLiRuleSet",
            ],
            [
                "the rule name in place of a label that drops part of it",
                "UserAgent_BadBots_HEADER",
                "awswaf:managed:aws:core-rule-set:UserAgent_BadBots_Header",
                COMMON_RULE_SET,
            ],
            [
                "another group's namespace",
                RFI_RULE,
                "awswaf:managed:aws:known-bad-inputs:GenericRFI_Body",
                COMMON_RULE_SET,
            ],
            [
                "another rule's label",
                RFI_RULE,
                "awswaf:managed:aws:core-rule-set:GenericLFI_Body",
                COMMON_RULE_SET,
            ],
            [
                "a label with an extra segment",
                RFI_RULE,
                "awswaf:managed:aws:core-rule-set:x:GenericRFI_Body",
                COMMON_RULE_SET,
            ],
            ["a label with a trailing space", RFI_RULE, `${RFI_LABEL} `, COMMON_RULE_SET],
        ])("rejects %s", (_, name, label, group) => {
            rejectsLabel(name, label, group);
        });

        test("rejects a rule the table does not list in a group it does", () => {
            expect(
                synthWith(
                    withExclusion({
                        rules: [
                            {
                                name: "GenericRFI_HEADER",
                                label: "awswaf:managed:aws:core-rule-set:GenericRFI_Header",
                            },
                        ],
                    })
                )
            ).toThrow(
                /rule "GenericRFI_HEADER" of AWSManagedRulesCommonRuleSet is not in AWS_MANAGED_RULE_LABELS.*Add the rule .*infra\/lib\/constructs\/wafv2-basic-construct\.ts/
            );
        });

        test("rejects a rule name inherited from Object.prototype", () => {
            expect(
                synthWith(
                    withExclusion({
                        rules: [
                            {
                                name: "toString",
                                label: "awswaf:managed:aws:core-rule-set:toString",
                            },
                        ],
                    })
                )
            ).toThrow(/rule "toString" of AWSManagedRulesCommonRuleSet is not in/);
        });

        test("rejects a managed rule group the table does not list", () => {
            expect(
                synthWith(
                    withExclusion({
                        managedRuleGroupName: "AWSManagedRulesBotControlRuleSet",
                        rules: [
                            {
                                name: "CategoryAI",
                                label: "awswaf:managed:aws:bot-control:bot:category:ai",
                            },
                        ],
                    })
                )
            ).toThrow(
                /AWSManagedRulesBotControlRuleSet.*not in AWS_MANAGED_RULE_LABELS.*Add the group .*infra\/lib\/constructs\/wafv2-basic-construct\.ts/
            );
        });

        // One documented label per group of the table, copied from the group's page in the AWS WAF
        // Developer Guide independently of the table, preferring a rule whose label's letter case
        // or wording differs from its name.
        const documentedLabelPerGroup: Record<string, { name: string; label: string }> = {
            AWSManagedRulesCommonRuleSet: {
                name: "UserAgent_BadBots_HEADER",
                label: "awswaf:managed:aws:core-rule-set:BadBots_Header",
            },
            AWSManagedRulesAdminProtectionRuleSet: {
                name: "AdminProtection_URIPATH",
                label: "awswaf:managed:aws:admin-protection:AdminProtection_URIPath",
            },
            AWSManagedRulesKnownBadInputsRuleSet: {
                name: "Host_localhost_HEADER",
                label: "awswaf:managed:aws:known-bad-inputs:Host_Localhost_Header",
            },
            AWSManagedRulesAmazonIpReputationList: {
                name: "AWSManagedIPReputationList",
                label: "awswaf:managed:aws:amazon-ip-list:AWSManagedIPReputationList",
            },
            AWSManagedRulesAnonymousIpList: {
                name: "HostingProviderIPList",
                label: "awswaf:managed:aws:anonymous-ip-list:HostingProviderIPList",
            },
            AWSManagedRulesSQLiRuleSet: {
                name: "SQLiExtendedPatterns_URIPATH",
                label: "awswaf:managed:aws:sql-database:SQLiExtendedPatterns_UriPath",
            },
            AWSManagedRulesLinuxRuleSet: {
                name: "LFI_QUERYSTRING",
                label: "awswaf:managed:aws:linux-os:LFI_QueryString",
            },
            AWSManagedRulesUnixRuleSet: {
                name: "UNIXShellCommandsVariables_BODY",
                label: "awswaf:managed:aws:posix-os:UNIXShellCommandsVariables_Body",
            },
            AWSManagedRulesWindowsRuleSet: {
                name: "WindowsShellCommands_URIPATH",
                label: "awswaf:managed:aws:windows-os:WindowsShellCommands_UriPath",
            },
            AWSManagedRulesPHPRuleSet: {
                name: "PHP2Shell_BODY_Extended",
                label: "awswaf:managed:aws:php-app:PHP2Shell_Body_Extended",
            },
            AWSManagedRulesWordPressRuleSet: {
                name: "WordPressExploitablePaths_URIPATH",
                label: "awswaf:managed:aws:wordpress-app:WordPressExploitablePaths_URIPATH",
            },
        };
        test.each(Object.entries(documentedLabelPerGroup))(
            "accepts the documented label of a rule of %s (control)",
            (managedRuleGroupName, rule) => {
                expect(
                    synthWith(withExclusion({ managedRuleGroupName, rules: [rule] }))
                ).not.toThrow();
            }
        );

        test("has a control for every group of the table", () => {
            expect(Object.keys(documentedLabelPerGroup).sort()).toEqual(
                Object.keys(AWS_MANAGED_RULE_LABELS).sort()
            );
        });

        test.each((shippedPolicy.managedRuleGroups || []).map((g) => g.managedRuleGroupName))(
            "lists the labels of %s, which the shipped policy enables",
            (managedRuleGroupName) => {
                expect(Object.keys(AWS_MANAGED_RULE_LABELS)).toContain(managedRuleGroupName);
            }
        );

        test("lists each label once, under its group's namespace", () => {
            const labels = Object.values(AWS_MANAGED_RULE_LABELS).flatMap((group) =>
                Object.values(group)
            );
            expect(new Set(labels).size).toBe(labels.length);
            for (const [group, rules] of Object.entries(AWS_MANAGED_RULE_LABELS)) {
                const namespaces = new Set(
                    Object.values(rules).map((label) => label.slice(0, label.lastIndexOf(":") + 1))
                );
                expect({ group, namespaces: [...namespaces].length }).toEqual({
                    group,
                    namespaces: 1,
                });
            }
        });
    });

    test("rejects an exclusion that relaxes no rule", () => {
        expect(synthWith(withExclusion({ rules: [] }))).toThrow(/relaxes no rule/);
    });

    test("rejects an empty URI path prefix", () => {
        expect(synthWith(withExclusion({ uriPathPrefixes: [""] }))).toThrow(
            /empty URI path prefix/
        );
    });

    test.each([["/"], ["/api"], ["/api/"]])(
        "rejects the prefix %s, which covers the whole API",
        (prefix) => {
            expect(synthWith(withExclusion({ uriPathPrefixes: [prefix] }))).toThrow(
                /covers the whole API/
            );
        }
    );

    test("rejects a prefix outside the API stage path", () => {
        expect(synthWith(withExclusion({ uriPathPrefixes: ["/fixture/bodies"] }))).toThrow(
            /does not start with "\/api\/"/
        );
    });

    test("rejects two exclusions with the same reapplyRuleName", () => {
        const second = { ...fixtureExclusions[0], uriPathPrefixes: ["/api/other"] };
        expect(synthWith([...fixtureExclusions, second])).toThrow(
            new RegExp(`more than one scoped rule exclusion is named "${REBLOCK_RULE}"`)
        );
    });

    test.each([
        ...(shippedPolicy.managedRuleGroups || []).map((g) => g.name),
        ...(shippedPolicy.rateBasedRules || []).map((r) => r.name),
    ])("rejects a reapplyRuleName equal to the policy rule %s", (name) => {
        expect(synthWith(withExclusion({ reapplyRuleName: name }))).toThrow(
            /also the name of a rule in the WAF policy/
        );
    });

    test.each([
        ["an empty name", ""],
        ["a space", "VAMS Fixture"],
        ["a colon", "VAMS:Fixture"],
        ["129 characters", "V".repeat(129)],
        ["the reserved metric name All", "All"],
        ["the reserved metric name Default_Action", "Default_Action"],
    ])("rejects a reapplyRuleName with %s", (_, name) => {
        expect(synthWith(withExclusion({ reapplyRuleName: name }))).toThrow(
            /not a valid AWS WAF rule and metric name/
        );
    });

    test("accepts a reapplyRuleName of 128 letters, digits, hyphens and underscores", () => {
        const name = `VAMS-Fixture_9${"x".repeat(114)}`;
        expect(name).toHaveLength(128);
        expect(synthWith(withExclusion({ reapplyRuleName: name }))).not.toThrow();
    });
});

describe("buildWafStacks", () => {
    afterEach(() => {
        jest.restoreAllMocks();
    });

    /** The commercial template's config with the shipped policy, as getConfig() would load it. */
    const configWith = (mutate: (c: any) => void): Config.Config => {
        const config = buildTemplateConfig("commercial", mutate);
        config.wafPolicyJSON = clonePolicy();
        return config;
    };
    const withCloudFront = () =>
        configWith((c) => {
            c.app.useCloudFront.enabled = true;
            c.app.useAlb.enabled = false;
        });
    const apiOnly = () =>
        configWith((c) => {
            c.app.useCloudFront.enabled = false;
            c.app.useAlb.enabled = false;
        });

    const aclOf = (stack: cdk.Stack): any => {
        const acls = Object.values(Template.fromStack(stack).findResources("AWS::WAFv2::WebACL"));
        expect(acls).toHaveLength(1);
        return acls[0];
    };

    /**
     * A stack holding the web ACL `Wafv2BasicConstruct` builds from the policy alone, in the same app
     * and environment as `like`, so a reference to another stack renders the same way in both. Built
     * before any template of the app is read.
     */
    const policyOnlyStack = (
        app: cdk.App,
        like: cdk.Stack,
        wafScope: WAFScope,
        wafPolicy: WafPolicyConfig | undefined,
        originVerifySecretArn?: string
    ): cdk.Stack => {
        const stack = new cdk.Stack(app, `PolicyOnly${wafScope}`, {
            env: { account: like.account, region: like.region },
        });
        new Wafv2BasicConstruct(stack, "Waf", { wafScope, wafPolicy, originVerifySecretArn });
        return stack;
    };

    test("builds no web ACL stack with WAF off", () => {
        const config = configWith((c) => {
            c.app.useWaf = false;
        });
        expect(buildWafStacks(newTestApp(), config)).toBeUndefined();
    });

    test("with CloudFront, keeps the stack names and Regions and wires the origin verification secret", () => {
        const app = newTestApp();
        const config = withCloudFront();
        const originVerify = buildApiOriginVerifyStack(app, config);
        expect(originVerify).toBeDefined();
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(app, config, originVerify)!;
        const base = config.app.baseStackName;

        expect(stacks.regional.stackName).toBe(`${config.name}-waf-regional-${base}`);
        expect(stacks.regional.region).toBe(config.env.region);
        expect(stacks.regional.dependencies).toContain(originVerify);
        expect(stacks.cloudfront?.stackName).toBe(`${config.name}-waf-${base}`);
        expect(stacks.cloudfront?.region).toBe("us-east-1");
        expect(stacks.cloudfront?.dependencies ?? []).not.toContain(originVerify);

        expect(aclOf(stacks.regional).Properties.Scope).toBe("REGIONAL");
        expect(aclOf(stacks.cloudfront as CfWafStack).Properties.Scope).toBe("CLOUDFRONT");
    });

    test("without CloudFront, keeps the regional stack name and builds no CloudFront stack", () => {
        const config = apiOnly();
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(newTestApp(), config)!;
        expect(stacks.regional.stackName).toBe(`${config.name}-waf-${config.app.baseStackName}`);
        expect(stacks.regional.region).toBe(config.env.region);
        expect(stacks.cloudfront).toBeUndefined();
    });

    test("the add-on hook supplies no exclusion on its own", () => {
        expect(addonWafExclusions.addonWafRuleExclusions(withCloudFront())).toEqual([]);
        expect(addonWafExclusions.addonWafRuleExclusions(apiOnly())).toEqual([]);
    });

    test("with no add-on exclusion, both web ACLs are the ones the policy alone builds", () => {
        const app = newTestApp();
        const config = withCloudFront();
        const originVerify = buildApiOriginVerifyStack(app, config);
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(app, config, originVerify)!;
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const cloudfront = stacks.cloudfront!;

        const regionalReference = policyOnlyStack(
            app,
            stacks.regional,
            WAFScope.REGIONAL,
            config.wafPolicyJSON,
            originVerify?.secretRef.secretArn
        );
        const edgeReference = policyOnlyStack(
            app,
            cloudfront,
            WAFScope.CLOUDFRONT,
            config.wafPolicyJSON
        );

        const regional = aclOf(stacks.regional).Properties;
        const regionalExpected = aclOf(regionalReference).Properties;
        // Control: the regional rate-based rules carry the origin verification scope-down.
        expect(JSON.stringify(regional.Rules)).toContain("ScopeDownStatement");
        expect(regional.Rules).toEqual(regionalExpected.Rules);
        expect(regional.CustomResponseBodies).toEqual(regionalExpected.CustomResponseBodies);

        const edge = aclOf(cloudfront).Properties;
        const edgeExpected = aclOf(edgeReference).Properties;
        // Control: the CloudFront ACL has no origin verification scope-down.
        expect(JSON.stringify(edge.Rules)).not.toContain("ScopeDownStatement");
        expect(edge.Rules).toEqual(edgeExpected.Rules);
        expect(edge.CustomResponseBodies).toEqual(edgeExpected.CustomResponseBodies);
    });

    test("without CloudFront and no add-on exclusion, the regional web ACL is the policy's alone", () => {
        const app = newTestApp();
        const config = apiOnly();
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(app, config)!;
        const reference = policyOnlyStack(
            app,
            stacks.regional,
            WAFScope.REGIONAL,
            config.wafPolicyJSON
        );
        expect(aclOf(stacks.regional).Properties.Rules).toEqual(aclOf(reference).Properties.Rules);
    });

    test("applies the add-on exclusions to both web ACLs", () => {
        jest.spyOn(addonWafExclusions, "addonWafRuleExclusions").mockReturnValue(fixtureExclusions);
        const app = newTestApp();
        const config = withCloudFront();
        const originVerify = buildApiOriginVerifyStack(app, config);
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(app, config, originVerify)!;
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const cloudfront = stacks.cloudfront!;

        // A request to `<distribution>/api/*` meets the CloudFront ACL first, on the viewer's path,
        // which CloudFront forwards to the stage with no origin path; without the exclusions there it
        // would be blocked before the regional ACL saw it.
        const edgeReference = new cdk.Stack(app, "EdgeWithExclusions", {
            env: { account: cloudfront.account, region: cloudfront.region },
        });
        new Wafv2BasicConstruct(edgeReference, "Waf", {
            wafScope: WAFScope.CLOUDFRONT,
            wafPolicy: config.wafPolicyJSON,
            scopedRuleExclusions: fixtureExclusions,
        });

        const regional = aclOf(stacks.regional);
        const edge = aclOf(cloudfront);
        for (const acl of [regional, edge]) {
            expect(ruleNamed(acl, REBLOCK_RULE)?.Action).toEqual({ Block: {} });
            expect(overridesOf(acl)).toEqual(
                expect.arrayContaining([
                    { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                    { Name: XSS_RULE, ActionToUse: { Count: {} } },
                ])
            );
        }
        expect(rulesOf(edge)).toEqual(rulesOf(aclOf(edgeReference)));
        // One prefix form serves both ACLs, so the re-block statements are the same.
        expect(ruleNamed(edge, REBLOCK_RULE).Statement).toEqual(
            ruleNamed(regional, REBLOCK_RULE).Statement
        );
        // Control: the CloudFront ACL still has no origin verification scope-down.
        expect(JSON.stringify(rulesOf(edge))).not.toContain("ScopeDownStatement");
    });
});
