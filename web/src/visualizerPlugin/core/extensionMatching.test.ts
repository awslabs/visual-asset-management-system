/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { supportsAllExtensions, WILDCARD_EXTENSION } from "./extensionMatching";

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
