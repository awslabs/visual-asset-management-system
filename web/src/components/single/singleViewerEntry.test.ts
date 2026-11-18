/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { buildSingleViewerEntry, previewIsOfLatestVersionOnly } from "./singleViewerEntry";

const file = {
    key: "asset1/model.glb",
    versionId: "source-v1",
    previewFile: "asset1/model.glb.previewFile.gif",
};

describe("buildSingleViewerEntry", () => {
    it("keeps the source key and version for the file tab", () => {
        expect(buildSingleViewerEntry(file, "file")).toMatchObject({
            key: file.key,
            versionId: "source-v1",
        });
    });

    it("uses the preview key and drops the source file's version for the preview tab", () => {
        const entry = buildSingleViewerEntry(file, "preview");
        expect(entry.key).toBe(file.previewFile);
        expect(entry.versionId).toBeUndefined();
    });

    it("keeps the source version when the preview tab has no preview file to show", () => {
        const entry = buildSingleViewerEntry({ ...file, previewFile: undefined }, "preview");
        expect(entry).toMatchObject({ key: file.key, versionId: "source-v1" });
    });

    it("drops the file version when viewing through an asset version", () => {
        expect(buildSingleViewerEntry(file, "file", "3").versionId).toBeUndefined();
        const entry = buildSingleViewerEntry(file, "preview", "3");
        expect(entry).toMatchObject({ key: file.previewFile, versionId: undefined });
    });
});

describe("previewIsOfLatestVersionOnly", () => {
    it("is true on the preview tab of a file version that is not the latest", () => {
        expect(previewIsOfLatestVersionOnly(file, "preview", undefined, "source-v2")).toBe(true);
    });

    it("is false for the latest file version", () => {
        expect(previewIsOfLatestVersionOnly(file, "preview", undefined, "source-v1")).toBe(false);
    });

    it("is false on the file tab, through an asset version, without a preview file", () => {
        expect(previewIsOfLatestVersionOnly(file, "file", undefined, "source-v2")).toBe(false);
        expect(previewIsOfLatestVersionOnly(file, "preview", "3", "source-v2")).toBe(false);
        const noPreview = { ...file, previewFile: undefined };
        expect(previewIsOfLatestVersionOnly(noPreview, "preview", undefined, "source-v2")).toBe(
            false
        );
    });

    it("is false while the latest version is unknown", () => {
        expect(previewIsOfLatestVersionOnly(file, "preview", undefined, undefined)).toBe(false);
        expect(previewIsOfLatestVersionOnly({ ...file, versionId: undefined }, "preview")).toBe(
            false
        );
    });
});
