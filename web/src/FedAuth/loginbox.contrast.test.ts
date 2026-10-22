/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Dark-mode login page colours meet the WCAG AA contrast ratio for normal text (4.5:1).
 * The dark theme values are read from theme.css, and the login overrides from
 * loginbox.module.css, so a change to either file is checked.
 */

import fs from "fs";
import path from "path";

const themeCss = fs.readFileSync(path.join(__dirname, "../styles/theme.css"), "utf8");
const loginCss = fs.readFileSync(path.join(__dirname, "loginbox.module.css"), "utf8");

/** The literal value of a token in the dark-mode block of theme.css (fallback of a var() alias). */
function darkToken(name: string): string {
    const block = themeCss.match(/:root\.awsui-dark-mode\s*\{([\s\S]*?)\n\}/);
    if (!block) throw new Error("dark-mode block not found in theme.css");
    const match = block[1].match(new RegExp(`${name}:\\s*(?:var\\([^,]+,\\s*)?(#[0-9a-fA-F]{6})`));
    if (!match) throw new Error(`${name} not found in the dark-mode block`);
    return match[1];
}

/** Resolve a declaration value that is either a hex literal or a var(--vams-*) reference. */
function resolve(value: string): string {
    const ref = value.match(/var\((--vams-[\w-]+)\)/);
    return ref ? darkToken(ref[1]) : value;
}

function relativeLuminance(hex: string): number {
    const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const [r, g, b] = channels.map((c) =>
        c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    );
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(a: string, b: string): number {
    const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

describe("dark-mode login contrast", () => {
    const buttonBackground = darkToken("--vams-color-info");

    it("primary button text variable contrasts with the button background", () => {
        const match = loginCss.match(/--amplify-components-button-primary-color:\s*([^;]+);/);
        expect(match).not.toBeNull();
        expect(contrastRatio(resolve(match![1].trim()), buttonBackground)).toBeGreaterThanOrEqual(
            4.5
        );
    });

    it("every rule painting the button background sets contrasting text", () => {
        const rules = Array.from(loginCss.matchAll(/\{([^{}]*)\}/g)).map((m) => m[1]);
        const buttonRules = rules.filter((body) =>
            /(?:^|[\s;])background-color:\s*var\(--vams-color-info\)/.test(body)
        );
        expect(buttonRules.length).toBeGreaterThanOrEqual(2);
        for (const body of buttonRules) {
            const color = body.match(/(?:^|[\s;])color:\s*([^;!]+)/);
            expect(color).not.toBeNull();
            expect(
                contrastRatio(resolve(color![1].trim()), buttonBackground)
            ).toBeGreaterThanOrEqual(4.5);
        }
    });
});
