/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from "fs";
import path from "path";
import { VIEWER_LAYER } from "./viewerLayers";

// The viewers with a side panel, and the shared loading cover, whose overlays take their z-index
// from VIEWER_LAYER. Each of those viewers' *ViewerComponent.stacking.test.tsx checks the rendered
// order.
const SCALED_FILES = [
    "components/LoadingSpinner.tsx",
    "viewers/ThreeJSViewerPlugin/ThreeJSViewerComponent.tsx",
    "viewers/ThreeJSViewerPlugin/ThreeJSPanel.tsx",
    "viewers/NeedleUSDViewerPlugin/NeedleUSDViewerComponent.tsx",
    "viewers/NeedleUSDViewerPlugin/NeedleUSDPanel.tsx",
    "viewers/BabylonJSGaussianSplatViewerPlugin/BabylonJSGaussianSplatViewerComponent.tsx",
    "viewers/BabylonJSGaussianSplatViewerPlugin/BabylonJSPanel.tsx",
    "viewers/PlayCanvasGaussianSplatViewerPlugin/PlayCanvasGaussianSplatViewerComponent.tsx",
    "viewers/PlayCanvasGaussianSplatViewerPlugin/PlayCanvasPanel.tsx",
    "viewers/ThatOpenWebIfcViewerPlugin/ThatOpenWebIfcViewerComponent.tsx",
    "viewers/ThatOpenWebIfcViewerPlugin/ThatOpenWebIfcPanel.tsx",
    "viewers/VeerumViewerPlugin/VeerumViewerComponent.tsx",
    "viewers/VeerumViewerPlugin/VeerumPanel.tsx",
];

const pluginRoot = path.join(__dirname, "..");

describe("VIEWER_LAYER", () => {
    it("stacks the info box, the panel, notices and the cover in that order, lowest first", () => {
        expect(VIEWER_LAYER.info).toBeLessThan(VIEWER_LAYER.panel);
        expect(VIEWER_LAYER.panel).toBeLessThan(VIEWER_LAYER.notice);
        expect(VIEWER_LAYER.notice).toBeLessThan(VIEWER_LAYER.cover);
    });

    it("keeps the info box at the value the viewers' overlays had, and room between the layers", () => {
        // The viewers drew their overlays at 1000 before the scale; the info box keeps that place
        // over the canvas and over anything a viewer library positions below it.
        expect(VIEWER_LAYER.info).toBe(1000);
        const values = Object.values(VIEWER_LAYER);
        values.slice(1).forEach((value, index) => {
            expect(value - values[index]).toBeGreaterThan(1);
        });
    });

    it.each(SCALED_FILES)("is the only source of the z-index values in %s", (file) => {
        const source = fs.readFileSync(path.join(pluginRoot, file), "utf8");
        const literals = Array.from(source.matchAll(/zIndex:\s*["']?\d+/g)).map(([m]) => m);
        expect(literals).toEqual([]);
        expect(source).toMatch(/zIndex:\s*VIEWER_LAYER\.(info|panel|notice|cover)\b/);
    });
});
