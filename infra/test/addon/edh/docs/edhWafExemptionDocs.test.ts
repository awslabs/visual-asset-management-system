/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH Workstations resources page explains the add-on's AWS WAF exemption for AMI recipes, and
 * nothing else ties it to the exclusion the add-on contributes to the regional web ACL: the rules it
 * relaxes, its re-block rule, its path prefix, the rules and groups it declares unrelaxed and the synth
 * warning that names them, and that the exemption exists only while the add-on and its AMI builds are
 * on. Each assertion derives the expected value from `buildEdhWafRuleExclusions()` and reads it back
 * out of the page, so a page that drifts fails here instead of shipping.
 *
 * Durable (root CLAUDE.md Rule 13): the relaxed rules, the re-block rule, the prefix and the unrelaxed
 * set can change again with the next recipe route or policy rule.
 */

import * as Config from "../../../../config/config";
import { buildEdhWafRuleExclusions } from "../../../../lib/nestedStacks/addon/edhWorkstations/edhWafExclusions";
import { page, sectionOf } from "../../../docs/support/docsText";
import { pageDir as PAGE_DIR } from "./placement";

const RESOURCES = `${PAGE_DIR}/resources.md`;
const SECTION = "## AWS WAF exemption for AMI recipes";

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

    test("the resources page scopes the exemption to the regional web ACL and to the add-on and AMI builds switches", () => {
        const text = exemptionSection();
        expect(text).toContain("regional web ACL");
        expect(text).toContain("`app.useWaf`");
        expect(text).toContain("With the add-on or its AMI builds off");
    });
});
