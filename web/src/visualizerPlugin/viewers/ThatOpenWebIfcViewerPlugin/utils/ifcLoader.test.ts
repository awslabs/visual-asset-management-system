/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// These are pure-function tests (no DOM), so they run on the lightweight `node`
// jest environment instead of jsdom. This keeps them self-contained and avoids
// adding any test-environment dependency to the core web package.

import { isIfcZip, pickIfcEntryName, extractIfcBytes } from "./ifcLoader";

describe("ifcLoader pure helpers", () => {
    describe("isIfcZip", () => {
        it("returns true for .ifczip regardless of case", () => {
            expect(isIfcZip("building.ifczip")).toBe(true);
            expect(isIfcZip("BUILDING.IFCZIP")).toBe(true);
            expect(isIfcZip("a/b/c/model.ifcZIP")).toBe(true);
        });
        it("returns false for .ifc and others", () => {
            expect(isIfcZip("building.ifc")).toBe(false);
            expect(isIfcZip("model.zip")).toBe(false);
            expect(isIfcZip("noext")).toBe(false);
        });
    });

    describe("pickIfcEntryName", () => {
        it("selects the single .ifc entry", () => {
            expect(pickIfcEntryName(["model.ifc"])).toBe("model.ifc");
        });
        it("selects the first .ifc entry when multiple files exist", () => {
            expect(pickIfcEntryName(["readme.txt", "a.ifc", "b.ifc"])).toBe("a.ifc");
        });
        it("is case-insensitive on the extension", () => {
            expect(pickIfcEntryName(["MODEL.IFC"])).toBe("MODEL.IFC");
        });
        it("returns null when no .ifc entry exists", () => {
            expect(pickIfcEntryName(["readme.txt", "data.json"])).toBeNull();
        });
    });

    describe("extractIfcBytes", () => {
        // A minimal fake of window.ThatOpenWebIfcBundle exposing only unzipSync.
        const makeBundle = (entries: Record<string, Uint8Array>) => ({
            unzipSync: () => entries,
        });

        it("passes .ifc bytes through unchanged without unzipping", () => {
            const original = new Uint8Array([1, 2, 3, 4]);
            // unzipSync would throw if called — proves passthrough does not unzip.
            const bundle = {
                unzipSync: () => {
                    throw new Error("unzipSync should not be called for .ifc");
                },
            };
            const result = extractIfcBytes(bundle, original.buffer, "model.ifc");
            expect(Array.from(result)).toEqual([1, 2, 3, 4]);
        });

        it("unzips a .ifczip and returns the .ifc entry bytes", () => {
            const ifcBytes = new Uint8Array([9, 8, 7]);
            const bundle = makeBundle({
                "readme.txt": new Uint8Array([0]),
                "model.ifc": ifcBytes,
            });
            const archive = new Uint8Array([0, 0]).buffer;
            const result = extractIfcBytes(bundle, archive, "building.ifczip");
            expect(Array.from(result)).toEqual([9, 8, 7]);
        });

        it("throws when a .ifczip contains no .ifc entry", () => {
            const bundle = makeBundle({ "readme.txt": new Uint8Array([0]) });
            expect(() =>
                extractIfcBytes(bundle, new Uint8Array([0]).buffer, "empty.ifczip")
            ).toThrow(/No \.ifc file found/);
        });
    });
});
