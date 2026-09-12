/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    hasViewerForExtensions,
    isViewableExtension,
    areFilenamesViewableTogether,
    areFilesComparableTogether,
    isExtensionComparableAsVersions,
    availableModesForFiles,
    extensionOfFilename,
    clearViewableExtensionCache,
} from "./viewableExtensions";

// A stand-in registry: .glb and .png are renderable on their own; only .las + .laz share a viewer.
// Compare mode: a text differ takes exactly two .txt/.md entries, same-asset only (no cross-asset).
// The jest.mock factory is hoisted, so the spy is created INSIDE it and read back through require().
jest.mock("./PluginRegistry", () => {
    const text = [".txt", ".md"];
    const getCompatibleViewers = jest.fn(
        (
            exts: string[],
            isMultiFile: boolean,
            _isPreview?: boolean,
            mode?: string,
            context?: { fileCount: number; shape: string; crossAsset?: boolean }
        ) => {
            if (mode === "compare") {
                const allText = exts.every((e) => text.includes(e));
                if (allText && context?.fileCount === 2 && !context.crossAsset) {
                    return [{ config: { id: "text-diff" } }];
                }
                return [];
            }
            const single = [".glb", ".png", ".txt"];
            const cloud = [".las", ".laz"];
            const allSingle = exts.every((e) => single.includes(e));
            const allCloud = exts.every((e) => cloud.includes(e));
            if (allCloud) return [{ config: { id: "potree" } }];
            if (allSingle && !isMultiFile) return [{ config: { id: "threejs" } }];
            return [];
        }
    );
    const getAvailableModes = jest.fn(
        (
            exts: string[],
            isMultiFile: boolean,
            context?: { fileCount: number; shape: string; crossAsset?: boolean }
        ) => ({
            visualize: getCompatibleViewers(exts, isMultiFile, false).length > 0,
            compare: getCompatibleViewers(exts, isMultiFile, false, "compare", context).length > 0,
        })
    );
    return {
        PluginRegistry: {
            getInstance: () => ({
                getCompatibleViewers,
                getAvailableModes,
                isInitialized: () => true,
            }),
        },
    };
});

const registrySpy = (): jest.Mock =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("./PluginRegistry").PluginRegistry.getInstance().getCompatibleViewers;

describe("extensionOfFilename", () => {
    it("returns the extension including the dot", () => {
        expect(extensionOfFilename("body.glb")).toBe(".glb");
        expect(extensionOfFilename("a/b/c.PNG")).toBe(".PNG");
    });

    it("returns undefined when there is no usable extension", () => {
        expect(extensionOfFilename(undefined)).toBeUndefined();
        expect(extensionOfFilename("README")).toBeUndefined();
        expect(extensionOfFilename("trailingdot.")).toBeUndefined();
        expect(extensionOfFilename(".hidden")).toBeUndefined(); // leading dot only
    });
});

describe("isViewableExtension", () => {
    beforeEach(() => {
        clearViewableExtensionCache();
        registrySpy().mockClear();
    });

    it("accepts an extension with or without the leading dot", () => {
        expect(isViewableExtension("glb")).toBe(true);
        expect(isViewableExtension(".glb")).toBe(true);
        expect(isViewableExtension("GLB")).toBe(true);
    });

    it("rejects an unrenderable or missing extension", () => {
        expect(isViewableExtension("zzz")).toBe(false);
        expect(isViewableExtension(undefined)).toBe(false);
        expect(isViewableExtension("")).toBe(false);
    });

    it("asks the registry once per extension", () => {
        for (let i = 0; i < 20; i++) isViewableExtension("glb");
        expect(registrySpy()).toHaveBeenCalledTimes(1);
    });
});

describe("hasViewerForExtensions", () => {
    beforeEach(() => {
        clearViewableExtensionCache();
        registrySpy().mockClear();
    });

    it("is false for an empty set rather than vacuously true", () => {
        expect(hasViewerForExtensions([], false)).toBe(false);
        expect(registrySpy()).not.toHaveBeenCalled();
    });

    it("keys the cache on the set, not on one member", () => {
        expect(hasViewerForExtensions([".las", ".laz"], true)).toBe(true);
        expect(hasViewerForExtensions([".glb", ".laz"], true)).toBe(false);
        expect(registrySpy()).toHaveBeenCalledTimes(2);
    });

    it("treats a reordered set as the same question", () => {
        hasViewerForExtensions([".las", ".laz"], true);
        hasViewerForExtensions([".laz", ".las"], true);
        expect(registrySpy()).toHaveBeenCalledTimes(1);
    });
});

