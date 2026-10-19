/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards issue #400 part A: the shipped WAF policy's per-rule `count` overrides on the AWS
 * Common Rule Set are pinned to an exact set, so an edit that silently widens the list (relaxing
 * protection) or narrows it (re-blocking ordinary VAMS file requests) fails here instead of at a
 * user's first `.log` file.
 *
 * Why these eight, and only these:
 *
 *   - SizeRestrictions_BODY / SizeRestrictions_QUERYSTRING — the two pre-existing overrides:
 *     multi-part upload bodies up to the API Gateway REST 10 MB cap, and the SuperSplat viewer's
 *     presigned-URL `?load=` query parameter over 2048 bytes.
 *   - SizeRestrictions_URIPATH — the stream routes (`.../download/stream/{proxy+}`,
 *     `.../auxiliaryPreviewAssets/stream/{proxy+}`) carry the URL-encoded asset file key in the
 *     path; a deep folder tree exceeds the rule's 1024-byte limit once percent-encoded.
 *   - RestrictedExtensions_URIPATH / RestrictedExtensions_QUERYARGUMENTS — file names ending
 *     `.log`, `.ini`, `.cfg`, `.conf`, `.config`, ... are ordinary sidecar files in asset sets. The
 *     backend upload blocklist (`UNALLOWED_FILE_EXTENSION_LIST`) admits them, so blocking the
 *     read path at the edge leaves a file that can be uploaded but never viewed or downloaded.
 *   - GenericLFI_URIPATH / GenericLFI_QUERYARGUMENTS / GenericLFI_BODY — fire on any encoded
 *     `../`. Every backend path validator rejects `..` segments (`common/validators.py`) and keys
 *     resolve against S3 object keys, not a local filesystem, so the rule blocks legitimate
 *     content without adding protection.
 *
 * Every other Common Rule Set rule, and every rule in the Known Bad Inputs and Amazon IP
 * Reputation List groups, keeps its block action — asserted below as "no overrides at all" on
 * those two groups, and as `overrideAction: none` on all three.
 *
 * The test reads the shipped file and ALSO synthesizes it through the construct, so it covers
 * both the configuration and the rendering of that configuration into the Web ACL.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
} from "../../lib/constructs/wafv2-basic-construct";
import { newTestApp } from "../support/testApp";

const COMMON_RULE_SET = "AWSManagedRulesCommonRuleSet";
const KNOWN_BAD_INPUTS = "AWSManagedRulesKnownBadInputsRuleSet";
const IP_REPUTATION = "AWSManagedRulesAmazonIpReputationList";

/** The complete, order-independent set of Common Rule Set rules the shipped policy counts. */
const EXPECTED_COMMON_RULE_SET_COUNT_OVERRIDES = [
    "SizeRestrictions_BODY",
    "SizeRestrictions_QUERYSTRING",
    "SizeRestrictions_URIPATH",
    "RestrictedExtensions_URIPATH",
    "RestrictedExtensions_QUERYARGUMENTS",
    "GenericLFI_URIPATH",
    "GenericLFI_QUERYARGUMENTS",
    "GenericLFI_BODY",
].sort();

const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "config", "policy", "wafPolicyConfig.json"), {
        encoding: "utf8",
    })
);

const shippedGroups = shippedPolicy.managedRuleGroups || [];

const groupByManagedName = (managedRuleGroupName: string) =>
    shippedGroups.find((g) => g.managedRuleGroupName === managedRuleGroupName);

const buildTemplate = (scope: WAFScope): Template => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "WafTestStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope: scope, wafPolicy: shippedPolicy });
    return Template.fromStack(stack);
};

const getWebAcl = (template: Template): any =>
    Object.values(template.findResources("AWS::WAFv2::WebACL"))[0];

const getManagedRule = (template: Template, managedRuleGroupName: string): any =>
    getWebAcl(template).Properties.Rules.find(
        (r: any) => r.Statement?.ManagedRuleGroupStatement?.Name === managedRuleGroupName
    );

