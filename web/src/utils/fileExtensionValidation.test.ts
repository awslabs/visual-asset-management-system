/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Client-side extension validation for the upload pages.
 *
 * The load-bearing case is a `.previewFile.` companion on a database with NO
 * `restrictFileUploadsToExtensions`, which is the majority configuration: the
 * companion is exempt from the database restriction list, but the API rejects the
 * whole `POST /uploads` request when its extension is not an allowed image type, so
 * the page has to report it before the upload starts.
 *
 * The extension of a companion is everything AFTER the `.previewFile.` marker rather
 * than the text after the last dot -- `x.previewFile.p.png` is `.p.png`, not `.png`.
 * A last-dot reading would accept a name the API refuses.
 */

import { FileUploadTableItem } from "../pages/AssetUpload/FileUploadTable";
import { previewFileFormats } from "../common/constants/fileFormats";
import Synonyms from "../synonyms";
import {
    validateFiles,
    formatValidationErrors,
    getPreviewFileExtension,
    isPreviewExtensionAllowed,
} from "./fileExtensionValidation";

const item = (name: string) => ({ name } as FileUploadTableItem);

describe("getPreviewFileExtension", () => {
    it("takes everything after the .previewFile. marker, not the last dot", () => {
        expect(getPreviewFileExtension("model.gltf.previewFile.png")).toBe(".png");
        expect(getPreviewFileExtension("model.gltf.previewFile.p.png")).toBe(".p.png");
        expect(getPreviewFileExtension("model.gltf.previewFile.png.bak")).toBe(".png.bak");
    });

    it("lowercases the extension", () => {
        expect(getPreviewFileExtension("model.gltf.previewFile.PNG")).toBe(".png");
    });

    it("falls back to the last-dot extension for a plain asset preview", () => {
        expect(getPreviewFileExtension("logo.PNG")).toBe(".png");
        expect(getPreviewFileExtension("notes.pdf")).toBe(".pdf");
    });
});

describe("isPreviewExtensionAllowed", () => {
    it("accepts every allowed preview format as a companion and as a plain preview", () => {
        previewFileFormats.forEach((ext) => {
            expect(isPreviewExtensionAllowed(`model.gltf.previewFile${ext}`)).toBe(true);
            expect(isPreviewExtensionAllowed(`logo${ext}`)).toBe(true);
        });
    });

    it("rejects a non-image companion and a non-image asset preview", () => {
        expect(isPreviewExtensionAllowed("model.gltf.previewFile.pdf")).toBe(false);
        expect(isPreviewExtensionAllowed("notes.pdf")).toBe(false);
    });

    it("rejects a companion whose marker extension only ends in an allowed one", () => {
        expect(isPreviewExtensionAllowed("model.gltf.previewFile.p.png")).toBe(false);
    });
});

describe("validateFiles preview companions", () => {
    it("reports a bad companion when the database has no restriction list", () => {
        const result = validateFiles(
            [item("smoke.glb"), item("smoke.glb.previewFile.pdf")],
            undefined
        );

        expect(result.isValid).toBe(false);
        expect(result.allowedExtensions).toBeNull();
        expect(result.invalidFiles).toHaveLength(1);
        expect(result.invalidFiles[0]).toMatchObject({
            fileName: "smoke.glb.previewFile.pdf",
            isPreviewFile: true,
            extension: ".pdf",
        });
        expect(result.invalidFiles[0].errorMessage).toContain(previewFileFormats.join(", "));
    });

    it("reports a bad companion when the database does restrict extensions", () => {
        const result = validateFiles(
            [item("smoke.glb"), item("smoke.glb.previewFile.pdf")],
            ".glb,.gltf"
        );

        expect(result.isValid).toBe(false);
        expect(result.invalidFiles.map((f) => f.fileName)).toEqual(["smoke.glb.previewFile.pdf"]);
    });

    // Positive control: without this, a validator that rejected every companion would
    // pass the two cases above
    it("accepts a legitimate companion with and without a restriction list", () => {
        expect(
            validateFiles([item("smoke.glb"), item("smoke.glb.previewFile.png")], undefined).isValid
        ).toBe(true);
        expect(
            validateFiles([item("smoke.glb"), item("smoke.glb.previewFile.png")], ".glb").isValid
        ).toBe(true);
    });

    it("keeps a companion exempt from the database restriction list", () => {
        // .png is not in the allowed list, but the companion is still accepted
        const result = validateFiles([item("smoke.glb.previewFile.png")], ".glb,.gltf");
        expect(result.isValid).toBe(true);
    });
});

describe("validateFiles database restrictions", () => {
    it("still rejects a plain file outside the restriction list", () => {
        const result = validateFiles([item("notes.pdf"), item("smoke.glb")], ".glb,.gltf");

        expect(result.isValid).toBe(false);
        expect(result.invalidFiles.map((f) => f.fileName)).toEqual(["notes.pdf"]);
        expect(result.invalidFiles[0].isPreviewFile).toBe(false);
    });

    it("accepts any plain file when there is no restriction list", () => {
        expect(validateFiles([item("notes.pdf"), item("smoke.glb")], undefined).isValid).toBe(true);
        expect(validateFiles([item("notes.pdf")], ".all").isValid).toBe(true);
        expect(validateFiles([item("notes.pdf")], "   ").isValid).toBe(true);
    });

    it("treats an empty selection as valid", () => {
        const result = validateFiles([], ".glb");
        expect(result.isValid).toBe(true);
        expect(result.invalidFiles).toEqual([]);
    });
});

describe("formatValidationErrors", () => {
    it("uses the per-file message and omits the allowed list when nothing is restricted", () => {
        const message = formatValidationErrors(
            validateFiles([item("smoke.glb.previewFile.pdf")], undefined)
        );

        expect(message).toContain("smoke.glb.previewFile.pdf");
        expect(message).toContain("Preview files must have one of the allowed extensions");
        expect(message).not.toContain(`Allowed extensions for this ${Synonyms.database}`);
    });

    it("names the allowed list when the database restricts extensions", () => {
        const message = formatValidationErrors(validateFiles([item("notes.pdf")], ".glb,.gltf"));

        expect(message).toContain("Extension .pdf is not allowed");
        expect(message).toContain(`Allowed extensions for this ${Synonyms.database}: .glb, .gltf`);
    });

    it("returns an empty string for a valid selection", () => {
        expect(formatValidationErrors(validateFiles([item("smoke.glb")], ".glb"))).toBe("");
    });
});
