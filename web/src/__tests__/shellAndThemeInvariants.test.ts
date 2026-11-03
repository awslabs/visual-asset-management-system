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

/**
 * index.scss declared #headerWrapper twice: z-index 1002, then a later rule with z-index 1000, which
 * applied. A menu opened from the sticky header paints inside the header's stacking context, so at
 * the mobile toolbar's z-index (1000, also sticky, later in the document) the toolbar covered the
 * Settings menu's first item (Light Theme) at mobile-layout widths. The header's effective z-index
 * is read from index.scss and the mobile layers from the installed Cloudscape AppLayout CSS, so a
 * Cloudscape upgrade that raises those layers fails here too.
 */

const indexScss = fs.readFileSync(path.join(__dirname, "../styles/index.scss"), "utf8");
const componentsDir = path.dirname(require.resolve("@cloudscape-design/components/package.json"));
const appLayoutCss = fs.readFileSync(
    path.join(componentsDir, "app-layout/visual-refresh/styles.scoped.css"),
    "utf8"
);

const zIndexes = (body: string) =>
    Array.from(body.matchAll(/z-index:\s*(\d+)/g)).map(([, n]) => Number(n));

/** Same selector, same specificity: the last declared z-index applies. */
function headerZIndex(): number {
    const declared = Array.from(
        indexScss.matchAll(/(?:^|\n)#headerWrapper\s*\{([^}]*)\}/g)
    ).flatMap(([, body]) => zIndexes(body));
    if (!declared.length) throw new Error("no #headerWrapper z-index in index.scss");
    return declared[declared.length - 1];
}

/** z-indexes of AppLayout rules whose selector names one of `layers`. */
function layerZIndexes(layers: RegExp): number[] {
    return Array.from(appLayoutCss.matchAll(/([^{}]+)\{([^{}]*)\}/g))
        .filter(([, selector]) => layers.test(selector))
        .flatMap(([, , body]) => zIndexes(body));
}

const MOBILE_LAYERS =
    /awsui_(mobile-toolbar|navigation-container|tools-container|drawers-container)_/;

describe("header stacking", () => {
    it("finds the mobile toolbar in the installed Cloudscape CSS", () => {
        // Control: an empty list would make the next check pass trivially.
        expect(layerZIndexes(/awsui_mobile-toolbar_/).length).toBeGreaterThan(0);
    });

    it("keeps the header above the AppLayout mobile toolbar and drawers", () => {
        expect(headerZIndex()).toBeGreaterThan(Math.max(...layerZIndexes(MOBILE_LAYERS)));
    });
});
