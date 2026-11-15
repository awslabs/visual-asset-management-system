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
    isFilenameComparableAsVersions,
    isViewableFilename,
    availableModesForFiles,
    extensionOfFilename,
    clearViewableExtensionCache,
} from "./viewableExtensions";

// A stand-in registry: .glb and .png are renderable on their own; only .las + .laz share a viewer.
// Compare mode: a text differ takes exactly two .txt/.md entries, same-asset only (no cross-asset).
// The jest.mock factory is hoisted, so the spy is created INSIDE it and read back through require().
// Like the shipped text viewers, the differ and the single-file viewer also list "(none)", the
// no-extension token (#1198).
jest.mock("./PluginRegistry", () => {
    const text = [".txt", ".md", "(none)"];
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
            const single = [".glb", ".png", ".txt", "(none)"];
            const cloud = [".las", ".laz"];
            const allSingle = exts.every((e) => single.includes(e));
            const allCloud = exts.every((e) => cloud.includes(e));
            if (allCloud) return [{ config: { id: "potree" } }];
            if (allSingle && !isMultiFile) return [{ config: { id: "threejs" } }];
            return [];
        }
    );
    // Mirrors the real registry: `getAvailableModes` judges the selection through the pure
    // `availableViewerModes`, which maps a lone file onto the two-versions compare question.
    const { compareContextForSelection } = jest.requireActual("./viewerSelection");
    const getAvailableModes = jest.fn(
        (
            exts: string[],
            isMultiFile: boolean,
            context?: { fileCount: number; shape: string; crossAsset?: boolean }
        ) => ({
            visualize: getCompatibleViewers(exts, isMultiFile, false).length > 0,
            compare:
                getCompatibleViewers(
                    exts,
                    isMultiFile,
                    false,
                    "compare",
                    compareContextForSelection(exts, isMultiFile, context)
                ).length > 0,
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

    it("applies the shared dotfile rule and reads only the last path segment (#1198)", () => {
        expect(extensionOfFilename("..hidden")).toBeUndefined();
        expect(extensionOfFilename(".eslintrc.json")).toBe(".json");
        expect(extensionOfFilename("config.d/README")).toBeUndefined();
    });
});

describe("filename-based gates agree on one file (#1198)", () => {
    beforeEach(() => clearViewableExtensionCache());

    it.each([
        ["README", true, true],
        ["docs/LICENSE", true, true],
        [".gitignore", true, true],
        ["notes.txt", true, true],
        ["model.glb", true, false],
        ["file.", true, true],
        [".env.example", false, false],
        ["folder/", false, false],
        ["", false, false],
    ])("%s: viewable %s, comparable as versions %s", (name, viewable, comparable) => {
        expect(isViewableFilename(name)).toBe(viewable);
        expect(isFilenameComparableAsVersions(name)).toBe(comparable);
        // The single-file modal asks the same questions through availableModesForFiles.
        const modes = name ? availableModesForFiles([inAsset(name)]) : null;
        if (modes && !name.endsWith("/")) {
            expect(modes).toEqual({ visualize: viewable, compare: comparable });
        }
    });

    it("never hands the registry a '.(none)' token", () => {
        registrySpy().mockClear();
        isViewableFilename("README");
        areFilenamesViewableTogether(["README", "LICENSE"]);
        availableModesForFiles([inAsset("README")]);
        const tokens = registrySpy().mock.calls.flatMap(([exts]) => exts as string[]);
        expect(tokens).toContain("(none)");
        expect(tokens).not.toContain(".(none)");
    });

    it("keys the cache on the token, apart from real extensions", () => {
        registrySpy().mockClear();
        isViewableFilename("README");
        isViewableFilename("LICENSE");
        expect(registrySpy()).toHaveBeenCalledTimes(1);
        isViewableFilename("notes.txt");
        expect(registrySpy()).toHaveBeenCalledTimes(2);
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

    it("is false when an extensionless file joins a selection no viewer covers whole", () => {
        // README contributes the no-extension token; no multi-file viewer lists it with .glb.
        expect(areFilenamesViewableTogether(["a.glb", "README"])).toBe(false);
        expect(areFilenamesViewableTogether(["a.las", "README"])).toBe(false);
    });

    it("is true for one extensionless file a viewer lists the token for (#1198)", () => {
        expect(areFilenamesViewableTogether(["README"])).toBe(true);
    });

    it("is false when an entry names no file (a folder key)", () => {
        expect(areFilenamesViewableTogether(["a.las", "folder/"])).toBe(false);
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

    it("is false for an empty selection", () => {
        expect(areFilesComparableTogether([])).toBe(false);
    });

    it("admits an extensionless file beside a text file when the differ lists both (#1198)", () => {
        // LICENSE vs LICENSE.txt: tokens ["(none)", ".txt"], both listed by the differ.
        expect(areFilesComparableTogether([inAsset("LICENSE"), inAsset("LICENSE.txt")])).toBe(true);
        // README beside an image: the differ does not list .png.
        expect(areFilesComparableTogether([inAsset("README"), inAsset("a.png")])).toBe(false);
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
    beforeEach(() => {
        clearViewableExtensionCache();
        registrySpy().mockClear();
    });

    it("reports BOTH modes for one text file — judged as two versions of itself", () => {
        expect(availableModesForFiles([inAsset("a.txt")])).toEqual({
            visualize: true,
            compare: true,
        });
        // The compare question put to the registry was the two-versions one, same-asset.
        const compareCall = registrySpy().mock.calls.find(([, , , mode]) => mode === "compare")!;
        expect(compareCall[4]).toEqual({
            fileCount: 2,
            shape: "same-file-versions",
            crossAsset: false,
        });
        // ...and it is exactly the gate a version list's per-row Compare uses.
        expect(isExtensionComparableAsVersions(".txt")).toBe(true);
    });

    it("reports Visualize only for one file no differ handles", () => {
        expect(availableModesForFiles([inAsset("a.png")])).toEqual({
            visualize: true,
            compare: false,
        });
        expect(availableModesForFiles([inAsset("a.glb")])).toEqual({
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

    it("reports neither for two images or an empty selection", () => {
        expect(availableModesForFiles([inAsset("a.png"), inAsset("b.png")])).toEqual({
            visualize: false,
            compare: false,
        });
        expect(availableModesForFiles([])).toEqual({ visualize: false, compare: false });
    });

    it("reports BOTH modes for one extensionless file (#1198)", () => {
        expect(availableModesForFiles([inAsset("README")])).toEqual({
            visualize: true,
            compare: true,
        });
    });

    it("reports neither for an extensionless file beside a 3D model", () => {
        expect(availableModesForFiles([inAsset("README"), inAsset("model.glb")])).toEqual({
            visualize: false,
            compare: false,
        });
    });
});
