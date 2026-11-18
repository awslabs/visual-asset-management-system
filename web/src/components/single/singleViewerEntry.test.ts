/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { buildSingleViewerEntry } from "./singleViewerEntry";

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
