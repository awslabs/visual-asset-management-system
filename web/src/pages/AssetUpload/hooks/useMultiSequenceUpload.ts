/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback } from "react";
import AssetUploadService, {
    InitializeUploadResponse,
    UploadPartResult,
} from "../../../services/AssetUploadService";
import { FileUploadTableItem } from "../FileUploadTable";
import { UploadSequence, calculateFileParts } from "../../../utils/uploadSequencer";
import { safeGetFile } from "../../../utils/fileHandleCompat";
import {
    retryWithBackoff,
    extractErrorMessage,
    extractStatusCode,
    is503Error,
    formatRetryMessage,
} from "../../../utils/uploadRetry";

interface FilePart {
    fileIndex: number;
    partNumber: number;
    start: number;
    end: number;
    uploadUrl: string;
    status: "pending" | "in-progress" | "completed" | "failed" | "cancelled";
    etag?: string;
    retryCount: number;
    sequenceId: number;
    lastError?: string;
    lastAttemptTime?: number;
}

interface SequenceInitResult {
    sequenceId: number;
    uploadId: string;
    response: InitializeUploadResponse;
}

export function useMultiSequenceUpload() {
    const [sequenceUploadIds, setSequenceUploadIds] = useState<Map<number, string>>(new Map());
    const [sequenceInitStatuses, setSequenceInitStatuses] = useState<
        Map<number, "pending" | "in-progress" | "completed" | "failed">
    >(new Map());
    const [sequenceCompleteStatuses, setSequenceCompleteStatuses] = useState<
        Map<number, "pending" | "in-progress" | "completed" | "failed">
    >(new Map());

    /**
     * Initialize upload for a single sequence with retry logic
     */
    const initializeSequence = useCallback(
        async (
            sequence: UploadSequence,
            assetId: string,
            databaseId: string,
            fileUploadItems: FileUploadTableItem[],
            onRetry?: (retryCount: number, error: any, backoffMs: number) => void
        ): Promise<SequenceInitResult> => {
            console.log(
                `Initializing sequence ${sequence.sequenceId} with ${sequence.files.length} files`
            );

            // Mark sequence as in-progress
            setSequenceInitStatuses((prev) => {
                const newMap = new Map(prev);
                newMap.set(sequence.sequenceId, "in-progress");
                return newMap;
            });

            try {
                // Check if this is an asset preview sequence
                const isAssetPreviewSequence = sequence.files.some(
                    (f) => f.index === 99999 || f.isAssetPreview
                );

                // Prepare file information for this sequence
                const files = await Promise.all(
                    sequence.files.map(async (fileInfo) => {
                        // Find the item by matching index property (not array index)
                        const item = fileUploadItems.find((i) => i.index === fileInfo.index);
                        if (!item) {
                            throw new Error(`File item not found for index ${fileInfo.index}`);
                        }

                        const file = await safeGetFile(item.handle);

                        // For asset preview files, use just the filename (no path)
                        const relativeKey =
                            fileInfo.index === 99999 || fileInfo.isAssetPreview
                                ? item.name // Just filename for asset preview
                                : item.relativePath; // Full path for regular files

                        // Handle zero-byte files
                        if (file.size === 0) {
                            return {
                                relativeKey,
                                file_size: 0,
                                num_parts: 0,
                            };
                        }

                        // Get parts for this file from the sequence
                        const parts = sequence.fileParts.get(fileInfo.index) || [];

                        return {
                            relativeKey,
                            file_size: file.size,
                            num_parts: parts.length,
                        };
                    })
                );

                const uploadRequest = {
                    assetId,
                    databaseId,
                    uploadType: isAssetPreviewSequence
                        ? ("assetPreview" as const)
                        : ("assetFile" as const),
                    files,
                };

                // Use retry logic for init call
                const response = await retryWithBackoff(
                    () => AssetUploadService.initializeUpload(uploadRequest),
                    `Initialize sequence ${sequence.sequenceId}`,
                    onRetry
                );

                // Store the uploadId for this sequence
                setSequenceUploadIds((prev) => {
                    const newMap = new Map(prev);
                    newMap.set(sequence.sequenceId, response.uploadId);
                    return newMap;
                });

                // Mark sequence as completed
                setSequenceInitStatuses((prev) => {
                    const newMap = new Map(prev);
                    newMap.set(sequence.sequenceId, "completed");
                    return newMap;
                });

                return {
                    sequenceId: sequence.sequenceId,
                    uploadId: response.uploadId,
                    response,
                };
            } catch (error: any) {
                const errorMessage = extractErrorMessage(error);
                const statusCode = extractStatusCode(error);
                console.error(
                    `Error initializing sequence ${sequence.sequenceId}:`,
                    `Status: ${statusCode}, Message: ${errorMessage}`
                );

                // Mark sequence as failed
                setSequenceInitStatuses((prev) => {
                    const newMap = new Map(prev);
                    newMap.set(sequence.sequenceId, "failed");
                    return newMap;
                });

                // Re-throw with enhanced error message
                const enhancedError = new Error(
                    `Sequence ${sequence.sequenceId} initialization failed (${statusCode}): ${errorMessage}`
                );
                (enhancedError as any).originalError = error;
                (enhancedError as any).statusCode = statusCode;
                throw enhancedError;
            }
        },
        []
    );

    /**
     * Complete upload for a single sequence with retry logic
     */
    const completeSequence = useCallback(
        async (
            sequence: UploadSequence,
            uploadId: string,
            assetId: string,
            databaseId: string,
            fileParts: FilePart[],
            fileUploadItems: FileUploadTableItem[],
            initResponse: InitializeUploadResponse,
            onRetry?: (retryCount: number, error: any, backoffMs: number) => void,
            cancelledFiles?: Set<number>
        ) => {
            console.log(`Completing sequence ${sequence.sequenceId}`);

            // Mark sequence as in-progress
            setSequenceCompleteStatuses((prev) => {
                const newMap = new Map(prev);
                newMap.set(sequence.sequenceId, "in-progress");
                return newMap;
            });

            try {
                // Group parts by file for this sequence
                const filePartsMap = new Map<number, UploadPartResult[]>();

                fileParts.forEach((part) => {
                    if (
                        part.sequenceId === sequence.sequenceId &&
                        part.status === "completed" &&
                        part.etag
                    ) {
                        if (!filePartsMap.has(part.fileIndex)) {
                            filePartsMap.set(part.fileIndex, []);
                        }

                        filePartsMap.get(part.fileIndex)?.push({
                            PartNumber: part.partNumber,
                            ETag: part.etag,
                        });
                    }
                });

                // Prepare completion files for this sequence using init response
                const completionFiles = initResponse.files.map((fileResponse, responseIndex) => {
                    const fileInfo = sequence.files[responseIndex];

                    // Check if file was cancelled - send empty parts array
                    if (cancelledFiles && cancelledFiles.has(fileInfo.index)) {
                        return {
                            relativeKey: fileResponse.relativeKey,
                            uploadIdS3: fileResponse.uploadIdS3,
                            parts: [], // Empty parts = backend nulls the file
                        };
                    }

                    const parts = filePartsMap.get(fileInfo.index) || [];

                    return {
                        relativeKey: fileResponse.relativeKey,
                        uploadIdS3: fileResponse.uploadIdS3,
                        parts,
                    };
                });

                // Check if this is an asset preview sequence
                const isAssetPreviewSequence = sequence.files.some(
                    (f) => f.index === 99999 || f.isAssetPreview
                );

                const completionRequest = {
                    assetId,
                    databaseId,
                    uploadType: isAssetPreviewSequence
                        ? ("assetPreview" as const)
                        : ("assetFile" as const),
                    files: completionFiles,
                };

                // Use retry logic for complete call
                const response = await retryWithBackoff(
                    () => AssetUploadService.completeUpload(uploadId, completionRequest),
                    `Complete sequence ${sequence.sequenceId}`,
                    onRetry
                );

                // Mark sequence as completed
                setSequenceCompleteStatuses((prev) => {
                    const newMap = new Map(prev);
                    newMap.set(sequence.sequenceId, "completed");
                    return newMap;
                });

                return response;
            } catch (error: any) {
                const errorMessage = extractErrorMessage(error);
                const statusCode = extractStatusCode(error);
                console.error(
                    `Error completing sequence ${sequence.sequenceId}:`,
                    `Status: ${statusCode}, Message: ${errorMessage}`
                );

                // Check for 503 - treat as success with warning
                if (is503Error(error)) {
                    console.log(
                        `Sequence ${sequence.sequenceId} got 503 - treating as potential success`
                    );
                    // Mark as completed but note the 503
                    setSequenceCompleteStatuses((prev) => {
                        const newMap = new Map(prev);
                        newMap.set(sequence.sequenceId, "completed");
                        return newMap;
                    });
                    return { is503: true, sequenceId: sequence.sequenceId, message: errorMessage };
                }

                // Mark sequence as failed
                setSequenceCompleteStatuses((prev) => {
                    const newMap = new Map(prev);
                    newMap.set(sequence.sequenceId, "failed");
                    return newMap;
                });

                // Re-throw with enhanced error message
                const enhancedError = new Error(
                    `Sequence ${sequence.sequenceId} completion failed (${statusCode}): ${errorMessage}`
                );
                (enhancedError as any).originalError = error;
                (enhancedError as any).statusCode = statusCode;
                throw enhancedError;
            }
        },
        []
    );

    /**
     * Create file parts from sequence init responses
     */
    const createFilePartsFromSequences = useCallback(
        (
            sequences: UploadSequence[],
            initResults: SequenceInitResult[],
            fileUploadItems: FileUploadTableItem[]
        ): FilePart[] => {
            const allParts: FilePart[] = [];

            initResults.forEach((initResult) => {
                const sequence = sequences.find((s) => s.sequenceId === initResult.sequenceId);
                if (!sequence) return;

                initResult.response.files.forEach((fileResponse, responseIndex) => {
                    // Find the corresponding file in the sequence
                    const fileInfo = sequence.files[responseIndex];
                    if (!fileInfo) return;

                    // Find the item by matching index property (not array index)
                    const item = fileUploadItems.find((i) => i.index === fileInfo.index);
                    if (!item) {
                        console.error(`File item not found for index ${fileInfo.index}`);
                        return;
                    }

                    const fileSize = item.size;

                    // Handle zero-byte files
                    if (fileSize === 0 || fileResponse.partUploadUrls.length === 0) {
                        return;
                    }

                    // Get the parts info from the sequence
                    const partsInfo = sequence.fileParts.get(fileInfo.index) || [];

                    fileResponse.partUploadUrls.forEach((part, partIndex) => {
                        const partInfo = partsInfo[partIndex];
                        if (!partInfo) return;

                        allParts.push({
                            fileIndex: fileInfo.index,
                            partNumber: part.PartNumber,
                            start: partInfo.startByte,
                            end: partInfo.endByte,
                            uploadUrl: part.UploadUrl,
                            status: "pending",
                            etag: undefined,
                            retryCount: 0,
                            sequenceId: sequence.sequenceId,
                        });
                    });
                });
            });

            return allParts;
        },
        []
    );

    return {
        sequenceUploadIds,
        sequenceInitStatuses,
        sequenceCompleteStatuses,
        initializeSequence,
        completeSequence,
        createFilePartsFromSequences,
        setSequenceUploadIds,
        setSequenceInitStatuses,
        setSequenceCompleteStatuses,
    };
}
