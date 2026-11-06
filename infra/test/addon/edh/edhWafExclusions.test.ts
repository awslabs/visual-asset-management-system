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
 * rules block such a body, so with the add-on's AMI builds on, each of the two the policy blocks
 * counts inside the managed group and one VAMS rule blocks on its label everywhere except at or
 * below the recipe route prefix. The shipped policy counts GenericRFI_BODY on every path already,
 * so with it only EC2MetaDataSSRF_BODY is relaxed and re-blocked.
 *
 * What is pinned here:
 *   * `buildEdhWafRuleExclusions` returns the exclusion only while the add-on and its AMI builds are
 *     on, and `addonWafRuleExclusions` carries it to `buildWafStacks`, which applies it in both web
 *     ACLs;
 *   * the prefix covers every AMI recipe route the add-on stack registers;
 *   * for both ACL scopes and the shipped policy, EC2MetaDataSSRF_BODY counts, the policy's own
 *     overrides are kept (GenericRFI_BODY among them), and the re-block rule, after every other
 *     rule, blocks the EC2MetaDataSSRF_BODY label off the recipe routes only: the prefix itself and
 *     the paths below it, not a sibling such as `.../ami-recipesX` (checked by the statement's shape
 *     and against a model of its evaluation);
 *   * under a policy that blocks GenericRFI_BODY, both rules count and both labels are re-blocked;
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

/** A fork's policy: the shipped one without its GenericRFI_BODY override, so the rule blocks. */
const policyBlockingRfi: WafPolicyConfig = (() => {
    const policy: WafPolicyConfig = JSON.parse(JSON.stringify(shippedPolicy));
    for (const group of policy.managedRuleGroups || []) {
        if (group.managedRuleGroupName !== COMMON_RULE_SET) continue;
        group.ruleActionOverrides = (group.ruleActionOverrides || []).filter(
            (o) => o.name !== RFI_RULE
        );
    }
    return policy;
})();

/** The Common Rule Set overrides a policy declares, as the ACL renders them. */
const commonOverridesOf = (policy: WafPolicyConfig) =>
    (policy.managedRuleGroups || [])
        .filter((group) => group.managedRuleGroupName === COMMON_RULE_SET)
        .flatMap((group) => group.ruleActionOverrides || [])
        .map((override) => ({
            Name: override.name,
            ActionToUse: {
                [override.action.charAt(0).toUpperCase() + override.action.slice(1)]: {},
            },
        }));

/**
 * The Common Rule Set overrides the shipped policy declares. The exact set is pinned by
 * `wafCommonRuleSetOverrides.test.ts`; here it is what the exclusion must keep.
 */
const shippedCommonOverrides = commonOverridesOf(shippedPolicy);

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

const RAW_PATH = [{ Priority: 0, Type: "NONE" }];
const NORMALIZED_PATH = [
    { Priority: 0, Type: "URL_DECODE" },
    { Priority: 1, Type: "NORMALIZE_PATH" },
];

const uriPathMatch = (searchString: string, positionalConstraint: string, transforms: any[]) => ({
    ByteMatchStatement: {
        SearchString: searchString,
        FieldToMatch: { UriPath: {} },
        TextTransformations: transforms,
        PositionalConstraint: positionalConstraint,
    },
});

/** The path, in one form, is the prefix itself or a path below it (a segment boundary). */
const atOrBelow = (prefix: string, transforms: any[]): any => ({
    OrStatement: {
        Statements: [
            uriPathMatch(prefix, "EXACTLY", transforms),
            uriPathMatch(`${prefix}/`, "STARTS_WITH", transforms),
        ],
    },
});

/** The raw path and its decoded, normalized form must both be at or below the prefix. */
const prefixMatch = (prefix: string): any => ({
    AndStatement: {
        Statements: [atOrBelow(prefix, RAW_PATH), atOrBelow(prefix, NORMALIZED_PATH)],
    },
});

