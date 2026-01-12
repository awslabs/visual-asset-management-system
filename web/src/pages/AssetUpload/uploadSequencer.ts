/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    MAX_FILES_PER_REQUEST,
    MAX_TOTAL_PARTS_PER_REQUEST,
    MAX_PARTS_PER_FILE,
    MAX_SEQUENCE_SIZE,
    MAX_PART_SIZE,
    MAX_PART_SIZE_LARGE,
    MAX_FILE_SIZE_SMALL_CHUNKS,
} from "../../constants/uploadLimits";

/**
 * Information about a file to be uploaded
 */
export interface FileInfo {
    index: number;
    name: string;
    size: number;
    relativePath: string;
    handle: any; // FileSystemFileHandle or compatible
    isPreviewFile: boolean;
    isAssetPreview?: boolean; // True for Asset Overall Preview file (index 99999)
}

/**
 * Information about a file part
 */
export interface PartInfo {
    partNumber: number;
    startByte: number;
    endByte: number;
    size: number;
}

/**
 * A sequence of files to be uploaded together in one init/complete cycle
 */
export class UploadSequence {
    public files: FileInfo[];
    public sequenceId: number;
    public totalSize: number;
    public totalParts: number;
    public fileParts: Map<number, PartInfo[]>; // fileIndex -> parts

    constructor(files: FileInfo[], sequenceId: number) {
        this.files = files;
        this.sequenceId = sequenceId;
        this.totalSize = files.reduce((sum, f) => sum + f.size, 0);
        this.totalParts = 0;
        this.fileParts = new Map();
        this.calculateParts();
    }

    private calculateParts(): void {
        this.totalParts = 0;
        this.fileParts.clear();

        for (const file of this.files) {
            const parts = calculateFileParts(file.size);
            this.fileParts.set(file.index, parts);
            this.totalParts += parts.length;
        }
    }
}

/**
 * Calculate parts for a single file based on size
 */
export function calculateFileParts(fileSize: number): PartInfo[] {
    if (fileSize === 0) {
        // Zero-byte files have no parts
        return [];
    }

    // Determine chunk size based on file size
    const chunkSize = fileSize > MAX_FILE_SIZE_SMALL_CHUNKS ? MAX_PART_SIZE_LARGE : MAX_PART_SIZE;

    const parts: PartInfo[] = [];
    let partNumber = 1;
    let startByte = 0;

    while (startByte < fileSize) {
        const endByte = Math.min(startByte + chunkSize, fileSize);
        const partSize = endByte - startByte;

        parts.push({
            partNumber,
            startByte,
            endByte,
            size: partSize,
        });

        startByte = endByte;
        partNumber++;
    }

    return parts;
}

/**
 * Validate files against backend upload constraints
 */
export function validateUploadConstraints(files: FileInfo[]): {
    valid: boolean;
    errors: string[];
} {
    const errors: string[] = [];

    // Check file count limit
    if (files.length > MAX_FILES_PER_REQUEST) {
        errors.push(
            `Too many files: ${files.length} files provided, but maximum is ${MAX_FILES_PER_REQUEST} files per upload. Files will be automatically split into multiple batches.`
        );
    }

    // Calculate total parts across all files
    let totalParts = 0;
    for (const file of files) {
        const parts = calculateFileParts(file.size);
        const numParts = parts.length;

        // Check individual file part limit
        if (numParts > MAX_PARTS_PER_FILE) {
            errors.push(
                `File '${
                    file.name
                }' requires ${numParts} parts, but maximum is ${MAX_PARTS_PER_FILE} parts per file. File size: ${formatFileSize(
                    file.size
                )}`
            );
        }

        totalParts += numParts;
    }

    // Check total parts limit
    if (totalParts > MAX_TOTAL_PARTS_PER_REQUEST) {
        errors.push(
            `Total parts across all files: ${totalParts}, but maximum is ${MAX_TOTAL_PARTS_PER_REQUEST} parts per upload. Files will be automatically split into multiple batches.`
        );
    }

    return {
        valid: errors.length === 0,
        errors,
    };
}

/**
 * Create upload sequences from a list of files
 * Splits files into batches that respect backend constraints
 */
export function createUploadSequences(files: FileInfo[]): UploadSequence[] {
    if (files.length === 0) {
        return [];
    }

    const sequences: UploadSequence[] = [];
    const regularFiles: FileInfo[] = [];
    const previewFiles: FileInfo[] = [];
    const assetPreviewFiles: FileInfo[] = [];

    // Separate files by type
    for (const file of files) {
        if (file.index === 99999 || file.isAssetPreview) {
            // Asset Overall Preview file (uploadType: "assetPreview")
            assetPreviewFiles.push(file);
        } else if (file.isPreviewFile) {
            // Inline preview files (uploadType: "assetFile")
            previewFiles.push(file);
        } else {
            // Regular files (uploadType: "assetFile")
            regularFiles.push(file);
        }
    }

    let sequenceId = 1;

    // Process regular files first (they get lower sequence IDs)
    if (regularFiles.length > 0) {
        sequenceId = processFilesIntoSequences(regularFiles, sequences, sequenceId);
    }

    // Process inline preview files second (they get middle sequence IDs)
    // These are uploaded and completed AFTER regular files
    if (previewFiles.length > 0) {
        sequenceId = processFilesIntoSequences(previewFiles, sequences, sequenceId);
    }

    // Process asset preview files last (they get highest sequence IDs)
    // These use uploadType "assetPreview" and must complete after all other files
    if (assetPreviewFiles.length > 0) {
        processFilesIntoSequences(assetPreviewFiles, sequences, sequenceId);
    }

    return sequences;
}

