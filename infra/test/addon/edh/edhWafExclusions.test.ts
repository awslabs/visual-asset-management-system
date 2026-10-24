/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH Workstations add-on's web ACL rule exclusion.
 *
 * The AMI recipe admin routes carry Image Builder component scripts in their request bodies, and
 * the shipped Linux recipes' components read the instance metadata service at
 * `http://169.254.169.254/...`. The Common Rule Set's EC2MetaDataSSRF_BODY and GenericRFI_BODY
 * rules block such a body, so with the add-on's AMI builds on, both rules count inside the managed
 * group and one VAMS rule blocks on their labels everywhere except under the recipe route prefix.
 *
 * What is pinned here:
 *   * `buildEdhWafRuleExclusions` returns the exclusion only while the add-on and its AMI builds are
 *     on, and `addonWafRuleExclusions` carries it to the regional web ACL;
 *   * the prefix covers every AMI recipe route the add-on stack registers;
 *   * for both ACL scopes and the shipped policy, the two rules count, the policy's own overrides
 *     are kept, and the re-block rule, after every other rule, exempts only the recipe prefix;
 *   * bin/infra.ts builds the WAF stacks through buildWafStacks, whose variations
 *     `edhWafVariations.test.ts` pins.
 *
 * The exclusion mechanism itself is pinned with a fixture exclusion in
 * `test/waf/wafScopedRuleExclusions.test.ts`.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../../config/config";
import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
    WafScopedRuleExclusion,
} from "../../../lib/constructs/wafv2-basic-construct";
import { addonWafRuleExclusions } from "../../../lib/nestedStacks/addon/addonWafExclusions";
import { buildEdhWafRuleExclusions } from "../../../lib/nestedStacks/addon/edhWorkstations/edhWafExclusions";
import { newTestApp } from "../../support/testApp";

const COMMON_RULE_SET = "AWSManagedRulesCommonRuleSet";
const SSRF_RULE = "EC2MetaDataSSRF_BODY";
const RFI_RULE = "GenericRFI_BODY";
const SSRF_LABEL = "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body";
const RFI_LABEL = "awswaf:managed:aws:core-rule-set:GenericRFI_Body";
const RECIPE_PREFIX = "/api/addon/edh/admin/ami-recipes";
const REBLOCK_RULE = "VAMS-EdhAmiRecipeBodyRules";

/** The policy that ships with the deployment, as `wafRateLimit.test.ts` reads it. */
const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "..", "config", "policy", "wafPolicyConfig.json"), {
        encoding: "utf8",
    })
);

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

/** The two config switches the EDH helper reads; nothing else in the config is consulted. */
const edhConfig = (edhEnabled: boolean, amiBuildsEnabled: boolean): Config.Config =>
    ({
        app: {
            addons: {
                useEdhWorkstations: {
                    enabled: edhEnabled,
                    amiBuilds: { enabled: amiBuildsEnabled },
                },
            },
        },
    } as unknown as Config.Config);

const edhExclusions = buildEdhWafRuleExclusions(edhConfig(true, true));

const synthAcl = (
    wafScope: WAFScope,
    wafPolicy?: WafPolicyConfig,
    scopedRuleExclusions?: WafScopedRuleExclusion[]
): any => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "EdhWafExclusionTestStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope, wafPolicy, scopedRuleExclusions });
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

