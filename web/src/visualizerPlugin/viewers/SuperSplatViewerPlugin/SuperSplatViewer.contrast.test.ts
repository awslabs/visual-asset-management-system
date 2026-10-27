/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SuperSplat loading overlay sits on a fixed dark background in both VAMS themes, so its text
 * colour is a fixed light value with at least 4.5:1 contrast, not a theme token that turns dark in
 * light mode.
 */
import fs from "fs";
import path from "path";

const css = fs.readFileSync(path.join(__dirname, "SuperSplatViewer.module.css"), "utf8");

const rule = (selector: string): string => {
    const start = css.indexOf(`${selector} {`);
    if (start < 0) throw new Error(`no rule for ${selector}`);
    return css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
};

const value = (body: string, property: string): string => {
    const match = body.match(new RegExp(`(?:^|[;\\s])${property}\\s*:\\s*([^;]+);`));
    if (!match) throw new Error(`no ${property}`);
    return match[1].trim();
};

const luminance = (hex: string): number => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};

describe("SuperSplat overlay text", () => {
    const overlay = rule(".overlay");
    const color = value(overlay, "color");
    const background = value(overlay, "background");

    it("uses a fixed colour on the fixed background", () => {
        expect(color).toMatch(/^#[0-9a-fA-F]{6}$/);
        expect(background).toMatch(/^#[0-9a-fA-F]{6}$/);
    });

    it("reaches 4.5:1 against the background", () => {
        const [hi, lo] = [luminance(color), luminance(background)].sort((x, y) => y - x);
        expect((hi + 0.05) / (lo + 0.05)).toBeGreaterThanOrEqual(4.5);
    });
});
