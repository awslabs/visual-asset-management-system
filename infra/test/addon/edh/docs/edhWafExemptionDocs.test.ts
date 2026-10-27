/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH Workstations resources page explains the add-on's AWS WAF exemption for AMI recipes, and
 * nothing else ties it to the exclusion the add-on contributes to both web ACLs: the rules it
 * relaxes, the ones the shipped policy already counts, its re-block rule, its path prefix and its
 * segment boundary, the rules and groups it declares unrelaxed and the synth warning that names
 * them, and that the exemption exists only while the add-on and its AMI builds are on. Each
 * assertion derives the expected value from `buildEdhWafRuleExclusions()` or the shipped policy and
 * reads it back out of the page, so a page that drifts fails here instead of shipping.
 *
 * Durable (root CLAUDE.md Rule 13): the relaxed rules, the re-block rule, the prefix and the unrelaxed
 * set can change again with the next recipe route or policy rule.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as Config from "../../../../config/config";
import { WafPolicyConfig } from "../../../../lib/constructs/wafv2-basic-construct";
import { buildEdhWafRuleExclusions } from "../../../../lib/nestedStacks/addon/edhWorkstations/edhWafExclusions";
import { page, sectionOf } from "../../../docs/support/docsText";
import { pageDir as PAGE_DIR } from "./placement";

const RESOURCES = `${PAGE_DIR}/resources.md`;
const SECTION = "## AWS WAF exemption for AMI recipes";

/** The policy that ships with the deployment, as `wafRateLimit.test.ts` reads it. */
const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(
        join(__dirname, "..", "..", "..", "..", "config", "policy", "wafPolicyConfig.json"),
        { encoding: "utf8" }
    )
);

/** The add-on's exclusions for an add-on switch and an AMI builds switch. */
function exclusionsFor(enabled: boolean, amiBuilds: boolean) {
    const config = {
        app: { addons: { useEdhWorkstations: { enabled, amiBuilds: { enabled: amiBuilds } } } },
    } as unknown as Config.Config;
    return buildEdhWafRuleExclusions(config);
}

/** The AMI recipe exemption with the add-on and its AMI builds on. */
function edhExclusion() {
    const exclusions = exclusionsFor(true, true);
    expect(exclusions).toHaveLength(1);
    return exclusions[0];
}

/** The page's exemption section. */
const exemptionSection = (): string => sectionOf(page(RESOURCES), SECTION);

describe("EDH Workstations AWS WAF exemption documentation matches the exclusion", () => {
    test("the exclusion exists only with the add-on and its AMI builds on, and the page has the section (control)", () => {
        expect(exclusionsFor(false, true)).toEqual([]);
        expect(exclusionsFor(true, false)).toEqual([]);
        expect(edhExclusion().rules.length).toBeGreaterThanOrEqual(1);
        expect(exemptionSection()).not.toBe("");
    });

    test("the resources page names every rule the exemption relaxes, its group, its re-block rule and its path prefix", () => {
        const exclusion = edhExclusion();
        const text = exemptionSection();
        const names = [
            exclusion.managedRuleGroupName,
            ...exclusion.rules.map((r) => r.name),
            exclusion.reapplyRuleName,
            ...exclusion.uriPathPrefixes,
        ];
        expect(names.filter((n) => !text.includes(`\`${n}\``))).toEqual([]);
    });

    // A rule or group the exemption does not relax still blocks recipe bodies wherever a policy
    // enforces it, so the page that explains the exemption lists each of them.
    test("the resources page names every unrelaxed rule and group, and the warning id", () => {
        const exclusion = edhExclusion();
        const text = exemptionSection();
        const unrelaxed = exclusion.unrelaxedRules || [];
        expect(unrelaxed.length).toBeGreaterThanOrEqual(6);
        const names = unrelaxed.flatMap((u) =>
            u.name ? [u.managedRuleGroupName, u.name] : [u.managedRuleGroupName]
        );
        expect(names.filter((n) => !text.includes(`\`${n}\``))).toEqual([]);
        expect(text).toContain(`\`VAMS:WafPolicy:${exclusion.reapplyRuleName}:unrelaxed:`);
    });

    test("the resources page places the exemption in both web ACLs and scopes it to the add-on and AMI builds switches", () => {
        const text = exemptionSection();
        expect(text).toContain("The exemption is in both web ACLs");
        expect(text).not.toContain("regional web ACL alone");
        expect(text).toContain("CloudFront forwards that path to the stage unchanged");
        expect(text).toContain("about 48 web ACL capacity units (WCU) in each web ACL");
        expect(text).toContain("`app.useWaf`");
        expect(text).toContain("With the add-on or its AMI builds off");
    });

    test("the resources page states the prefix's segment boundary with a sibling path that is not exempt", () => {
        const [prefix] = edhExclusion().uriPathPrefixes;
        expect(exemptionSection()).toContain(`\`${prefix}\` itself or a path below it`);
        expect(exemptionSection()).toContain(`\`${prefix}X\` is not exempt`);
    });

    // A relaxed rule the shipped policy already counts is neither relaxed nor re-blocked by the
    // exemption, so the page says which ones the shipped policy leaves at its own count.
    test("the resources page names each relaxed rule the shipped policy already counts", () => {
        const exclusion = edhExclusion();
        const counted = (shippedPolicy.managedRuleGroups || [])
            .filter((g) => g.managedRuleGroupName === exclusion.managedRuleGroupName)
            .flatMap((g) => g.ruleActionOverrides || [])
            .filter((o) => o.action === "count")
            .map((o) => o.name);
        const countedRelaxed = exclusion.rules
            .map((r) => r.name)
            .filter((n) => counted.includes(n));
        // Control: since the shipped policy counts GenericRFI_BODY, at least one is counted.
        expect(countedRelaxed.length).toBeGreaterThanOrEqual(1);
        expect(
            countedRelaxed.filter(
                (n) => !exemptionSection().includes(`The shipped policy already counts \`${n}\``)
            )
        ).toEqual([]);
    });
});
