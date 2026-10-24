/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * theme.css aliases some --vams-* tokens to Cloudscape's hashed runtime custom properties
 * (var(--color-background-input-default-ifz5bb, #ffffff)). A renamed or removed Cloudscape token,
 * or a changed value, would silently fall back to the literal, so every alias is checked against
 * the installed @cloudscape-design/design-tokens: the hashed name must exist, and the literal in
 * the light (:root) and dark (:root.awsui-dark-mode) blocks must equal the token's light or dark
 * value.
 */

import fs from "fs";
import path from "path";

const themeCss = fs.readFileSync(path.join(__dirname, "../styles/theme.css"), "utf8");
const tokensDir = path.dirname(require.resolve("@cloudscape-design/design-tokens/package.json"));
const tokensJs = fs.readFileSync(path.join(tokensDir, "index.js"), "utf8");
const tokens: Record<string, { $value: { light: string; dark: string } }> = JSON.parse(
    fs.readFileSync(path.join(tokensDir, "index-visual-refresh.json"), "utf8")
).tokens;

function block(selector: RegExp): string {
    const match = themeCss.match(selector);
    if (!match) throw new Error(`block ${selector} not found in theme.css`);
    return match[1];
}

/** Every `var(--<token>-<hash>, <literal>)` alias in a block. */
function aliases(css: string) {
    return Array.from(
        css.matchAll(/var\(--([a-z0-9-]+)-([a-z0-9]{6}),\s*(#[0-9a-fA-F]{3,8})\)/g)
    ).map(([, name, hash, literal]) => ({ name, hash, literal }));
}

const MODES: [string, "light" | "dark", string][] = [
    ["light", "light", block(/(?:^|\n):root\s*\{([\s\S]*?)\n\}/)],
    ["dark", "dark", block(/:root\.awsui-dark-mode\s*\{([\s\S]*?)\n\}/)],
];

describe("theme.css Cloudscape token aliases", () => {
    it.each(MODES)("finds aliases in the %s block", (_label, _mode, css) => {
        // Control: an empty list would make the checks below pass trivially.
        expect(aliases(css).length).toBeGreaterThanOrEqual(6);
    });

    it.each(MODES)("aliases only hashed names the installed tokens define (%s)", (_l, _m, css) => {
        const missing = aliases(css)
            .map(({ name, hash }) => `--${name}-${hash}`)
            .filter((property) => !tokensJs.includes(`var(${property},`));
        expect(missing).toEqual([]);
    });

    it.each(MODES)("keeps each %s fallback equal to the token value", (_label, mode, css) => {
        const mismatched = aliases(css)
            .filter(({ name, literal }) => {
                const value = tokens[name]?.$value?.[mode];
                return typeof value !== "string" || value.toLowerCase() !== literal.toLowerCase();
            })
            .map(({ name, literal }) => `${name}: ${literal} vs ${tokens[name]?.$value?.[mode]}`);
        expect(mismatched).toEqual([]);
    });
});
