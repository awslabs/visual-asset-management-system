/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// The viewer list and manifest paths the registry registers: core, then add-on, then the fork-owned
// custom files. The custom files ship empty and are filled in by a fork, so the custom behaviour is
// asserted on synthetic entries rather than on the shipped (empty) ones.

import viewerConfig from "../config/viewerConfig.json";
import addonViewerConfig from "../config/addonViewerConfig.json";
import { ViewerPluginConfig } from "./types";
import { VIEWER_COMPONENTS } from "../viewers/manifest";
import {
    ALL_VIEWER_COMPONENTS,
    ALL_VIEWER_CONFIGS,
    mergeManifests,
    mergeViewerConfigs,
} from "./viewerSources";

const coreViewers = (viewerConfig as any).viewers as ViewerPluginConfig[];
const addonViewers = (addonViewerConfig as any).viewers as ViewerPluginConfig[];

const viewer = (id: string): ViewerPluginConfig =>
    ({
        id,
        name: id,
        description: id,
        componentPath: `./viewers/custom/${id}/Component`,
        supportedExtensions: [".widget"],
        supportsMultiFile: false,
        canFullscreen: false,
        priority: 1,
        dependencies: [],
        loadStrategy: "lazy",
        category: "3d",
        enabled: true,
    } as any);

describe("mergeViewerConfigs", () => {
    let consoleError: jest.SpyInstance;
    beforeEach(() => {
        consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
    });
    afterEach(() => consoleError.mockRestore());

    it("registers a custom viewer after the shipped ones", () => {
        const merged = mergeViewerConfigs(coreViewers, [viewer("custom-widget-viewer")]);
        expect(merged.map((v) => v.id)).toEqual([
            ...coreViewers.map((v) => v.id),
            "custom-widget-viewer",
        ]);
        expect(consoleError).not.toHaveBeenCalled();
    });

    it("skips a custom viewer whose id repeats a core viewer, leaving the core entry in place", () => {
        const core = coreViewers[0];
        const merged = mergeViewerConfigs(coreViewers, [{ ...viewer(core.id), name: "Shadow" }]);
        expect(merged).toHaveLength(coreViewers.length);
        expect(merged.find((v) => v.id === core.id)).toBe(core);
        expect(consoleError).toHaveBeenCalledWith(expect.stringContaining(core.id));
    });

    it("skips a custom viewer whose id repeats an add-on viewer", () => {
        const shipped = [...coreViewers, ...addonViewers];
        const addonId = addonViewers[0].id;
        const merged = mergeViewerConfigs(shipped, [viewer(addonId)]);
        expect(merged.filter((v) => v.id === addonId)).toEqual([addonViewers[0]]);
    });

    it("keeps the first of two custom viewers that share an id", () => {
        const first = viewer("custom-widget-viewer");
        const merged = mergeViewerConfigs([], [first, { ...first, name: "Second" }]);
        expect(merged).toEqual([first]);
    });
});

describe("mergeManifests", () => {
    it("adds custom paths and never replaces a shipped one", () => {
        const [coreKey, corePath] = Object.entries(VIEWER_COMPONENTS)[0];
        const merged = mergeManifests(
            { [coreKey]: corePath },
            { [coreKey]: "custom/Shadow", "./viewers/custom/W/WComponent": "custom/W/WComponent" }
        );
        expect(merged[coreKey]).toBe(corePath);
        expect(merged["./viewers/custom/W/WComponent"]).toBe("custom/W/WComponent");
    });
});

describe("the shipped composition", () => {
    it("registers every core viewer, then every add-on viewer, in file order", () => {
        const ids = ALL_VIEWER_CONFIGS.map((v) => v.id);
        const shippedIds = [...coreViewers, ...addonViewers].map((v) => v.id);
        expect(addonViewers.length).toBeGreaterThan(0);
        expect(ids.slice(0, shippedIds.length)).toEqual(shippedIds);
    });

    it("resolves every registered viewer's component through the merged manifest", () => {
        const missing = ALL_VIEWER_CONFIGS.map((v) => v.componentPath).filter(
            (componentPath) => !(componentPath in ALL_VIEWER_COMPONENTS)
        );
        expect(missing).toEqual([]);
    });
});
