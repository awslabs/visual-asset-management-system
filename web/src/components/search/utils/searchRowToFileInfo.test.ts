/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { searchRowToFileInfo, isViewableExtension } from "./searchRowToFileInfo";

// Mock the registry so this test is pure and does not load real plugins.
jest.mock("../../../visualizerPlugin/core/PluginRegistry", () => {
    const getCompatibleViewers = jest.fn((exts: string[]) =>
        exts.includes(".glb") || exts.includes(".png") ? [{ config: { id: "x" } }] : []
    );
    return {
        PluginRegistry: {
            getInstance: () => ({ getCompatibleViewers }),
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
        expect(searchRowToFileInfo({ str_key: "a/b/c.png", str_assetid: "x", str_databaseid: "d" }).filename).toBe(
            "c.png"
        );
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