/**
 * A model of how AWS WAF evaluates the re-block rule's statements (label match, AND / OR / NOT, and
 * an EXACTLY or STARTS_WITH match on the URI path after NONE, URL_DECODE or NORMALIZE_PATH), the
 * one `wafScopedRuleExclusions.test.ts` documents. Any other statement fails the test.
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

const transformPath = (path: string, transforms: Array<{ Priority: number; Type: string }>) =>
    [...transforms]
        .sort((a, b) => a.Priority - b.Priority)
        .reduce((value, { Type }) => {
            if (Type === "NONE") return value;
            if (Type === "URL_DECODE") return urlDecode(value);
            if (Type === "NORMALIZE_PATH") return normalizePath(value);
            throw new Error(`unmodelled text transformation ${Type}`);
        }, path);

const modelMatches = (statement: any, request: ModelRequest): boolean => {
    if (statement.AndStatement) {
        return statement.AndStatement.Statements.every((s: any) => modelMatches(s, request));
    }
    if (statement.OrStatement) {
        return statement.OrStatement.Statements.some((s: any) => modelMatches(s, request));
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

/** Whether the EDH re-block rule blocks the request in the model. */
const reblocks = (acl: any, request: ModelRequest): boolean =>
    modelMatches(ruleNamed(acl, REBLOCK_RULE).Statement, request);

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
            const stagePath = `/${Config.API_GATEWAY_STAGE_NAME}${routePath}`;
            // At or below the prefix: the prefix itself or a path below it, at a segment boundary.
            expect(stagePath === RECIPE_PREFIX || stagePath.startsWith(`${RECIPE_PREFIX}/`)).toBe(
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
    "%s-scoped web ACL with the EDH AMI recipe exclusion and the shipped policy",
    (wafScope) => {
        const acl = synthAcl(wafScope, shippedPolicy, edhExclusions);
        const baseline = synthAcl(wafScope, shippedPolicy);

        test("counts EC2MetaDataSSRF_BODY in the Common Rule Set, keeping the policy's overrides", () => {
            const overrides = overridesOf(acl);
            // Control: the shipped policy counts GenericRFI_BODY itself, and overrides other rules
            // of its own, so "keeping" them is tested.
            expect(shippedCommonOverrides).toContainEqual({
                Name: RFI_RULE,
                ActionToUse: { Count: {} },
            });
            expect(shippedCommonOverrides.length).toBeGreaterThan(1);
            expect(overrides).toHaveLength(shippedCommonOverrides.length + 1);
            expect(overrides).toEqual(
                expect.arrayContaining([
                    ...shippedCommonOverrides,
                    { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                ])
            );
            // GenericRFI_BODY keeps the policy's one count override.
            expect(overrides.filter((o) => o.Name === RFI_RULE)).toEqual([
                { Name: RFI_RULE, ActionToUse: { Count: {} } },
            ]);
            // The rest of the group stays in block mode.
            expect(commonRuleSetOf(acl).OverrideAction).toEqual({ None: {} });
        });

        test("re-blocks the relaxed rule after every other rule", () => {
            const rule = ruleNamed(acl, REBLOCK_RULE);
            expect(rule).toBeDefined();
            expect(rule.Action).toEqual({ Block: {} });
            rulesOf(acl)
                .filter((r) => r.Name !== REBLOCK_RULE)
                .forEach((other) => expect(rule.Priority).toBeGreaterThan(other.Priority));
        });

        test("blocks the EC2MetaDataSSRF_BODY label unless the raw and normalized paths are at or below the prefix", () => {
            expect(ruleNamed(acl, REBLOCK_RULE).Statement).toEqual({
                AndStatement: {
                    Statements: [
                        labelMatch(SSRF_LABEL),
                        { NotStatement: { Statement: prefixMatch(RECIPE_PREFIX) } },
                    ],
                },
            });
        });

        test.each([
            [RECIPE_PREFIX],
            [`${RECIPE_PREFIX}/`],
            [`${RECIPE_PREFIX}/al2023-desktop`],
            [`${RECIPE_PREFIX}/al2023-desktop/builds`],
        ])("exempts %s", (path) => {
            expect(reblocks(acl, { path, labels: [SSRF_LABEL] })).toBe(false);
        });

        test.each([
            // A sibling path whose last segment begins with the prefix's last segment.
            [`${RECIPE_PREFIX}X`],
            [`${RECIPE_PREFIX}X/al2023-desktop`],
            // Another admin route of the add-on.
            ["/api/addon/edh/admin/images"],
            // Dot segments, plain and encoded, leaving the prefix.
            [`${RECIPE_PREFIX}/../images`],
            [`${RECIPE_PREFIX}/%2e%2e/images`],
        ])("re-blocks %s", (path) => {
            expect(reblocks(acl, { path, labels: [SSRF_LABEL] })).toBe(true);
        });

        test("does not re-block GenericRFI_BODY, which the policy counts on every path", () => {
            expect(reblocks(acl, { path: "/api/assets/item", labels: [RFI_LABEL] })).toBe(false);
            // Control: the same path is re-blocked for the relaxed rule's label.
            expect(reblocks(acl, { path: "/api/assets/item", labels: [SSRF_LABEL] })).toBe(true);
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

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL with the EDH AMI recipe exclusion and a policy that blocks GenericRFI_BODY",
    (wafScope) => {
        const acl = synthAcl(wafScope, policyBlockingRfi, edhExclusions);

        test("counts both body rules in the Common Rule Set, keeping the policy's overrides", () => {
            const policyOverrides = commonOverridesOf(policyBlockingRfi);
            // Control: the policy no longer overrides GenericRFI_BODY.
            expect(policyOverrides.map((o) => o.Name)).not.toContain(RFI_RULE);
            const overrides = overridesOf(acl);
            expect(overrides).toHaveLength(policyOverrides.length + 2);
            expect(overrides).toEqual(
                expect.arrayContaining([
                    ...policyOverrides,
                    { Name: SSRF_RULE, ActionToUse: { Count: {} } },
                    { Name: RFI_RULE, ActionToUse: { Count: {} } },
                ])
            );
        });

        test("blocks either label unless the raw and normalized paths are at or below the prefix", () => {
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
            expect(reblocks(acl, { path: `${RECIPE_PREFIX}X`, labels: [RFI_LABEL] })).toBe(true);
            expect(reblocks(acl, { path: `${RECIPE_PREFIX}/x`, labels: [RFI_LABEL] })).toBe(false);
        });
    }
);

describe("bin/infra.ts", () => {
    test("builds the web ACL stacks through buildWafStacks", () => {
        // The stacks buildWafStacks returns are what edhWafVariations.test.ts asserts per deployment
        // variation; a CfWafStack constructed in the app itself would bypass every one of them.
        const appSource = readFileSync(join(__dirname, "..", "..", "..", "bin", "infra.ts"), {
            encoding: "utf8",
        });
        expect(appSource).toMatch(/buildWafStacks\(app, config\b/);
        expect(appSource).not.toContain("new CfWafStack(");
    });
});
