/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    searchRowToFileInfo,
    isViewableExtension,
    reconcileViewerSelection,
    clearViewableExtensionCache,
} from "./searchRowToFileInfo";

// Mock the registry so this test is pure and does not load real plugins.
jest.mock("../../../visualizerPlugin/core/PluginRegistry", () => {
    const getCompatibleViewers = jest.fn((exts: string[]) =>
        exts.includes(".glb") || exts.includes(".png") ? [{ config: { id: "x" } }] : []
    );
    return {
        PluginRegistry: {
            // isInitialized mirrors the real registry: isViewableExtension only memoizes once the
            // registry reports itself ready, because before that getCompatibleViewers returns an
            // empty list and caching that would hide the icon permanently.
            getInstance: () => ({ getCompatibleViewers, isInitialized: () => true }),
        },
    };
});

describe("searchRowToFileInfo", () => {
    it("maps a file-mode row to a FileInfo with per-file asset context", () => {
        const row = {
            str_key: "xasset1/test/body.glb",
            str_fileext: ".glb",
            str_assetid: "xasset1",
            str_databaseid: "db1",
            num_filesize: 2400,
            date_lastmodified: "2026-06-10",
            bool_archived: false,
            str_primarytype: "model",
        };
        const fi = searchRowToFileInfo(row);
        expect(fi).toEqual({
            filename: "body.glb",
            key: "xasset1/test/body.glb",
            isDirectory: false,
            assetId: "xasset1",
            databaseId: "db1",
            size: 2400,
            dateCreatedCurrentVersion: "2026-06-10",
            isArchived: false,
            primaryType: "model",
        });
    });

    it("derives filename from the last path segment", () => {
        expect(
            searchRowToFileInfo({ str_key: "a/b/c.png", str_assetid: "x", str_databaseid: "d" })
                .filename
        ).toBe("c.png");
    });
});

describe("isViewableExtension", () => {
    it("returns true when a plugin supports the extension", () => {
        expect(isViewableExtension(".glb")).toBe(true);
        expect(isViewableExtension(".PNG")).toBe(true); // case-insensitive
    });
    it("returns false for unknown or missing extension", () => {
        expect(isViewableExtension(".docx")).toBe(false);
        expect(isViewableExtension(undefined)).toBe(false);
        expect(isViewableExtension("")).toBe(false);
    });
});

describe("reconcileViewerSelection file identity", () => {
    const row = (key: string, assetId: string, databaseId = "d1") => ({
        str_key: key,
        str_fileext: ".glb",
        str_assetid: assetId,
        str_databaseid: databaseId,
    });
    const idsOf = (sel: any[]) => sel.map((f) => `${f.databaseId}/${f.assetId}/${f.key}`);

    // The same asset-relative path exists in many assets. Keying the selection on the path alone
    // made the second file look like one that was already selected, so checking it did nothing.
    it("treats the same key in different assets as two different files", () => {
        const rows = [row("model.glb", "assetA"), row("model.glb", "assetB")];

        const result = reconcileViewerSelection([], rows, rows);

        expect(result).toHaveLength(2);
        expect(idsOf(result)).toEqual(["d1/assetA/model.glb", "d1/assetB/model.glb"]);
    });

    it("treats the same key and asset in different databases as two different files", () => {
        const rows = [row("model.glb", "assetA", "dbOne"), row("model.glb", "assetA", "dbTwo")];

        expect(reconcileViewerSelection([], rows, rows)).toHaveLength(2);
    });

    it("carries over a same-key pick from another asset that is not on screen", () => {
        const previous = [searchRowToFileInfo(row("model.glb", "assetA"))];
        const onScreen = [row("model.glb", "assetB")];

        // assetB is checked now; assetA is off-screen and must survive rather than being taken for
        // the same file and dropped.
        const result = reconcileViewerSelection(previous, onScreen, onScreen);

        expect(idsOf(result).sort()).toEqual(["d1/assetA/model.glb", "d1/assetB/model.glb"]);
    });

    it("still removes a file when its own row is unchecked", () => {
        const previous = [searchRowToFileInfo(row("model.glb", "assetA"))];
        const onScreen = [row("model.glb", "assetA")];

        expect(reconcileViewerSelection(previous, onScreen, [])).toEqual([]);
    });
});

