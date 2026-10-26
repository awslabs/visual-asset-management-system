/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Managed rules relaxed on a set of URI paths only.
 *
 * A scoped rule exclusion sets named managed rules to count inside their group and adds one VAMS
 * rule that blocks on their labels everywhere except under the exclusion's URI path prefixes. The
 * enabled add-ons supply the regional ACL's exclusions through `addonWafRuleExclusions()`; the
 * fixture exclusion here has the same shape, two Common Rule Set body rules relaxed under one
 * prefix.
 *
 * What is pinned here, for both ACL scopes and driven by the shipped policy:
 *   * the relaxed rules are overridden to count and the policy's own overrides are kept;
 *   * the re-block rule is evaluated after every other rule, blocks, matches either rule's label
 *     and exempts only a path whose raw form, which API Gateway routes on, and whose URL-decoded,
 *     normalized form both start with the prefix, so neither an encoded slash inside a path
 *     parameter nor a dot segment lets a request to another route borrow the exemption;
 *   * a CfWafStack passes the exclusions to its web ACL;
 *   * with no exclusion supplied, or an empty list, the ACL is the one the policy alone produces;
 *   * a rule the policy counts or allows keeps the policy's action and is not re-blocked; a rule
 *     the policy blocks is relaxed and re-blocked like any other;
 *   * a label is re-blocked once, however many policy groups name the managed group;
 *   * an exclusion with no path prefix relaxes nothing and reports no unrelaxed rule;
 *   * the explicit-rules and legacy count-only paths ignore exclusions;
 *   * `buildWafStacks()` keeps the stack names, Regions and origin verification wiring, applies the
 *     add-on exclusions to the regional web ACL only, and with no add-on exclusion builds both web
 *     ACLs from the policy alone.
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
            { name: RFI_RULE, label: RFI_LABEL },
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

const uriPathStartsWith = (prefix: string, textTransformations: any[]): any => ({
    ByteMatchStatement: {
        SearchString: prefix,
        FieldToMatch: { UriPath: {} },
        TextTransformations: textTransformations,
        PositionalConstraint: "STARTS_WITH",
    },
});

/** The raw path and its decoded, normalized form must both start with the prefix. */
const prefixMatch = (prefix: string): any => ({
    AndStatement: {
        Statements: [
            uriPathStartsWith(prefix, [{ Priority: 0, Type: "NONE" }]),
            uriPathStartsWith(prefix, [
                { Priority: 0, Type: "URL_DECODE" },
                { Priority: 1, Type: "NORMALIZE_PATH" },
            ]),
        ],
    },
});

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
                    { Name: RFI_RULE, ActionToUse: { Count: {} } },
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

        test("blocks either label unless the raw and normalized paths start with the prefix", () => {
            expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                AndStatement: {
                    Statements: [
                        {
                            OrStatement: {
                                Statements: [labelMatch(SSRF_LABEL), labelMatch(RFI_LABEL)],
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
                { Name: RFI_RULE, ActionToUse: { Count: {} } },
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
                    policyOverriding([{ name: RFI_RULE, action }]),
                    fixtureExclusions
                );
                const rfiOverrides = overridesOf(acl).filter((o) => o.Name === RFI_RULE);
                expect(rfiOverrides).toEqual([{ Name: RFI_RULE, ActionToUse: actionToUse }]);
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
                policyOverriding([{ name: RFI_RULE, action: "block" }]),
                fixtureExclusions
            );
            expect(overridesOf(acl).filter((o) => o.Name === RFI_RULE)).toEqual([
                { Name: RFI_RULE, ActionToUse: { Count: {} } },
            ]);
            expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                AndStatement: {
                    Statements: [
                        {
                            OrStatement: {
                                Statements: [labelMatch(SSRF_LABEL), labelMatch(RFI_LABEL)],
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
                { name: RFI_RULE, action: "count" },
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
            OrStatement: { Statements: [labelMatch(SSRF_LABEL), labelMatch(RFI_LABEL)] },
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
                    { Name: RFI_RULE, ActionToUse: { Count: {} } },
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
                    name: "CrossSiteScripting_BODY",
                    label: "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body",
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
                    labelMatch("awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body"),
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

    test("applies the add-on exclusions to the regional web ACL only", () => {
        jest.spyOn(addonWafExclusions, "addonWafRuleExclusions").mockReturnValue(fixtureExclusions);
        const app = newTestApp();
        const config = withCloudFront();
        const originVerify = buildApiOriginVerifyStack(app, config);
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const stacks = buildWafStacks(app, config, originVerify)!;
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const cloudfront = stacks.cloudfront!;
        const edgeReference = policyOnlyStack(
            app,
            cloudfront,
            WAFScope.CLOUDFRONT,
            config.wafPolicyJSON
        );

        const regional = aclOf(stacks.regional);
        expect(ruleNamed(regional, REBLOCK_RULE)?.Action).toEqual({ Block: {} });
        expect(overridesOf(regional)).toEqual(
            expect.arrayContaining([
                { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                { Name: RFI_RULE, ActionToUse: { Count: {} } },
            ])
        );

        const edge = aclOf(cloudfront);
        expect(ruleNamed(edge, REBLOCK_RULE)).toBeUndefined();
        expect(rulesOf(edge)).toEqual(rulesOf(aclOf(edgeReference)));
    });
});
