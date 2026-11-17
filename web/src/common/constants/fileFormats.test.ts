/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    PREVIEW_FILE_SIZE_LIMIT_BYTES,
    isSelfPreviewable,
    previewFileFormats,
} from "./fileFormats";

describe("isSelfPreviewable", () => {
    it("uses the 5 MiB limit, strictly under", () => {
        expect(PREVIEW_FILE_SIZE_LIMIT_BYTES).toBe(5242880);
        expect(isSelfPreviewable("a.png", 4999999)).toBe(true);
        expect(isSelfPreviewable("a.png", 5242879)).toBe(true);
        expect(isSelfPreviewable("a.png", 5242880)).toBe(false);
        expect(isSelfPreviewable("a.png", 5242881)).toBe(false);
    });

    it("accepts a zero-byte image and any case of the extension", () => {
        expect(isSelfPreviewable("A.PNG", 0)).toBe(true);
        expect(isSelfPreviewable("photo.JpEg", 100)).toBe(true);
    });

    it("includes webp and bmp but not tga, tif or tiff", () => {
        expect(previewFileFormats).toEqual(
            expect.arrayContaining([".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".bmp"])
        );
        expect(isSelfPreviewable("a.webp", 10)).toBe(true);
        expect(isSelfPreviewable("a.bmp", 10)).toBe(true);
        for (const name of ["a.tga", "a.tif", "a.tiff"]) {
            expect(isSelfPreviewable(name, 10)).toBe(false);
            expect(previewFileFormats).not.toContain(name.substring(name.lastIndexOf(".")));
        }
    });

    it("is false for an unknown size, a file with no extension and a non-image", () => {
        expect(isSelfPreviewable("a.png", undefined)).toBe(false);
        expect(isSelfPreviewable("a.png", null)).toBe(false);
        expect(isSelfPreviewable("a.png", Number.NaN)).toBe(false);
        expect(isSelfPreviewable("png", 10)).toBe(false);
        expect(isSelfPreviewable("model.glb", 10)).toBe(false);
    });

    it("reads the extension after the last dot", () => {
        expect(isSelfPreviewable("scan.tif.png", 10)).toBe(true);
        expect(isSelfPreviewable("scan.png.tif", 10)).toBe(false);
    });
});
