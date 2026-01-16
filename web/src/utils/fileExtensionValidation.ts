/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileUploadTableItem } from "../pages/AssetUpload/FileUploadTable";

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
    return fileName.includes(".previewFile.");
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

    // If no restrictions, all files are valid
    if (allowedExtensions === null) {
        return {
            isValid: true,
            invalidFiles: [],
            allowedExtensions: null,
        };
    }

    // Check each file
    for (const file of files) {
        const fileName = file.name;
        const fileExt = getFileExtension(fileName);
        const isPreview = isPreviewFile(fileName);

        // Skip preview files
        if (isPreview) {
            continue;
        }

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

    let message = "The following files have extensions that are not allowed for this database:\n\n";

    invalidFiles.forEach((file) => {
        message += `• ${file.fileName} - Extension ${file.extension} not allowed\n`;
    });

    message += `\nAllowed extensions: ${allowedExtensions?.join(", ") || "none"}`;
    message +=
        "\n\nNote: Preview files (containing .previewFile. in the filename) are exempt from these restrictions.";

    return message;
}
