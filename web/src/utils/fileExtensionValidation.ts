/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileUploadTableItem } from "../pages/AssetUpload/FileUploadTable";
import { previewFileFormats, PREVIEW_FILE_PATTERN } from "../common/constants/fileFormats";
import Synonyms from "../synonyms";

/**
 * Parse the restrictFileUploadsToExtensions string into an array of allowed extensions
 * Returns null if no restrictions (empty, ".all", or undefined)
 */
export function parseAllowedExtensions(restrictFileUploadsToExtensions?: string): string[] | null {
    // No restrictions if empty or undefined
    if (!restrictFileUploadsToExtensions || restrictFileUploadsToExtensions.trim() === "") {
        return null;
    }

    // Parse comma-separated extensions and normalize to lowercase
    const extensions = restrictFileUploadsToExtensions
        .split(",")
        .map((ext) => ext.trim().toLowerCase())
        .filter((ext) => ext.length > 0);

    // No restrictions if ".all" is listed (the API ignores the rest of the list then)
    return extensions.includes(".all") ? null : extensions;
}

/**
 * Whether a file name ends with an allowed extension (lower-case, with its leading dot).
 *
 * An extension may have several parts (`.tar.gz`), so the name is matched by its ending, not by
 * its last suffix. The part of the name before the extension must hold a character other than a
 * dot: `.pdf` and `..pdf` have no extension. An entry that does not start with a dot or ends with
 * one matches nothing. Mirrors file_name_has_extension in backend/backend/common/assetUploads.py.
 */
export function fileNameHasExtension(fileName: string, extension: string): boolean {
    const name = (fileName.split("/").pop() ?? "").toLowerCase();
    if (!extension.startsWith(".") || extension.endsWith(".") || !name.endsWith(extension)) {
        return false;
    }
    return name.slice(0, name.length - extension.length).replace(/\./g, "") !== "";
}

/** Whether a file name ends with one of the allowed extensions. */
function hasAllowedExtension(fileName: string, allowedExtensions: string[]): boolean {
    return allowedExtensions.some((ext) => fileNameHasExtension(fileName, ext));
}

/**
 * Get the file extension from a filename (including the dot)
 */
export function getFileExtension(fileName: string): string {
    const lastDotIndex = fileName.lastIndexOf(".");
    if (lastDotIndex === -1 || lastDotIndex === fileName.length - 1) {
        return "";
    }
    return fileName.substring(lastDotIndex).toLowerCase();
}

/**
 * Check if a file is a preview file (contains .previewFile. in the name)
 */
export function isPreviewFile(fileName: string): boolean {
    return fileName.includes(PREVIEW_FILE_PATTERN);
}

/**
 * Get the extension a preview file is validated against.
 *
 * For a name containing .previewFile. the extension is everything AFTER the marker,
 * not the last dot: `model.gltf.previewFile.p.png` has the extension `.p.png`. This
 * mirrors validate_preview_file_extension in the backend, which is the check that
 * rejects the upload.
 */
export function getPreviewFileExtension(fileName: string): string {
    if (fileName.includes(PREVIEW_FILE_PATTERN)) {
        return "." + fileName.split(PREVIEW_FILE_PATTERN)[1].toLowerCase();
    }
    return getFileExtension(fileName);
}

/**
 * Check if a preview file carries one of the allowed image extensions
 */
export function isPreviewExtensionAllowed(fileName: string): boolean {
    return previewFileFormats.includes(getPreviewFileExtension(fileName));
}

/**
 * Check if a file's extension is allowed based on the restrictions
 */
export function isFileAllowed(fileName: string, allowedExtensions: string[] | null): boolean {
    // No restrictions means all files are allowed
    if (allowedExtensions === null) {
        return true;
    }

    // Preview files are always allowed
    if (isPreviewFile(fileName)) {
        return true;
    }

    return hasAllowedExtension(fileName, allowedExtensions);
}

/**
 * Validation result for a single file
 */
export interface FileValidationResult {
    fileName: string;
    isValid: boolean;
    isPreviewFile: boolean;
    extension: string;
    errorMessage?: string;
}

/**
 * Overall validation result for all files
 */
export interface ValidationResult {
    isValid: boolean;
    invalidFiles: FileValidationResult[];
    allowedExtensions: string[] | null;
}

/**
 * The extension rule for one file name: a preview file (containing .previewFile.) is exempt from
 * the database's list but must carry a preview image extension; any other file must carry one of
 * the listed extensions when the database restricts them. Returns null when the file is allowed.
 * Shared by validateFiles and the upload preview's per-row errors.
 */
export function extensionViolation(
    fileName: string,
    allowedExtensions: string[] | null
): { isPreviewFile: boolean; extension: string } | null {
    if (isPreviewFile(fileName)) {
        return isPreviewExtensionAllowed(fileName)
            ? null
            : { isPreviewFile: true, extension: getPreviewFileExtension(fileName) };
    }
    if (allowedExtensions === null) return null;
    return hasAllowedExtension(fileName, allowedExtensions)
        ? null
        : { isPreviewFile: false, extension: getFileExtension(fileName) };
}

/**
 * Validate a list of files against the extension restrictions
 */
export function validateFiles(
    files: FileUploadTableItem[],
    restrictFileUploadsToExtensions?: string
): ValidationResult {
    const allowedExtensions = parseAllowedExtensions(restrictFileUploadsToExtensions);
    const invalidFiles: FileValidationResult[] = [];

    // Check each file
    for (const file of files) {
        const fileName = file.name;
        const violation = extensionViolation(fileName, allowedExtensions);
        if (!violation) continue;

        if (violation.isPreviewFile) {
            invalidFiles.push({
                fileName,
                isValid: false,
                isPreviewFile: true,
                extension: violation.extension,
                errorMessage: `Preview files must have one of the allowed extensions: ${previewFileFormats.join(
                    ", "
                )}`,
            });
        } else {
            invalidFiles.push({
                fileName,
                isValid: false,
                isPreviewFile: false,
                extension: violation.extension,
                errorMessage: `Extension ${
                    violation.extension
                } is not allowed. Allowed extensions: ${(allowedExtensions || []).join(", ")}`,
            });
        }
    }

    return {
        isValid: invalidFiles.length === 0,
        invalidFiles,
        allowedExtensions,
    };
}

/**
 * Format validation errors for display
 */
export function formatValidationErrors(validationResult: ValidationResult): string {
    if (validationResult.isValid) {
        return "";
    }

    const { invalidFiles, allowedExtensions } = validationResult;

    let message = `The following files cannot be uploaded:\n\n`;

    invalidFiles.forEach((file) => {
        message += `• ${file.fileName} - ${
            file.errorMessage || `Extension ${file.extension} not allowed`
        }\n`;
    });

    if (allowedExtensions !== null) {
        message += `\nAllowed extensions for this ${Synonyms.database}: ${allowedExtensions.join(
            ", "
        )}\n`;
    }
    message += `\nNote: Preview files (containing ${PREVIEW_FILE_PATTERN} in the filename) are exempt from the ${
        Synonyms.database
    } restriction list, but must still be one of ${previewFileFormats.join(", ")}.`;

    return message;
}
