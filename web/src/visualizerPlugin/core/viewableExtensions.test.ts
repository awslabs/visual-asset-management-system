/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    hasViewerForExtensions,
    isViewableExtension,
    areFilenamesViewableTogether,
    extensionOfFilename,
    clearViewableExtensionCache,
} from "./viewableExtensions";

// A stand-in registry: .glb and .png are renderable on their own; only .las + .laz share a viewer.
// The jest.mock factory is hoisted, so the spy is created INSIDE it and read back through require().
jest.mock("./PluginRegistry", () => {
    const getCompatibleViewers = jest.fn((exts: string[], isMultiFile: boolean) => {
        const single = [".glb", ".png"];
        const cloud = [".las", ".laz"];
        const allSingle = exts.every((e) => single.includes(e));
        const allCloud = exts.every((e) => cloud.includes(e));
        if (allCloud) return [{ config: { id: "potree" } }];
        if (allSingle && (!isMultiFile || exts.length === 1))
            return [{ config: { id: "threejs" } }];
        return [];
    });
    return {
        PluginRegistry: {
            getInstance: () => ({ getCompatibleViewers, isInitialized: () => true }),
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
