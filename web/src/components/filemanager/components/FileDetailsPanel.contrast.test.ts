/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The "not included in this version" badge reaches WCAG AA text contrast (4.5:1) in both themes.
 * The colours are read from the badge rule itself and its var() references are resolved against
 * the light and dark blocks of theme.css, so a literal or a token change is caught either way.
 */
import fs from "fs";
import path from "path";

const read = (rel: string) => fs.readFileSync(path.join(__dirname, rel), "utf8");
const theme = read("../../../styles/theme.css");
const panelCss = read("FileDetailsPanel.css");

/** The declarations of the first block whose selector list starts with `selector`. */
const block = (css: string, selector: string): string => {
    const start = css.indexOf(selector + " {");
    if (start < 0) throw new Error(`no rule for ${selector}`);
    return css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
};

const declaration = (body: string, property: string): string => {
    const match = body.match(new RegExp(`(?:^|[;\\s])${property}\\s*:\\s*([^;]+);`));
    if (!match) throw new Error(`no ${property}`);
    return match[1].trim();
};

const lightTokens = block(theme, ":root");
const darkTokens = block(theme, ".awsui-dark-mode,\n:root.awsui-dark-mode");

/** Resolves a colour value to #rrggbb against one theme block, following var() fallbacks. */
const resolve = (value: string, tokens: string): string => {
    const v = value.trim();
    const ref = v.match(/^var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)$/);
    if (!ref) return v;
    const name = ref[1];
    if (name.startsWith("--vams-")) return resolve(declaration(tokens, name), tokens);
    // A Cloudscape runtime variable: its build-time fallback is the literal after the comma.
    return resolve(ref[2], tokens);
};

const luminance = (hex: string): number => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};

const contrast = (a: string, b: string): number => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
};

describe("not-included badge contrast", () => {
    const rule = block(panelCss, ".not-included-label");
    const color = declaration(rule, "color");
    const background = declaration(rule, "background-color");

    it.each([
        ["light", lightTokens],
        ["dark", darkTokens],
    ])("reaches 4.5:1 in %s mode", (_mode, tokens) => {
        const ratio = contrast(resolve(color, tokens), resolve(background, tokens));
        expect(ratio).toBeGreaterThanOrEqual(4.5);
    });
});
