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

    // No restrictions if ".all"
    if (restrictFileUploadsToExtensions.trim().toLowerCase() === ".all") {
        return null;
    }

    // Parse comma-separated extensions and normalize to lowercase
    return restrictFileUploadsToExtensions
        .split(",")
        .map((ext) => ext.trim().toLowerCase())
        .filter((ext) => ext.length > 0);
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

    // Get the file extension
    const fileExt = getFileExtension(fileName);

    // Check if the extension is in the allowed list
    return allowedExtensions.includes(fileExt);
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

        // A preview file is exempt from the database restriction list but must still
        // carry an allowed image extension, so it is checked whether or not the
        // database restricts extensions
        if (isPreviewFile(fileName)) {
            if (!isPreviewExtensionAllowed(fileName)) {
                invalidFiles.push({
                    fileName,
                    isValid: false,
                    isPreviewFile: true,
                    extension: getPreviewFileExtension(fileName),
                    errorMessage: `Preview files must have one of the allowed extensions: ${previewFileFormats.join(
                        ", "
                    )}`,
                });
            }
            continue;
        }

        // No restrictions means any non-preview file is allowed
        if (allowedExtensions === null) {
            continue;
        }

        const fileExt = getFileExtension(fileName);

        // Check if file is allowed
        if (!allowedExtensions.includes(fileExt)) {
            invalidFiles.push({
                fileName,
                isValid: false,
                isPreviewFile: false,
                extension: fileExt,
                errorMessage: `Extension ${fileExt} is not allowed. Allowed extensions: ${allowedExtensions.join(
                    ", "
                )}`,
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