describe("areFilenamesViewableTogether", () => {
    beforeEach(() => clearViewableExtensionCache());

    it("is true when one viewer covers every selected file", () => {
        expect(areFilenamesViewableTogether(["a.las", "b.laz"])).toBe(true);
    });

    it("is false for a mixed selection no single viewer covers", () => {
        expect(areFilenamesViewableTogether(["a.glb", "b.laz"])).toBe(false);
    });

    it("is false when any selected file has no extension", () => {
        expect(areFilenamesViewableTogether(["a.glb", "README"])).toBe(false);
    });

    it("is false for an empty selection", () => {
        expect(areFilenamesViewableTogether([])).toBe(false);
    });
});

// ---------------------------------------------------------------------------------------------
// Compare path
// ---------------------------------------------------------------------------------------------

const inAsset = (filename: string, versionId?: string) => ({
    filename,
    key: `/${filename}`,
    assetId: "asset-1",
    databaseId: "db-1",
    versionId,
});

describe("areFilesComparableTogether", () => {
    beforeEach(() => {
        clearViewableExtensionCache();
        registrySpy().mockClear();
    });

    it("is true for two text files one differ admits", () => {
        expect(areFilesComparableTogether([inAsset("a.txt"), inAsset("b.md")])).toBe(true);
    });

    it("is true for two versions of one text file", () => {
        expect(areFilesComparableTogether([inAsset("a.txt", "v1"), inAsset("a.txt", "v2")])).toBe(
            true
        );
    });

    it("is false outside the differ's file-count window", () => {
        expect(areFilesComparableTogether([inAsset("a.txt")])).toBe(false);
        expect(
            areFilesComparableTogether([inAsset("a.txt"), inAsset("b.txt"), inAsset("c.txt")])
        ).toBe(false);
    });

    it("is false for a type no differ handles, even though a visualizer exists", () => {
        expect(areFilesComparableTogether([inAsset("a.png"), inAsset("b.png")])).toBe(false);
        expect(areFilesComparableTogether([inAsset("a.txt"), inAsset("b.png")])).toBe(false);
    });

    it("passes the real selection shape to the registry (cross-asset is refused here)", () => {
        const other = { ...inAsset("b.txt"), assetId: "asset-2" };
        expect(areFilesComparableTogether([inAsset("a.txt"), other])).toBe(false);
        const [, , , mode, context] = registrySpy().mock.calls.at(-1)!;
        expect(mode).toBe("compare");
        expect(context).toEqual({
            fileCount: 2,
            shape: "different-files",
            crossAsset: true,
        });
    });

    it("is false for an empty selection or an extension-less file", () => {
        expect(areFilesComparableTogether([])).toBe(false);
        expect(areFilesComparableTogether([inAsset("a.txt"), inAsset("README")])).toBe(false);
    });

    it("memoizes per extension set AND shape", () => {
        areFilesComparableTogether([inAsset("a.txt"), inAsset("b.txt")]);
        areFilesComparableTogether([inAsset("b.txt"), inAsset("a.txt")]);
        expect(registrySpy()).toHaveBeenCalledTimes(1);
        // Same extensions, different shape: a new question.
        areFilesComparableTogether([inAsset("a.txt", "v1"), inAsset("a.txt", "v2")]);
        expect(registrySpy()).toHaveBeenCalledTimes(2);
    });
});

describe("isExtensionComparableAsVersions", () => {
    beforeEach(() => clearViewableExtensionCache());

    it("is true for a type a differ diffs as two versions of one file", () => {
        expect(isExtensionComparableAsVersions(".txt")).toBe(true);
        expect(isExtensionComparableAsVersions("md")).toBe(true);
    });

    it("is false for a type no differ handles, or no extension", () => {
        expect(isExtensionComparableAsVersions(".png")).toBe(false);
        expect(isExtensionComparableAsVersions(".glb")).toBe(false);
        expect(isExtensionComparableAsVersions(undefined)).toBe(false);
        expect(isExtensionComparableAsVersions("")).toBe(false);
    });
});

describe("availableModesForFiles", () => {
    beforeEach(() => clearViewableExtensionCache());

    it("reports Visualize only for one text file", () => {
        expect(availableModesForFiles([inAsset("a.txt")])).toEqual({
            visualize: true,
            compare: false,
        });
    });

    it("reports Compare only for two text files", () => {
        expect(availableModesForFiles([inAsset("a.txt"), inAsset("b.txt")])).toEqual({
            visualize: false,
            compare: true,
        });
    });

    it("reports Visualize only for two point clouds", () => {
        expect(availableModesForFiles([inAsset("a.las"), inAsset("b.laz")])).toEqual({
            visualize: true,
            compare: false,
        });
    });

    it("reports neither for two images or an extension-less file", () => {
        expect(availableModesForFiles([inAsset("a.png"), inAsset("b.png")])).toEqual({
            visualize: false,
            compare: false,
        });
        expect(availableModesForFiles([inAsset("README")])).toEqual({
            visualize: false,
            compare: false,
        });
        expect(availableModesForFiles([])).toEqual({ visualize: false, compare: false });
    });
});
