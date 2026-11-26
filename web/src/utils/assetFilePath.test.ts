/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assetFilePath } from "./assetFilePath";

describe("assetFilePath", () => {
    it.each([
        ["cloud.las", "/cloud.las"],
        ["/cloud.las", "/cloud.las"],
        ["//cloud.las", "//cloud.las"],
        ["/sub/", "/sub/"],
        ["", "/"],
        [undefined, "/"],
    ])("sends %p as %p", (input, expected) => {
        expect(assetFilePath(input as any)).toBe(expected);
    });
});
