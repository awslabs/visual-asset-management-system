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
    availableViewerModes,
    hasCompareViewer,
    hasVisualizeViewer,
    isCompareOnlyViewer,
} from "./viewerSelection";
import { CompareContext } from "./compareShape";
import { CompareModeConfig, ViewerConfig, ViewerPluginConfig } from "./types";
import shippedConfig from "../config/viewerConfig.json";

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

// ---------------------------------------------------------------------------------------------
// Selection-level helpers: the inputs for the Visualize/Compare toggle and the "View / Compare
// Selected" gates (FileViewerModal, SearchPageListView, FileDetailsPanel, version lists).
// ---------------------------------------------------------------------------------------------

/** A regular single-file text viewer, like the shipped text-viewer. */
const singleTextViewer = viewer({ id: "text-viewer" });
/** A regular multi-file viewer for point clouds (nothing to do with text). */
const multiCloudViewer = viewer({
    id: "potree",
    supportedExtensions: [".las", ".laz"],
    supportsMultiFile: true,
});
/** The preview viewer is never a candidate on either path. */
const previewViewer = viewer({
    id: "preview-viewer",
    isPreviewViewer: true,
    supportedExtensions: [".txt", ".png", ".las"],
    supportsMultiFile: true,
    compareMode: compare(),
});
const registry = [singleTextViewer, multiCloudViewer, compareOnlyViewer, previewViewer];

describe("hasVisualizeViewer / hasCompareViewer", () => {
    it("counts only regular viewers towards visualize", () => {
        expect(hasVisualizeViewer(registry, [".txt"], false)).toBe(true);
        // Two text files: the only multi-file text-capable viewer is the compare-only differ.
        expect(hasVisualizeViewer(registry, [".txt"], true)).toBe(false);
        expect(hasVisualizeViewer(registry, [".las", ".laz"], true)).toBe(true);
        expect(hasVisualizeViewer([compareOnlyViewer], [".txt"], false)).toBe(false);
    });

    it("counts only compare-capable viewers towards compare", () => {
        expect(hasCompareViewer(registry, [".txt"], twoDistinctFiles)).toBe(true);
        expect(hasCompareViewer(registry, [".txt"], twoVersions)).toBe(true);
        expect(hasCompareViewer(registry, [".las"], twoDistinctFiles)).toBe(false);
        expect(
            hasCompareViewer(registry, [".txt"], { fileCount: 1, shape: "same-file-versions" })
        ).toBe(false);
        expect(
            hasCompareViewer(registry, [".txt"], { fileCount: 3, shape: "different-files" })
        ).toBe(false);
    });

    it("never lets the preview viewer answer either question", () => {
        expect(hasVisualizeViewer([previewViewer], [".png"], false)).toBe(false);
        expect(hasCompareViewer([previewViewer], [".png"], twoDistinctFiles)).toBe(false);
    });
});

describe("availableViewerModes", () => {
    it("offers only Visualize for one text file", () => {
        expect(availableViewerModes(registry, [".txt"], false)).toEqual({
            visualize: true,
            compare: false,
        });
    });

    it("offers only Compare for two text files (no multi-file text visualizer)", () => {
        expect(availableViewerModes(registry, [".txt"], true, twoDistinctFiles)).toEqual({
            visualize: false,
            compare: true,
        });
        expect(availableViewerModes(registry, [".txt"], true, twoVersions)).toEqual({
            visualize: false,
            compare: true,
        });
    });

    it("offers only Visualize for two point clouds (no differ for them)", () => {
        expect(availableViewerModes(registry, [".las", ".laz"], true, twoDistinctFiles)).toEqual({
            visualize: true,
            compare: false,
        });
    });

    it("offers neither for a type nothing handles, or a mixed selection", () => {
        expect(availableViewerModes(registry, [".png"], false)).toEqual({
            visualize: false,
            compare: false,
        });
        expect(availableViewerModes(registry, [".txt", ".las"], true, twoDistinctFiles)).toEqual({
            visualize: false,
            compare: false,
        });
    });

    it("offers both when a hybrid viewer admits the selection on both paths", () => {
        const both = [viewer({ id: "dual", supportsMultiFile: true, compareMode: compare() })];
        expect(availableViewerModes(both, [".txt"], true, twoDistinctFiles)).toEqual({
            visualize: true,
            compare: true,
        });
    });
});

// ---------------------------------------------------------------------------------------------
// Config guard over the SHIPPED viewerConfig.json. The design rule is: a viewer is EITHER a
// compare-differ (compareMode.compareOnly) OR a regular viewer — no hybrid. These assertions fail
// the build if someone adds a compare block without compareOnly, or a compare-only viewer that the
// visualize path would offer for any file count.
// ---------------------------------------------------------------------------------------------

describe("shipped viewerConfig.json compare contract", () => {
    const shipped = (shippedConfig as ViewerConfig).viewers;
    const compareCapable = shipped.filter((v) => v.compareMode?.enabled);
    const compareOnly = shipped.filter(isCompareOnlyViewer);

    it("ships at least one compare-only viewer (the text differ)", () => {
        expect(compareOnly.map((v) => v.id)).toContain("text-diff-viewer");
    });

    it("has no hybrid: every compare-capable viewer is compare-only", () => {
        const hybrids = compareCapable.filter((v) => !isCompareOnlyViewer(v)).map((v) => v.id);
        expect(hybrids).toEqual([]);
    });

    it("keeps compare-only viewers off the visualize path for ANY file count", () => {
        for (const v of compareOnly) {
            for (const ext of v.supportedExtensions) {
                expect(admitsVisualizeSelection(v, [ext], false)).toBe(false); // one file
                expect(admitsVisualizeSelection(v, [ext], true)).toBe(false); // N files
            }
            // ... and regardless of the (visualize-only) multi-file flag.
            expect(v.supportsMultiFile).toBe(false);
        }
    });

    it("still offers each compare-only viewer inside its own file-count window", () => {
        for (const v of compareOnly) {
            const { minFiles, maxFiles } = v.compareMode!;
            const ext = v.supportedExtensions[0];
            expect(
                admitsCompareSelection(v, [ext], { fileCount: minFiles, shape: "different-files" })
            ).toBe(true);
            expect(
                admitsCompareSelection(v, [ext], {
                    fileCount: minFiles,
                    shape: "same-file-versions",
                })
            ).toBe(true);
            expect(
                admitsCompareSelection(v, [ext], {
                    fileCount: minFiles - 1,
                    shape: "different-files",
                })
            ).toBe(false);
            expect(
                admitsCompareSelection(v, [ext], {
                    fileCount: maxFiles + 1,
                    shape: "different-files",
                })
            ).toBe(false);
        }
    });

    it("offers a differ, and only a differ, for two shipped text files", () => {
        const modes = availableViewerModes(shipped, [".txt"], true, twoDistinctFiles);
        expect(modes.compare).toBe(true);
        expect(modes.visualize).toBe(false);
        // A binary type no differ handles gets no compare mode.
        expect(availableViewerModes(shipped, [".png"], true, twoDistinctFiles).compare).toBe(false);
    });
});