/**
 * Helper function to process files into sequences
 */
function processFilesIntoSequences(
    files: FileInfo[],
    sequences: UploadSequence[],
    startSequenceId: number
): number {
    let sequenceId = startSequenceId;
    let currentSequence: FileInfo[] = [];
    let currentSize = 0;
    let currentParts = 0;

    for (const file of files) {
        const fileParts = calculateFileParts(file.size);
        const filePartCount = fileParts.length;

        // Check if adding this file would exceed limits
        const wouldExceedSize = currentSize + file.size > MAX_SEQUENCE_SIZE;
        const wouldExceedFiles = currentSequence.length >= MAX_FILES_PER_REQUEST;
        const wouldExceedParts = currentParts + filePartCount > MAX_TOTAL_PARTS_PER_REQUEST;

        // If file is >= 3GB, it gets its own sequence
        if (file.size >= MAX_SEQUENCE_SIZE) {
            // Save current sequence if it has files
            if (currentSequence.length > 0) {
                sequences.push(new UploadSequence(currentSequence, sequenceId));
                sequenceId++;
                currentSequence = [];
                currentSize = 0;
                currentParts = 0;
            }

            // Large file gets its own sequence
            sequences.push(new UploadSequence([file], sequenceId));
            sequenceId++;
        } else if (wouldExceedSize || wouldExceedFiles || wouldExceedParts) {
            // Current sequence is full, start a new one
            if (currentSequence.length > 0) {
                sequences.push(new UploadSequence(currentSequence, sequenceId));
                sequenceId++;
                currentSequence = [];
                currentSize = 0;
                currentParts = 0;
            }

            // Add this file to the new sequence
            currentSequence.push(file);
            currentSize += file.size;
            currentParts += filePartCount;
        } else {
            // Add to current sequence
            currentSequence.push(file);
            currentSize += file.size;
            currentParts += filePartCount;
        }
    }

    // Add remaining files
    if (currentSequence.length > 0) {
        sequences.push(new UploadSequence(currentSequence, sequenceId));
        sequenceId++;
    }

    return sequenceId;
}

/**
 * Check if files need multi-sequence upload
 */
export function needsMultiSequenceUpload(files: FileInfo[]): boolean {
    if (files.length === 0) {
        return false;
    }

    // Check if there are both preview and regular files (will create 2+ sequences)
    const hasPreviewFiles = files.some((f) => f.isPreviewFile);
    const hasRegularFiles = files.some((f) => !f.isPreviewFile);
    if (hasPreviewFiles && hasRegularFiles) {
        return true;
    }

    // Check file count
    if (files.length > MAX_FILES_PER_REQUEST) {
        return true;
    }

    // Check total parts
    let totalParts = 0;
    for (const file of files) {
        const parts = calculateFileParts(file.size);
        totalParts += parts.length;
    }

    if (totalParts > MAX_TOTAL_PARTS_PER_REQUEST) {
        return true;
    }

    // Check if any single file is >= 3GB (would get its own sequence)
    for (const file of files) {
        if (file.size >= MAX_SEQUENCE_SIZE) {
            return true;
        }
    }

    return false;
}

/**
 * Get upload summary information
 */
export function getUploadSummary(sequences: UploadSequence[]): {
    totalFiles: number;
    totalSize: number;
    totalSizeFormatted: string;
    totalParts: number;
    totalSequences: number;
    regularFiles: number;
    previewFiles: number;
} {
    let totalFiles = 0;
    let totalSize = 0;
    let totalParts = 0;
    let regularFiles = 0;
    let previewFiles = 0;

    for (const sequence of sequences) {
        totalFiles += sequence.files.length;
        totalSize += sequence.totalSize;
        totalParts += sequence.totalParts;

        for (const file of sequence.files) {
            if (file.isPreviewFile) {
                previewFiles++;
            } else {
                regularFiles++;
            }
        }
    }

    return {
        totalFiles,
        totalSize,
        totalSizeFormatted: formatFileSize(totalSize),
        totalParts,
        totalSequences: sequences.length,
        regularFiles,
        previewFiles,
    };
}

/**
 * Format file size in human readable format
 */
export function formatFileSize(sizeBytes: number): string {
    if (sizeBytes === 0) {
        return "0 B";
    }

    const sizeNames = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(sizeBytes) / Math.log(1024));
    const p = Math.pow(1024, i);
    const s = Math.round((sizeBytes / p) * 100) / 100;

    return `${s} ${sizeNames[i]}`;
}