describe("shipped WAF policy: Common Rule Set per-rule overrides", () => {
    /**
     * Positive control: all three managed groups must be present and in block mode. Without
     * this, deleting a group would satisfy every "carries no overrides" assertion vacuously,
     * and flipping `block` to false would relax the whole group while the override set still
     * read as "exactly eight".
     */
    test("the three shipped managed rule groups are present and in block mode", () => {
        expect(shippedGroups.map((g) => g.managedRuleGroupName).sort()).toEqual(
            [COMMON_RULE_SET, KNOWN_BAD_INPUTS, IP_REPUTATION].sort()
        );
        shippedGroups.forEach((group) => {
            expect(group.vendorName).toBe("AWS");
            expect(group.block).toBe(true);
        });
    });

    test("the Common Rule Set overrides are exactly the eight expected rules", () => {
        const overrides = groupByManagedName(COMMON_RULE_SET)?.ruleActionOverrides || [];
        const names = overrides.map((o) => o.name);
        // No duplicates: AWS WAF rejects a managed rule group statement that names the same
        // rule twice, and a duplicate would also let the sorted-equality check below pass with
        // one real rule missing.
        expect(new Set(names).size).toBe(names.length);
        expect([...names].sort()).toEqual(EXPECTED_COMMON_RULE_SET_COUNT_OVERRIDES);
    });

    test("every Common Rule Set override is 'count' — never 'allow'", () => {
        // `allow` would short-circuit the rest of the Web ACL for a matching request, including
        // the rate-based rule; `count` keeps the request flowing through every later rule while
        // still recording the match in the group's CloudWatch metrics and the WAF log.
        const overrides = groupByManagedName(COMMON_RULE_SET)?.ruleActionOverrides || [];
        expect(overrides.length).toBe(EXPECTED_COMMON_RULE_SET_COUNT_OVERRIDES.length);
        overrides.forEach((o) => expect(o.action).toBe("count"));
    });

    test.each([KNOWN_BAD_INPUTS, IP_REPUTATION])(
        "%s carries no per-rule overrides",
        (managedRuleGroupName) => {
            const group = groupByManagedName(managedRuleGroupName);
            expect(group).toBeDefined();
            expect(group?.ruleActionOverrides ?? []).toEqual([]);
        }
    );
});

describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
    "%s-scoped web ACL renders the shipped overrides",
    (wafScope) => {
        const template = buildTemplate(wafScope);

        test("is emitted at the scope under test", () => {
            expect(getWebAcl(template).Properties.Scope).toBe(wafScope.toString());
        });

        test("the Common Rule Set statement carries exactly the eight count overrides", () => {
            const statement = getManagedRule(template, COMMON_RULE_SET).Statement
                .ManagedRuleGroupStatement;
            const rendered: Array<{ Name: string; ActionToUse: any }> =
                statement.RuleActionOverrides;
            expect(rendered.map((o) => o.Name).sort()).toEqual(
                EXPECTED_COMMON_RULE_SET_COUNT_OVERRIDES
            );
            rendered.forEach((o) => {
                expect(o.ActionToUse).toEqual({ Count: {} });
            });
        });

        test("all three managed groups keep the group's own block actions", () => {
            [COMMON_RULE_SET, KNOWN_BAD_INPUTS, IP_REPUTATION].forEach((name) => {
                const rule = getManagedRule(template, name);
                expect(rule).toBeDefined();
                // `None` (not `Count`) is what lets the un-overridden rules block.
                expect(rule.OverrideAction).toEqual({ None: {} });
            });
        });

        test.each([KNOWN_BAD_INPUTS, IP_REPUTATION])(
            "%s is rendered without RuleActionOverrides",
            (name) => {
                const statement = getManagedRule(template, name).Statement
                    .ManagedRuleGroupStatement;
                expect(statement.RuleActionOverrides).toBeUndefined();
            }
        );
    }
);
