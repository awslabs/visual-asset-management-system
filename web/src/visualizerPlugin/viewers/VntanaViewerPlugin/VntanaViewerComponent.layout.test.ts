/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The VNTANA viewer's bottom-right control rows must sit above the visualizer's wide and fullscreen
 * buttons (#1206). jsdom does no layout, so this reads the two stylesheets and compares the numbers:
 * the footer's top edge from styles/index.scss, and the rows' lifted `bottom` from the plugin's CSS.
 */
import fs from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "../../..");
const hostCss = fs.readFileSync(path.join(SRC, "styles", "index.scss"), "utf8");
const pluginCss = fs.readFileSync(path.join(__dirname, "VntanaViewerComponent.css"), "utf8");

/** The declarations of the first rule whose selector is exactly `selector`. */
function rule(css: string, selector: string): Record<string, string> {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css);
    if (!match) throw new Error(`no rule for ${selector}`);
    const body = match[1].replace(/\/\*[\s\S]*?\*\//g, "");
    const out: Record<string, string> = {};
    for (const decl of body.split(";")) {
        const [prop, ...value] = decl.split(":");
        if (prop.trim() && value.length) out[prop.trim()] = value.join(":").trim();
    }
    return out;
}

const px = (value: string | undefined): number => {
    const m = /^(-?\d+(?:\.\d+)?)px$/.exec(value ?? "");
    if (!m) throw new Error(`not a px value: ${value}`);
    return Number(m[1]);
};

/** VNTANA's control rows are 40px high (one button). */
const ROW_HEIGHT = 40;

describe("VNTANA controls clear the visualizer footer (#1206)", () => {
    const footer = rule(hostCss, ".visualizer-footer");
    const [padTop, padBottom] = (() => {
        const parts = (footer.padding ?? "").split(/\s+/).map(px);
        return [parts[0], parts.length > 2 ? parts[2] : parts[0]];
    })();
    const footerTop = px(footer.bottom) + padBottom + px(footer.height) + padTop;
    const general = px(rule(pluginCss, ".vntana-viewer-root .general-buttons").bottom);
    const zoom = px(rule(pluginCss, ".vntana-viewer-root .zoom-buttons").bottom);

    it("[control] reads the footer geometry", () => {
        expect(footer.position).toBe("absolute");
        expect(footerTop).toBe(59);
    });

    it("lifts the exploded-view and center row above the footer's top edge", () => {
        expect(general).toBeGreaterThan(footerTop);
    });

    it("keeps the zoom row above the row below it", () => {
        expect(zoom).toBeGreaterThanOrEqual(general + ROW_HEIGHT);
    });

    it("is imported by the component", () => {
        const component = fs.readFileSync(
            path.join(__dirname, "VntanaViewerComponent.tsx"),
            "utf8"
        );
        expect(component).toContain('import "./VntanaViewerComponent.css";');
    });
});
