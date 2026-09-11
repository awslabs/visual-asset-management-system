/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// These are pure-function tests (no DOM), so they run on the lightweight `node`
// jest environment instead of jsdom. This keeps them self-contained and avoids
// adding any test-environment dependency to the core web package.

import {
    isIfcZip,
    pickIfcEntryName,
    extractIfcBytes,
    assertIfcSizeWithinLimit,
    formatByteSize,
    MAX_IFC_BYTES,
} from "./ifcLoader";

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

    // A .ifczip is inflated synchronously on the main thread, so an archive that
    // declares gigabytes of output must be refused from its central directory
    // rather than after allocation.
    describe("size limits", () => {
        /**
         * Stands in for the bundle's fflate: honours the `filter` option and
         * reports the same field names fflate does — `size` is the COMPRESSED
         * size, `originalSize` the inflated one.
         */
        const makeFilteringBundle = (
            entries: Array<{ name: string; originalSize: number; bytes: Uint8Array }>
        ) => {
            const inflated: string[] = [];
            return {
                inflated,
                unzipSync: (_data: Uint8Array, opts?: any) => {
                    const out: Record<string, Uint8Array> = {};
                    for (const entry of entries) {
                        const info = {
                            name: entry.name,
                            size: 4096,
                            originalSize: entry.originalSize,
                            compression: 8,
                        };
                        if (!opts?.filter || opts.filter(info)) {
                            inflated.push(entry.name);
                            out[entry.name] = entry.bytes;
                        }
                    }
                    return out;
                },
            };
        };

        it("refuses an archive whose .ifc entry declares more than the limit, without inflating it", () => {
            const bundle = makeFilteringBundle([
                {
                    name: "bomb.ifc",
                    originalSize: MAX_IFC_BYTES + 1,
                    bytes: new Uint8Array([1, 2, 3]),
                },
            ]);

            expect(() =>
                extractIfcBytes(bundle, new Uint8Array([0, 0]).buffer, "bomb.ifczip")
            ).toThrow(/too large to open in the browser/);
            // The oversized entry was skipped by the filter, i.e. never inflated.
            expect(bundle.inflated).toEqual([]);
        });

        it("still returns a normally sized entry from the same harness", () => {
            // Positive control for the test above: proves the filter harness does
            // not reject every entry, so the rejection there came from the size.
            const bundle = makeFilteringBundle([
                { name: "readme.txt", originalSize: 10, bytes: new Uint8Array([0]) },
                { name: "model.ifc", originalSize: 3, bytes: new Uint8Array([9, 8, 7]) },
            ]);

            const result = extractIfcBytes(bundle, new Uint8Array([0, 0]).buffer, "ok.ifczip");

            expect(Array.from(result)).toEqual([9, 8, 7]);
            expect(bundle.inflated).toEqual(["model.ifc"]);
        });

        it("rejects a declared length over the limit and accepts one at the limit", () => {
            // The download path applies this to Content-Length before buffering,
            // and extractIfcBytes applies it to the inflated result.
            expect(() => assertIfcSizeWithinLimit(MAX_IFC_BYTES + 1, "This IFC file")).toThrow(
                /This IFC file is too large to open in the browser/
            );
            expect(() => assertIfcSizeWithinLimit(MAX_IFC_BYTES, "This IFC file")).not.toThrow();
            expect(() => assertIfcSizeWithinLimit(0, "This IFC file")).not.toThrow();
            expect(() => assertIfcSizeWithinLimit(NaN, "This IFC file")).not.toThrow();
        });

        it("names the limit in units a user can act on", () => {
            expect(formatByteSize(MAX_IFC_BYTES)).toBe("1 GB");
            expect(formatByteSize(250 * 1024 * 1024)).toBe("250 MB");
        });
    });
});
