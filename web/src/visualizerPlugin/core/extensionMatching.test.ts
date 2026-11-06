/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    describeExtensionToken,
    extensionToken,
    filenameExtension,
    NO_EXTENSION,
    supportsAllExtensions,
    WILDCARD_EXTENSION,
} from "./extensionMatching";

const THREEJS = [".gltf", ".glb", ".obj", ".fbx", ".stl", ".ply"];
const POTREE = [".e57", ".las", ".laz", ".ply"];

describe("supportsAllExtensions", () => {
    it("excludes a viewer that covers only part of a mixed selection", () => {
        // The reported case: .glb + .laz offered the Three.js viewer on the strength of .glb alone,
        // and it then failed on the point cloud it can never read.
        expect(supportsAllExtensions(THREEJS, [".glb", ".laz"])).toBe(false);
        expect(supportsAllExtensions(POTREE, [".glb", ".laz"])).toBe(false);
    });

    it("includes a viewer that covers every extension in the selection", () => {
        expect(supportsAllExtensions(POTREE, [".las", ".laz"])).toBe(true);
        expect(supportsAllExtensions(THREEJS, [".glb", ".obj", ".stl"])).toBe(true);
    });

    it("still matches a single supported file", () => {
        expect(supportsAllExtensions(THREEJS, [".glb"])).toBe(true);
        expect(supportsAllExtensions(POTREE, [".laz"])).toBe(true);
    });

    it("rejects a single unsupported file", () => {
        expect(supportsAllExtensions(THREEJS, [".laz"])).toBe(false);
    });

    it("matches an extension whose case differs", () => {
        expect(supportsAllExtensions(THREEJS, [".GLB"])).toBe(true);
        expect(supportsAllExtensions(THREEJS, [".GLB", ".Obj"])).toBe(true);
    });

    it("matches everything for a wildcard viewer, including mixed selections", () => {
        expect(supportsAllExtensions([WILDCARD_EXTENSION], [".glb", ".laz", ".zzz"])).toBe(true);
    });

    it("matches nothing when the selection is empty", () => {
        // Array.every is vacuously true on an empty array, which would report every viewer as
        // compatible with no files at all.
        expect(supportsAllExtensions(THREEJS, [])).toBe(false);
        expect(supportsAllExtensions([WILDCARD_EXTENSION], [])).toBe(false);
    });

    it("matches nothing when the viewer declares no extensions", () => {
        expect(supportsAllExtensions([], [".glb"])).toBe(false);
    });

    it("treats a shared extension as compatible for both viewers", () => {
        // .ply is claimed by both, so either may render a .ply-only selection.
        expect(supportsAllExtensions(THREEJS, [".ply"])).toBe(true);
        expect(supportsAllExtensions(POTREE, [".ply"])).toBe(true);
    });
});

describe("filenameExtension / extensionToken: the shared dotfile rule (#1198)", () => {
    it.each([
        ["README", "", NO_EXTENSION],
        ["LICENSE", "", NO_EXTENSION],
        [".gitignore", "", NO_EXTENSION],
        [".bashrc", "", NO_EXTENSION],
        ["..hidden", "", NO_EXTENSION],
        [".eslintrc.json", ".json", ".json"],
        [".env.example", ".example", ".example"],
        ["file.", "", NO_EXTENSION],
        ["notes.txt", ".txt", ".txt"],
        ["a.TAR.GZ", ".GZ", ".gz"],
        ["Makefile", "", NO_EXTENSION],
        ["xasset1/config.d/README", "", NO_EXTENSION],
        ["xasset1/docs/Guide.MD", ".MD", ".md"],
    ])("%s → extension %j, token %j", (name, extension, matchToken) => {
        expect(filenameExtension(name)).toBe(extension);
        expect(extensionToken(name)).toBe(matchToken);
    });

    it("gives no token when there is no file name (absent, or a folder key)", () => {
        expect(extensionToken(undefined)).toBeUndefined();
        expect(extensionToken("")).toBeUndefined();
        expect(extensionToken("xasset1/folder/")).toBeUndefined();
        expect(filenameExtension(undefined)).toBe("");
    });

    it("never produces a token that a real extension could collide with", () => {
        expect(NO_EXTENSION.startsWith(".")).toBe(false);
        expect(extensionToken("x.(none)")).toBe(".(none)");
    });
});

describe("supportsAllExtensions with the no-extension token (#1198)", () => {
    const TEXT = [".txt", ".md", NO_EXTENSION];

    it("matches an extensionless file only for a viewer that lists the token", () => {
        expect(supportsAllExtensions(TEXT, [NO_EXTENSION])).toBe(true);
        expect(supportsAllExtensions(THREEJS, [NO_EXTENSION])).toBe(false);
    });

    it("needs the token AND the other extensions for a mixed selection", () => {
        expect(supportsAllExtensions(TEXT, [NO_EXTENSION, ".txt"])).toBe(true);
        expect(supportsAllExtensions(TEXT, [NO_EXTENSION, ".glb"])).toBe(false);
        expect(supportsAllExtensions(THREEJS, [NO_EXTENSION, ".glb"])).toBe(false);
    });

    it("lets the wildcard match the token too", () => {
        expect(supportsAllExtensions([WILDCARD_EXTENSION], [NO_EXTENSION])).toBe(true);
    });
});

describe("describeExtensionToken", () => {
    it("names the token in words and passes real extensions through", () => {
        expect(describeExtensionToken(NO_EXTENSION)).toBe("no extension");
        expect(describeExtensionToken(".glb")).toBe(".glb");
    });
});
