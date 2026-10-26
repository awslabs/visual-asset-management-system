/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    isRequestableDependencyPath,
    isZipPackage,
    listZipEntryNames,
    normalizePackagedPath,
} from "./usdDependencyPaths";

/** Builds a stored (uncompressed) zip archive the way a .usdz package is laid out. */
const buildZip = (entries: { name: string; data: string }[], comment = ""): ArrayBuffer => {
    const encoder = new TextEncoder();
    const locals: Uint8Array[] = [];
    const centrals: Uint8Array[] = [];
    let offset = 0;
    for (const entry of entries) {
        const name = encoder.encode(entry.name);
        const data = encoder.encode(entry.data);
        const local = new Uint8Array(30 + name.length + data.length);
        const lv = new DataView(local.buffer);
        lv.setUint32(0, 0x04034b50, true);
        lv.setUint32(18, data.length, true);
        lv.setUint32(22, data.length, true);
        lv.setUint16(26, name.length, true);
        local.set(name, 30);
        local.set(data, 30 + name.length);
        const central = new Uint8Array(46 + name.length);
        const cv = new DataView(central.buffer);
        cv.setUint32(0, 0x02014b50, true);
        cv.setUint32(20, data.length, true);
        cv.setUint32(24, data.length, true);
        cv.setUint16(28, name.length, true);
        cv.setUint32(42, offset, true);
        central.set(name, 46);
        locals.push(local);
        centrals.push(central);
        offset += local.length;
    }
    const commentBytes = encoder.encode(comment);
    const directorySize = centrals.reduce((sum, c) => sum + c.length, 0);
    const eocd = new Uint8Array(22 + commentBytes.length);
    const ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, entries.length, true);
    ev.setUint16(10, entries.length, true);
    ev.setUint32(12, directorySize, true);
    ev.setUint32(16, offset, true);
    ev.setUint16(20, commentBytes.length, true);
    eocd.set(commentBytes, 22);
    const parts = [...locals, ...centrals, eocd];
    const out = new Uint8Array(parts.reduce((sum, p) => sum + p.length, 0));
    let at = 0;
    for (const part of parts) {
        out.set(part, at);
        at += part.length;
    }
    return out.buffer;
};

const PACKAGE_ENTRIES = [
    { name: "scene.usda", data: "#usda 1.0\n" },
    { name: "scene_materials/tex/dirt.png", data: "png" },
    { name: "scene_materials/tex/", data: "" },
];

describe("listZipEntryNames", () => {
    it("lists the file entries of a stored zip package and skips directory entries", () => {
        const names = listZipEntryNames(buildZip(PACKAGE_ENTRIES));
        expect(names).toEqual(new Set(["scene.usda", "scene_materials/tex/dirt.png"]));
    });

    it("finds the directory when the archive carries a comment", () => {
        const names = listZipEntryNames(buildZip(PACKAGE_ENTRIES, "written by a test"));
        expect(names?.has("scene_materials/tex/dirt.png")).toBe(true);
    });

    it("returns null for bytes that are not a zip archive", () => {
        expect(listZipEntryNames(new TextEncoder().encode("#usda 1.0\ndef X {}\n").buffer)).toBe(
            null
        );
        expect(listZipEntryNames(new ArrayBuffer(4))).toBe(null);
    });

    it("returns null for a truncated archive rather than reading past it", () => {
        const zip = new Uint8Array(buildZip(PACKAGE_ENTRIES));
        expect(listZipEntryNames(zip.slice(0, zip.length - 30).buffer)).toBe(null);
    });

    it("returns null for a directory that points past the end record", () => {
        const zip = buildZip(PACKAGE_ENTRIES);
        const view = new DataView(zip);
        view.setUint32(zip.byteLength - 22 + 16, zip.byteLength, true);
        expect(listZipEntryNames(zip)).toBe(null);
    });
});

describe("isZipPackage", () => {
    it("recognizes the zip local file header only", () => {
        expect(isZipPackage(buildZip(PACKAGE_ENTRIES))).toBe(true);
        expect(isZipPackage(new TextEncoder().encode("PXR-USDC").buffer)).toBe(false);
        expect(isZipPackage(new ArrayBuffer(2))).toBe(false);
    });
});

describe("normalizePackagedPath", () => {
    it("matches the forms a reference to a package entry is written in", () => {
        for (const written of [
            "scene_materials/tex/dirt.png",
            "./scene_materials/tex/dirt.png",
            "@./scene_materials/tex/dirt.png@",
            " scene_materials//tex/dirt.png ",
            "/scene_materials/tex/dirt.png",
        ]) {
            expect(normalizePackagedPath(written)).toBe("scene_materials/tex/dirt.png");
        }
    });

    it("keeps a reference that leaves the package distinguishable", () => {
        expect(normalizePackagedPath("../shared/dirt.png")).toBe("../shared/dirt.png");
    });
});

describe("isRequestableDependencyPath", () => {
    it("accepts asset-relative paths", () => {
        for (const path of [
            "scene_materials/tex/dirt.png",
            "textures/wood grain.png",
            "a/b/c.usd",
            "file.v2.usdc",
        ]) {
            expect(isRequestableDependencyPath(path)).toBe(true);
        }
    });

    it("rejects text a binary scan picks up from comments and absolute Windows paths", () => {
        for (const path of [
            "Terrain file name: C:\\\\Users\\\\someone\\\\..\\\\TileMaker\\\\terrain.png",
            "C:\\textures\\dirt.png",
            "C:/textures/dirt.png",
            "omniverse://server/library/dirt.png",
            "file:dirt.png",
        ]) {
            expect(isRequestableDependencyPath(path)).toBe(false);
        }
    });

    it("rejects a parent segment left after resolution, control characters and empty paths", () => {
        expect(isRequestableDependencyPath("a/../b.png")).toBe(false);
        expect(isRequestableDependencyPath("dirt\u0007.png")).toBe(false);
        expect(isRequestableDependencyPath("")).toBe(false);
    });
});