describe("reconcileViewerSelection", () => {
    // Rows the mocked registry treats as viewable: .glb and .png
    const row = (key: string, ext = ".glb", assetId = "a1", databaseId = "d1") => ({
        str_key: key,
        str_fileext: ext,
        str_assetid: assetId,
        str_databaseid: databaseId,
    });
    const keysOf = (sel: ReturnType<typeof reconcileViewerSelection>) => sel.map((f) => f.key);

    it("keeps picks from an earlier search when a row in a NEW result set is checked", () => {
        // The bug: a new search clears the checkboxes but keeps the selection, so mirroring the
        // checkboxes alone dropped everything picked before.
        const previous = [searchRowToFileInfo(row("old/first.glb"))];
        const newResults = [row("new/second.glb"), row("new/third.glb")];
        const checked = [row("new/second.glb")];

        expect(keysOf(reconcileViewerSelection(previous, newResults, checked))).toEqual([
            "old/first.glb",
            "new/second.glb",
        ]);
    });

    it("removes a visible row when it is unchecked", () => {
        const rows = [row("a.glb"), row("b.glb")];
        const previous = [searchRowToFileInfo(row("a.glb")), searchRowToFileInfo(row("b.glb"))];

        // b.glb unchecked -> only a.glb remains, because both are on screen.
        expect(keysOf(reconcileViewerSelection(previous, rows, [row("a.glb")]))).toEqual(["a.glb"]);
    });

    it("clearing every checkbox drops only the on-screen rows, not earlier picks", () => {
        const previous = [
            searchRowToFileInfo(row("old/keep.glb")),
            searchRowToFileInfo(row("a.glb")),
        ];
        expect(keysOf(reconcileViewerSelection(previous, [row("a.glb")], []))).toEqual([
            "old/keep.glb",
        ]);
    });

    it("does not duplicate a file that is both previously selected and checked again", () => {
        const previous = [searchRowToFileInfo(row("a.glb"))];
        expect(keysOf(reconcileViewerSelection(previous, [row("a.glb")], [row("a.glb")]))).toEqual([
            "a.glb",
        ]);
    });

    it("ignores checked rows whose extension no viewer can render", () => {
        expect(
            keysOf(reconcileViewerSelection([], [row("x.txt", ".txt")], [row("x.txt", ".txt")]))
        ).toEqual([]);
    });

    it("tolerates an empty previous selection", () => {
        expect(keysOf(reconcileViewerSelection([], [row("a.glb")], [row("a.glb")]))).toEqual([
            "a.glb",
        ]);
    });
});

describe("isViewableExtension memoization", () => {
    beforeEach(() => clearViewableExtensionCache());

    it("asks the registry once per extension, not once per row", () => {
        // This runs for every rendered table cell, and getCompatibleViewers walks, filters and
        // SORTS every registered viewer, so a page of results repeated identical work per row.
        const { PluginRegistry } = require("../../../visualizerPlugin/core/PluginRegistry");
        const spy = PluginRegistry.getInstance().getCompatibleViewers as jest.Mock;
        spy.mockClear();

        for (let i = 0; i < 25; i++) isViewableExtension("glb");

        expect(spy).toHaveBeenCalledTimes(1);
    });

    it("still answers correctly from the cache", () => {
        expect(isViewableExtension("glb")).toBe(true);
        expect(isViewableExtension("glb")).toBe(true);
        expect(isViewableExtension("zzz")).toBe(false);
        expect(isViewableExtension("zzz")).toBe(false);
    });

    it("caches per extension rather than sharing one answer", () => {
        expect(isViewableExtension("glb")).toBe(true);
        expect(isViewableExtension("zzz")).toBe(false);
        expect(isViewableExtension("png")).toBe(true);
    });
});