describe("buildEdhWafRuleExclusions", () => {
    test.each([
        ["the add-on is off", false, false],
        ["the add-on is on with AMI builds off", true, false],
        ["AMI builds are on without the add-on", false, true],
    ])("returns no exclusion when %s", (_case, edhEnabled, amiBuildsEnabled) => {
        expect(buildEdhWafRuleExclusions(edhConfig(edhEnabled, amiBuildsEnabled))).toEqual([]);
    });

    test("returns the AMI recipe body exclusion when the add-on and AMI builds are on", () => {
        expect(edhExclusions).toEqual([
            {
                managedRuleGroupName: COMMON_RULE_SET,
                rules: [
                    { name: SSRF_RULE, label: SSRF_LABEL },
                    { name: RFI_RULE, label: RFI_LABEL },
                ],
                uriPathPrefixes: [RECIPE_PREFIX],
                reapplyRuleName: REBLOCK_RULE,
                unrelaxedRules: [
                    { managedRuleGroupName: COMMON_RULE_SET, name: "SizeRestrictions_BODY" },
                    { managedRuleGroupName: "AWSManagedRulesLinuxRuleSet" },
                    { managedRuleGroupName: "AWSManagedRulesUnixRuleSet" },
                    { managedRuleGroupName: "AWSManagedRulesWindowsRuleSet" },
                    { managedRuleGroupName: "AWSManagedRulesSQLiRuleSet" },
                    { managedRuleGroupName: "AWSManagedRulesPHPRuleSet" },
                ],
            },
        ]);
        expect(RECIPE_PREFIX.startsWith(`/${Config.API_GATEWAY_STAGE_NAME}/`)).toBe(true);
    });

    test("the prefix covers the AMI recipe routes the add-on stack registers", () => {
        const source = readFileSync(
            join(
                __dirname,
                "..",
                "..",
                "..",
                "lib",
                "nestedStacks",
                "addon",
                "edhWorkstations",
                "edhWorkstationsBuilder-nestedStack.ts"
            ),
            { encoding: "utf8" }
        );
        const routePaths = Array.from(source.matchAll(/routePath:\s*"([^"]+)"/g), (m) => m[1]);
        // Positive control: the collection and the per-recipe routes are both registered, so a
        // renamed route fails here rather than leaving the prefix pointing at nothing.
        expect(routePaths).toContain("/addon/edh/admin/ami-recipes");
        expect(routePaths).toContain("/addon/edh/admin/ami-recipes/{recipeId}");
        const recipeRoutes = routePaths.filter((p) => p.includes("/ami-recipes"));
        recipeRoutes.forEach((routePath) => {
            expect(`/${Config.API_GATEWAY_STAGE_NAME}${routePath}`.startsWith(RECIPE_PREFIX)).toBe(
                true
            );
        });
    });
});

describe("addonWafRuleExclusions", () => {
    test("carries the EDH exclusion while the add-on and its AMI builds are on", () => {
        expect(addonWafRuleExclusions(edhConfig(true, true))).toEqual(
            expect.arrayContaining(edhExclusions)
        );
    });

    test.each([
        ["the add-on is off", false, false],
        ["the add-on is on with AMI builds off", true, false],
    ])("carries no EDH exclusion when %s", (_case, edhEnabled, amiBuildsEnabled) => {
        const exclusions = addonWafRuleExclusions(edhConfig(edhEnabled, amiBuildsEnabled));
        expect(exclusions.filter((e) => e.reapplyRuleName === REBLOCK_RULE)).toEqual([]);
    });
});

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL with the EDH AMI recipe exclusion",
    (wafScope) => {
        const acl = synthAcl(wafScope, shippedPolicy, edhExclusions);
        const baseline = synthAcl(wafScope, shippedPolicy);

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
                        { NotStatement: { Statement: prefixMatch(RECIPE_PREFIX) } },
                    ],
                },
            });
        });

        test.each([
            ["the add-on is off", false, false],
            ["the add-on is on with AMI builds off", true, false],
        ])(
            "is the ACL the policy alone produces when %s",
            (_case, edhEnabled, amiBuildsEnabled) => {
                const exclusions = buildEdhWafRuleExclusions(
                    edhConfig(edhEnabled, amiBuildsEnabled)
                );
                expect(rulesOf(synthAcl(wafScope, shippedPolicy, exclusions))).toEqual(
                    rulesOf(baseline)
                );
            }
        );
    }
);

describe("the regional web ACL stack takes the exclusions", () => {
    test("bin/infra.ts builds the web ACL stacks through buildWafStacks", () => {
        // The stacks buildWafStacks returns are what edhWafVariations.test.ts asserts per deployment
        // variation; a CfWafStack constructed in the app itself would bypass every one of them.
        const appSource = readFileSync(join(__dirname, "..", "..", "..", "bin", "infra.ts"), {
            encoding: "utf8",
        });
        expect(appSource).toContain("buildWafStacks(app, config)");
        expect(appSource).not.toContain("new CfWafStack(");
    });
});
