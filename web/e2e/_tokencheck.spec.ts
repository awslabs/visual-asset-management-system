/* Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: Apache-2.0 */
import { test } from "@playwright/test";
test("find cloudscape bg token names", async ({ page }) => {
    await page.goto("/#/assets/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(4000);
    const res = await page.evaluate(() => {
        document.documentElement.classList.add("awsui-dark-mode");
        document.body.classList.add("awsui-dark-mode");
        // Enumerate all CSS custom properties whose name mentions background-layout/container.
        const found: Record<string, string> = {};
        for (const sheet of Array.from(document.styleSheets)) {
            let rules: CSSRuleList;
            try {
                rules = sheet.cssRules;
            } catch {
                continue;
            }
            for (const rule of Array.from(rules) as any[]) {
                if (!rule.style) continue;
                for (const prop of Array.from(rule.style) as string[]) {
                    if (
                        prop.startsWith("--color-background-layout-main") ||
                        prop.startsWith("--color-background-container-content")
                    ) {
                        // resolve computed value on documentElement
                        const v = getComputedStyle(document.documentElement)
                            .getPropertyValue(prop)
                            .trim();
                        if (v) found[prop] = v;
                    }
                }
            }
        }
        return found;
    });
    console.log("TOKENS " + JSON.stringify(res));
});
