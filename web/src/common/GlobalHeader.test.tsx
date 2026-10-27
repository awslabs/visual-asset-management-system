/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The deployment banner paints its background and text from the --vams-banner-* theme tokens, and
 * those tokens meet the WCAG AA contrast ratio for normal text (4.5:1).
 */

import React from "react";
import fs from "fs";
import path from "path";
import { render, screen } from "@testing-library/react";
import { GlobalHeader } from "./GlobalHeader";

// sanitize-html's parser ships as ESM, which jest does not transform; sanitizing is not under test.
jest.mock("sanitize-html", () => (html: string) => html);

jest.mock("../services/appCache", () => ({
    appCache: {
        getItem: (key: string) =>
            key === "config" ? { bannerHtmlMessage: "<b>Test deployment</b>" } : undefined,
    },
}));

function rootToken(css: string, name: string): string {
    const rootBlock = css.match(/:root\s*\{([\s\S]*?)\n\}/);
    if (!rootBlock) throw new Error(":root block not found");
    const match = rootBlock[1].match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`));
    if (!match) throw new Error(`${name} not found in :root`);
    return match[1];
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

describe("GlobalHeader deployment banner", () => {
    it.each([
        ["application pages", false],
        ["sign-in pages", true],
    ])("uses the banner theme tokens on %s", (_label, authorizationHeader) => {
        render(<GlobalHeader authorizationHeader={authorizationHeader} />);

        const banner = screen.getByText("Test deployment").closest("div[style]") as HTMLElement;
        expect(banner.style.backgroundColor).toBe("var(--vams-banner-bg)");
        expect(banner.style.color).toBe("var(--vams-banner-text)");
    });

    it("defines banner tokens with at least 4.5:1 contrast", () => {
        const css = fs.readFileSync(path.join(__dirname, "../styles/theme.css"), "utf8");
        const background = rootToken(css, "--vams-banner-bg");
        const text = rootToken(css, "--vams-banner-text");

        expect(contrastRatio(background, text)).toBeGreaterThanOrEqual(4.5);
    });
});
