/* Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: Apache-2.0 */
import { test } from "@playwright/test";
test("compare dark backgrounds", async ({ page }) => {
    await page.goto("/#/assets/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(4000);
    // Force dark mode like the app does.
    const info = await page.evaluate(() => {
        document.body.classList.add("awsui-dark-mode");
        (document.documentElement as any).classList.add("awsui-dark-mode");
        const layout = document.querySelector(
            "[class*='awsui_content'], [class*='awsui_layout'], main"
        );
        const cs = layout ? getComputedStyle(layout as Element).backgroundColor : "none";
        // Cloudscape design token values
        const probe = document.createElement("div");
        probe.style.background = "var(--color-background-layout-main-2xUE39, unset)";
        document.body.appendChild(probe);
        const tokenVal = getComputedStyle(probe).backgroundColor;
        // read the app-shell root bg
        const rootBg = getComputedStyle(
            document.getElementById("root") || document.body
        ).backgroundColor;
        return { layoutBg: cs, rootBg, tokenVal };
    });
    console.log("BG_INFO " + JSON.stringify(info));
});
