/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from "fs";
import path from "path";
import { hasLocationOnlyMetadata, isFileHitSource, RECORD_TYPE_FIELD } from "./recordType";
import { FIELD_MAPPINGS } from "../types";

describe("record-type discriminator", () => {
    it("names the field the backend indexers stamp", () => {
        expect(RECORD_TYPE_FIELD).toBe("str_rectype");
    });

    it("classifies a file document from its own record type", () => {
        expect(isFileHitSource({ str_rectype: "file", str_key: "/a.glb" }, false)).toBe(true);
    });

    it("classifies an asset document from its own record type", () => {
        expect(isFileHitSource({ str_rectype: "asset", str_assetname: "Pump" }, false)).toBe(false);
    });

    it("ignores the legacy underscore key, which no document carries", () => {
        expect(isFileHitSource({ _rectype: "file" } as Record<string, any>, false)).toBe(false);
    });

    it("treats file search mode as authoritative over the document", () => {
        expect(isFileHitSource({ str_rectype: "asset" }, true)).toBe(true);
    });

    it("tolerates a missing source", () => {
        expect(isFileHitSource(undefined, false)).toBe(false);
        expect(isFileHitSource(null, false)).toBe(false);
    });
});

describe("search field map", () => {
    it("labels the live record-type field and no stale spelling", () => {
        const keys = Object.keys(FIELD_MAPPINGS);
        // Control: a broken import would leave this empty and make the checks vacuous.
        expect(keys.length).toBeGreaterThan(10);

        const rectypeKeys = keys.filter((key) => key.toLowerCase().includes("rectype"));
        expect(rectypeKeys).toEqual([RECORD_TYPE_FIELD]);
    });
});

describe("search components", () => {
    /**
     * The map view is the only surface that reads the discriminator off a hit source.
     * Reading it inline again would reintroduce the stale key without failing anything
     * else, so no component may dereference the legacy name on a document.
     */
    it("never read the legacy record-type key off a hit source", () => {
        const searchDir = path.join(__dirname, "..");
        const files: string[] = [];
        const walk = (dir: string) => {
            for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
                // Unit test walking the repo's own source tree: the joined segment is a readdirSync
                // entry, not external input.
                // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
                const full = path.join(dir, entry.name);
                if (entry.isDirectory()) walk(full);
                else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name))
                    files.push(full);
            }
        };
        walk(searchDir);

        // Control: assert the walk actually found the search sources.
        expect(files.length).toBeGreaterThan(20);

        // `filters._rectype` is client-side search state, not a document field, and stays.
        const documentRead = /(?:source|_source|hit|row|item)\s*[?]?\.\s*_rectype\b/;
        const offenders = files.filter((file) => documentRead.test(fs.readFileSync(file, "utf-8")));
        expect(offenders).toEqual([]);
    });
});

// A natural-language asset hit carries its asset's location keys in MD_, not its metadata (D83).
describe("hasLocationOnlyMetadata", () => {
    const vector = { distance: 0.1 };
    const asset = { [RECORD_TYPE_FIELD]: "asset", MD_: { latitude: "1", longitude: "2" } };
    const file = { [RECORD_TYPE_FIELD]: "file", MD_: { latitude: "1", longitude: "2" } };

    it("is true for a natural-language asset hit", () => {
        expect(hasLocationOnlyMetadata(asset, vector, false)).toBe(true);
    });

    it("is false for a natural-language file hit, which carries its file's whole MD_", () => {
        expect(hasLocationOnlyMetadata(file, vector, false)).toBe(false);
        expect(hasLocationOnlyMetadata(asset, vector, true)).toBe(false);
    });

    it("is false for a keyword hit, which has no vector details", () => {
        expect(hasLocationOnlyMetadata(asset, undefined, false)).toBe(false);
    });
});
