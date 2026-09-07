/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Synonyms from "../../../synonyms";
import { getChangeSourceLabel } from "./changeSourceLabels";

/**
 * Every value the backend can stamp into vams-changesource, mirroring
 * VAMS_CHANGE_SOURCE_VALUES in backend/backend/common/s3MetadataKeys.py. A value missing from
 * the label map renders its raw camelCase token in the file manager's provenance column, so the
 * list is asserted whole rather than one value at a time.
 */
const BACKEND_CHANGE_SOURCE_VALUES = [
    "direct",
    "upload",
    "workflowExecution",
    "fileCopy",
    "fileMove",
    "fileRename",
    "fileArchive",
    "fileUnarchive",
    "assetArchive",
    "assetUnarchive",
    "fileRevert",
];

describe("getChangeSourceLabel", () => {
    it("returns a display label for every value the backend emits, never the raw token", () => {
        const raw = BACKEND_CHANGE_SOURCE_VALUES.filter(
            (value) => getChangeSourceLabel(value) === value
        );
        expect(raw).toEqual([]);
    });

    it("labels the whole-asset archive sources", () => {
        expect(getChangeSourceLabel("assetArchive")).toBe(`${Synonyms.Asset} Archive`);
        expect(getChangeSourceLabel("assetUnarchive")).toBe(`${Synonyms.Asset} Unarchive`);
    });

    it("keeps the raw value for a genuinely unknown source, and empty for none", () => {
        // The fallback is deliberate: a value added by a newer backend stays visible rather
        // than rendering blank.
        expect(getChangeSourceLabel("somethingNewer")).toBe("somethingNewer");
        expect(getChangeSourceLabel(undefined)).toBe("");
        expect(getChangeSourceLabel(null)).toBe("");
    });
});
