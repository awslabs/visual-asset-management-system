/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { versionFilePath } from "./versionFilePath";

describe("versionFilePath", () => {
    it.each([
        ["A1/cloud.las", "/A1/cloud.las"],
        ["/A1/cloud.las", "/A1/cloud.las"],
        ["//sub/x.glb", "/sub/x.glb"],
        ["model.glb", "/model.glb"],
        ["", "/"],
        [undefined, "/"],
    ])("sends the snapshot path %p as the path in the asset %p", (relativeKey, expected) => {
        expect(versionFilePath(relativeKey)).toBe(expected);
    });
});
