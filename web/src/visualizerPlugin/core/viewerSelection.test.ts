/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Text Diff Viewer shipped with `supportsMultiFile: true`, so the VISUALIZE path offered it for
 * any two text files ("Visualize Selected Files" / search "View Selected (2)") even though it only
 * renders `compareFiles`, which visualize never passes — the user got an immediate "needs exactly two
 * files" error. A compare-only viewer declares `compareMode.compareOnly` and is never a visualize
 * option, while compare mode keeps offering it exactly as before.
 */

import {
    admitsCompareSelection,
    admitsVisualizeSelection,
    isCompareOnlyViewer,
} from "./viewerSelection";
import { CompareContext } from "./compareShape";
import { CompareModeConfig, ViewerPluginConfig } from "./types";

const viewer = (overrides: Partial<ViewerPluginConfig> = {}): ViewerPluginConfig => ({
    id: "test-viewer",
    name: "Test Viewer",
    description: "",
    componentPath: "./viewers/Test/TestComponent",
    supportedExtensions: [".txt", ".md"],
    supportsMultiFile: false,
    canFullscreen: true,
    priority: 1,
    dependencies: [],
    loadStrategy: "lazy",
    category: "document",
    ...overrides,
});

const compare = (overrides: Partial<CompareModeConfig> = {}): CompareModeConfig => ({
    enabled: true,
    minFiles: 2,
    maxFiles: 2,
    allowSameFileDifferentVersions: true,
    allowDifferentFiles: true,
    allowCrossAsset: true,
    ...overrides,
});

/** Mirrors the shipped text-diff-viewer: compare-only, two files. */
const compareOnlyViewer = viewer({
    id: "text-diff-viewer",
    compareMode: compare({ compareOnly: true }),
});

/** A viewer that visualizes single files AND can compare two — compare is orthogonal here. */
const visualizeAndCompareViewer = viewer({
    id: "dual-viewer",
    compareMode: compare(),
});

const twoDistinctFiles: CompareContext = { fileCount: 2, shape: "different-files" };
const twoVersions: CompareContext = { fileCount: 2, shape: "same-file-versions" };

describe("isCompareOnlyViewer", () => {
    it("is true only when compareMode.compareOnly is set", () => {
        expect(isCompareOnlyViewer(compareOnlyViewer)).toBe(true);
        expect(isCompareOnlyViewer(visualizeAndCompareViewer)).toBe(false);
        expect(isCompareOnlyViewer(viewer())).toBe(false);
    });
});

describe("admitsVisualizeSelection", () => {
    it("never offers a compare-only viewer for two matching files", () => {
        expect(admitsVisualizeSelection(compareOnlyViewer, [".txt"], true)).toBe(false);
        expect(admitsVisualizeSelection(compareOnlyViewer, [".txt", ".md"], true)).toBe(false);
    });

    it("never offers a compare-only viewer for a single matching file either", () => {
        // Same defect, one file: the viewer would render "needs exactly two files".
        expect(admitsVisualizeSelection(compareOnlyViewer, [".txt"], false)).toBe(false);
    });

    it("ignores supportsMultiFile on a compare-only viewer", () => {
        // The regression shipped with supportsMultiFile: true; the flag must not re-admit it.
        const misdeclared = viewer({
            supportsMultiFile: true,
            compareMode: compare({ compareOnly: true }),
        });
        expect(admitsVisualizeSelection(misdeclared, [".txt"], true)).toBe(false);
        expect(admitsVisualizeSelection(misdeclared, [".txt"], false)).toBe(false);
    });

    it("keeps compare capability orthogonal when compareOnly is not set", () => {
        expect(admitsVisualizeSelection(visualizeAndCompareViewer, [".txt"], false)).toBe(true);
        // Multi-file visualize still needs supportsMultiFile, as for any viewer.
        expect(admitsVisualizeSelection(visualizeAndCompareViewer, [".txt"], true)).toBe(false);
        expect(
            admitsVisualizeSelection(
                viewer({ supportsMultiFile: true, compareMode: compare() }),
                [".txt"],
                true
            )
        ).toBe(true);
    });

    it("applies the pre-existing multi-file and extension gates", () => {
        const single = viewer();
        expect(admitsVisualizeSelection(single, [".txt"], false)).toBe(true);
        expect(admitsVisualizeSelection(single, [".txt"], true)).toBe(false);
        expect(admitsVisualizeSelection(viewer({ supportsMultiFile: true }), [".txt"], true)).toBe(
            true
        );
        expect(admitsVisualizeSelection(single, [".ply"], false)).toBe(false);
        expect(
            admitsVisualizeSelection(viewer({ supportsMultiFile: true }), [".txt", ".ply"], true)
        ).toBe(false);
    });
});

describe("admitsCompareSelection", () => {
    it("still offers a compare-only viewer for two files in compare mode", () => {
        expect(admitsCompareSelection(compareOnlyViewer, [".txt"], twoDistinctFiles)).toBe(true);
        expect(admitsCompareSelection(compareOnlyViewer, [".txt"], twoVersions)).toBe(true);
        expect(
            admitsCompareSelection(compareOnlyViewer, [".txt"], {
                ...twoDistinctFiles,
                crossAsset: true,
            })
        ).toBe(true);
    });

    it("offers a compare-capable visualize viewer too — compareOnly is not required", () => {
        expect(admitsCompareSelection(visualizeAndCompareViewer, [".txt"], twoDistinctFiles)).toBe(
            true
        );
    });

    it("never offers a viewer without an enabled compareMode block", () => {
        expect(
            admitsCompareSelection(viewer({ supportsMultiFile: true }), [".txt"], twoDistinctFiles)
        ).toBe(false);
        expect(
            admitsCompareSelection(
                viewer({ compareMode: compare({ enabled: false }) }),
                [".txt"],
                twoDistinctFiles
            )
        ).toBe(false);
    });

    it("enforces the file-count window and extension support", () => {
        expect(
            admitsCompareSelection(compareOnlyViewer, [".txt"], {
                fileCount: 3,
                shape: "different-files",
            })
        ).toBe(false);
        expect(
            admitsCompareSelection(compareOnlyViewer, [".txt"], {
                fileCount: 1,
                shape: "same-file-versions",
            })
        ).toBe(false);
        expect(admitsCompareSelection(compareOnlyViewer, [".txt", ".ply"], twoDistinctFiles)).toBe(
            false
        );
        // Without a context the count falls back to the extension list.
        expect(admitsCompareSelection(compareOnlyViewer, [".txt", ".md"])).toBe(true);
        expect(admitsCompareSelection(compareOnlyViewer, [".txt"])).toBe(false);
    });

    it("applies the selection-shape gates from compareShape", () => {
        const versionsOnly = viewer({
            compareMode: compare({ allowDifferentFiles: false, allowCrossAsset: false }),
        });
        expect(admitsCompareSelection(versionsOnly, [".txt"], twoVersions)).toBe(true);
        expect(admitsCompareSelection(versionsOnly, [".txt"], twoDistinctFiles)).toBe(false);
        expect(
            admitsCompareSelection(versionsOnly, [".txt"], { ...twoVersions, crossAsset: true })
        ).toBe(false);
    });
});
