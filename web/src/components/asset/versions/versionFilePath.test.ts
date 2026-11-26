/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storedVersionEntryKey, versionFilePath } from "./versionFilePath";

describe("versionFilePath", () => {
    it.each([
        ["A1/cloud.las", "/A1/cloud.las"],
        ["/A1/cloud.las", "/A1/cloud.las"],
        // A stored "//x" is the key with an empty segment after the location (A1//x), not "/x" (#1977)
        ["//cloud.las", "//cloud.las"],
        ["//sub/x.glb", "//sub/x.glb"],
        ["model.glb", "/model.glb"],
        ["", "/"],
        [undefined, "/"],
    ])("sends the snapshot path %p as the path in the asset %p", (relativeKey, expected) => {
        expect(versionFilePath(relativeKey)).toBe(expected);
    });
});

describe("storedVersionEntryKey", () => {
    it.each([
        // The file list's path and a snapshot's key of one file match
        ["/cloud.las", "cloud.las"],
        ["cloud.las", "cloud.las"],
        // An empty-segment key stays apart from the root file (#1977)
        ["//cloud.las", "//cloud.las"],
        ["/sub/x.glb", "sub/x.glb"],
        ["", ""],
        [undefined, ""],
    ])("matches %p as %p", (path, expected) => {
        expect(storedVersionEntryKey(path)).toBe(expected);
    });

    it("round-trips through versionFilePath to the listed path", () => {
        ["/cloud.las", "//cloud.las", "/sub//x.glb"].forEach((listed) =>
            expect(versionFilePath(storedVersionEntryKey(listed))).toBe(listed)
        );
    });
});
