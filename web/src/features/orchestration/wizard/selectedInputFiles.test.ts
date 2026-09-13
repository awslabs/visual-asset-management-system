/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    appendInputFiles,
    describeInputFiles,
    describeKey,
    filterRows,
    inputFileCompatibility,
    inputFileKey,
    isCompleteInputFile,
    parsePastedKeys,
    rowsByAsset,
    summarizeInputFiles,
} from "./selectedInputFiles";
import type { ExecuteInputFile } from "../types";

const file = (assetId: string, relativeFileKey: string, databaseId = "db1"): ExecuteInputFile => ({
    databaseId,
    assetId,
    relativeFileKey,
});

const open = {
    allow: [],
    exclude: [],
    wholeAssetAllowed: true,
    folderAllowed: true,
};

describe("appendInputFiles", () => {
    it("appends new entries and drops duplicates of the selection and of each other", () => {
        const existing = [file("a", "/one.glb")];
        const result = appendInputFiles(existing, [
            file("a", "/one.glb"),
            file("a", "/two.glb"),
            file("a", "/two.glb"),
            file("b", "/one.glb"),
        ]);
        expect(result.files.map(inputFileKey)).toEqual([
            "db1:a:/one.glb",
            "db1:a:/two.glb",
            "db1:b:/one.glb",
        ]);
        expect(result.added).toBe(2);
        expect(result.skipped).toBe(2);
        // The caller's array is not mutated.
        expect(existing).toHaveLength(1);
    });

    it("treats the same key in another database as a distinct entry", () => {
        const result = appendInputFiles([file("a", "/one.glb")], [file("a", "/one.glb", "db2")]);
        expect(result.added).toBe(1);
    });
});

describe("summarizeInputFiles", () => {
    it("counts complete entries per asset in first-seen order and ignores drafts", () => {
        const summary = summarizeInputFiles([
            file("b", "/b1.glb"),
            file("a", "/a1.glb"),
            file("a", ""),
            file("", ""),
            file("a", "/a2.glb"),
            file("b", "/"),
        ]);
        expect(summary.total).toBe(4);
        expect(summary.assetCount).toBe(2);
        expect(summary.perAsset).toEqual([
            { databaseId: "db1", assetId: "b", count: 2 },
            { databaseId: "db1", assetId: "a", count: 2 },
        ]);
        expect(describeInputFiles(summary)).toBe("4 files across 2 assets");
    });

    it("uses the singular forms", () => {
        expect(describeInputFiles(summarizeInputFiles([file("a", "/x")]))).toBe(
            "1 file across 1 asset"
        );
    });
});

describe("isCompleteInputFile", () => {
    it("needs an asset and a file selection", () => {
        expect(isCompleteInputFile(file("a", "/x"))).toBe(true);
        expect(isCompleteInputFile(file("a", "/"))).toBe(true);
        expect(isCompleteInputFile(file("a", ""))).toBe(false);
        expect(isCompleteInputFile(file("", "/x"))).toBe(false);
    });
});

describe("inputFileCompatibility", () => {
    it("passes an admitted file and reads the filters against the key", () => {
        expect(inputFileCompatibility(file("a", "/scan.e57"), open)).toEqual({ ok: true });
        expect(
            inputFileCompatibility(file("a", "/notes.txt"), { ...open, allow: ["*.e57"] }).ok
        ).toBe(false);
        expect(
            inputFileCompatibility(file("a", "/scan.e57"), { ...open, exclude: ["*.e57"] }).ok
        ).toBe(false);
    });

    it("gates the container selections on the resolved scope", () => {
        expect(inputFileCompatibility(file("a", "/"), open).ok).toBe(true);
        expect(
            inputFileCompatibility(file("a", "/"), { ...open, wholeAssetAllowed: false })
        ).toEqual({ ok: false, reason: "Whole-asset selection is not allowed by this workflow." });
        expect(inputFileCompatibility(file("a", "/docs/"), open).ok).toBe(true);
        expect(
            inputFileCompatibility(file("a", "/docs/"), { ...open, folderAllowed: false }).ok
        ).toBe(false);
    });

    it("does not let an extension-only allow list reject a container", () => {
        // Mirrors applyInputFileFilters: an extension pattern cannot describe a folder, so the
        // scope gate alone decides.
        expect(inputFileCompatibility(file("a", "/"), { ...open, allow: ["*.glb"] }).ok).toBe(true);
    });
});

describe("rowsByAsset and filterRows", () => {
    const files = [
        file("b", "/b1"),
        file("a", "/a1"),
        file("b", "/b2"),
        file("a", ""),
        file("c", "/c1", "db2"),
    ];

    it("groups by asset in first-seen order, keeps the index and skips the excluded rows", () => {
        const rows = rowsByAsset(files, new Set([3]));
        expect(rows.map((r) => `${r.file.assetId}${r.file.relativeFileKey}#${r.index}`)).toEqual([
            "b/b1#0",
            "b/b2#2",
            "a/a1#1",
            "c/c1#4",
        ]);
    });

    it("filters on the key or the asset, case-insensitively", () => {
        const rows = rowsByAsset(files);
        // "/B1" rather than "B1": the database id "db1" itself contains "b1".
        expect(filterRows(rows, "/B1").map((r) => r.index)).toEqual([0]);
        expect(filterRows(rows, "db2").map((r) => r.index)).toEqual([4]);
        expect(filterRows(rows, "")).toBe(rows);
    });
});

describe("parsePastedKeys", () => {
    it("normalizes one key per line, keeps folders, drops blanks and duplicates", () => {
        expect(
            parsePastedKeys("scans/1.e57\n /scans/2.e57 \r\n\n//scans/1.e57\ndocs/\n/\n")
        ).toEqual(["/scans/1.e57", "/scans/2.e57", "/docs/", "/"]);
    });

    it("yields nothing for empty input", () => {
        expect(parsePastedKeys("")).toEqual([]);
        expect(parsePastedKeys("\n\n")).toEqual([]);
    });
});

describe("describeKey", () => {
    it("spells out the container forms", () => {
        expect(describeKey("/")).toBe("Whole asset (all files)");
        expect(describeKey("/docs/")).toBe("/docs/ (folder)");
        expect(describeKey("/a.glb")).toBe("/a.glb");
    });
});
