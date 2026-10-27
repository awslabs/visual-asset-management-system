/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback, useRef } from "react";
import AssetUploadService from "../../../services/AssetUploadService";
import { FileUploadTableItem } from "../FileUploadTable";
import { safeGetFile } from "../../../utils/fileHandleCompat";
import { MAX_CONCURRENT_UPLOADS, MAX_RETRY_ATTEMPTS } from "../../../constants/uploadLimits";

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

type FilePartsUpdate = FilePart[] | ((prev: FilePart[]) => FilePart[]);

export function useFilePartsUpload() {
    const [fileParts, setFilePartsState] = useState<FilePart[]>([]);
    const [completedParts, setCompletedParts] = useState(0);
    const [totalParts, setTotalParts] = useState(0);

    // Synchronous mirror of `fileParts`, read through `getFileParts()`.
    //
    // React applies a state update on its next render, so code running between an update and that
    // render reads the previous value from state. A sequence is completed the moment its last part
    // records its ETag, and the completion was built from `fileParts` as the last render saw it: the
    // part that had just finished was still in progress there, so it was left out and the request
    // carried `parts: []` for that file. Every write goes through `setFileParts` below, which stores
    // the next value here before handing it to React, so this ref always holds the latest parts.
    const filePartsRef = useRef<FilePart[]>([]);

    const setFileParts = useCallback((update: FilePartsUpdate) => {
        const next = typeof update === "function" ? update(filePartsRef.current) : update;
        filePartsRef.current = next;
        setFilePartsState(next);
    }, []);

    const getFileParts = useCallback(() => filePartsRef.current, []);

    /**
     * Upload all file parts with sequence-aware concurrency control
     */
    const uploadFileParts = useCallback(
        async (
            fileUploadItems: FileUploadTableItem[],
            partsToUpload: FilePart[],
            onProgress?: (completed: number, total: number) => void,
            // `failedReason` is set while one of the file's parts has failed every automatic
            // retry: the file is then Failed, with that part's error, until the part is retried.
            onFileProgress?: (fileIndex: number, progress: number, failedReason?: string) => void,
            onSequenceComplete?: (sequenceId: number) => void
        ): Promise<{ success: boolean; failedCount: number }> => {
            console.log(`uploadFileParts called: partsToUpload.length=${partsToUpload.length}`);

            if (partsToUpload.length === 0) {
                console.warn("Cannot upload file parts: partsToUpload is empty");
                return { success: true, failedCount: 0 };
            }

            // Update the internal fileParts state with the parts to upload
            setFileParts(partsToUpload);
            const totalPartsCount = partsToUpload.length;
            setTotalParts(totalPartsCount);

            // Create a queue of parts to upload - only include pending and non-cancelled parts
            const pendingParts = partsToUpload.filter((part) => part.status === "pending");
            console.log(`Created queue with ${pendingParts.length} parts to upload`);

            const completed = partsToUpload.filter((part) => part.status === "completed").length;
            setCompletedParts(completed);

            // Sequence-aware queue manager with proper concurrency control
            class SequenceAwareUploadQueue {
                private partsBySequence: Map<number, FilePart[]> = new Map();
                private activeUploads = new Set<string>();
                private completedCount = 0;
                private failedCount = 0;
                private currentSequenceIndex = 0;
                private sequenceIds: number[] = [];
                private completedSequences = new Set<number>();

                constructor(parts: FilePart[]) {
                    // Group parts by sequence and sort by sequence ID
                    parts.forEach((part) => {
                        if (!this.partsBySequence.has(part.sequenceId)) {
                            this.partsBySequence.set(part.sequenceId, []);
                        }
                        this.partsBySequence.get(part.sequenceId)!.push(part);
                    });

                    // Sort sequence IDs for ordered processing
                    this.sequenceIds = Array.from(this.partsBySequence.keys()).sort(
                        (a, b) => a - b
                    );
                    this.completedCount = completed;

                    console.log(`Initialized queue with ${this.sequenceIds.length} sequences`);
                }

                private getPartKey(part: FilePart): string {
                    return `${part.fileIndex}-${part.partNumber}`;
                }

                async processQueue(): Promise<{ success: boolean; failedCount: number }> {
                    const totalParts = Array.from(this.partsBySequence.values()).reduce(
                        (sum, parts) => sum + parts.length,
                        0
                    );
                    console.log(
                        `Starting queue processing with ${totalParts} parts across ${this.sequenceIds.length} sequences`
                    );

                    // Start initial batch
                    this.fillActiveSlots();

                    // Wait for all uploads to complete
                    while (this.activeUploads.size > 0 || this.hasRemainingParts()) {
                        await new Promise((resolve) => setTimeout(resolve, 100));
                        this.fillActiveSlots();
                        this.checkSequenceCompletion();
                    }

                    console.log(
                        `Queue processing completed. Completed: ${this.completedCount}, Failed: ${this.failedCount}`
                    );

                    return {
                        success: this.failedCount === 0,
                        failedCount: this.failedCount,
                    };
                }

                private hasRemainingParts(): boolean {
                    return Array.from(this.partsBySequence.values()).some(
                        (parts) => parts.length > 0
                    );
                }

                private fillActiveSlots(): void {
                    // Fill slots with parts from current and subsequent sequences
                    while (this.activeUploads.size < MAX_CONCURRENT_UPLOADS) {
                        const part = this.getNextPart();
                        if (!part) break;

                        const partKey = this.getPartKey(part);
                        this.activeUploads.add(partKey);
                        this.uploadPart(part, partKey);
                    }
                }

                private getNextPart(): FilePart | null {
                    // Try to get a part from the current sequence first (prioritize sequence order)
                    for (let i = this.currentSequenceIndex; i < this.sequenceIds.length; i++) {
                        const sequenceId = this.sequenceIds[i];
                        const parts = this.partsBySequence.get(sequenceId);

                        if (parts && parts.length > 0) {
                            const part = parts.shift();
                            if (part) {
                                // If this sequence is now empty, move to next sequence
                                if (parts.length === 0) {
                                    this.currentSequenceIndex = i + 1;
                                }
                                return part;
                            }
                        }
                    }

                    return null;
                }

                private checkSequenceCompletion(): void {
                    // Check if any sequences have completed all their parts. Read from the ref, not
                    // inside a state updater: React runs an updater during a later render (twice
                    // under StrictMode), where the completion callback saw the parts of the PREVIOUS
                    // render and the ETag just recorded was missing from them.
                    const currentParts = getFileParts();
                    this.sequenceIds.forEach((sequenceId) => {
                        if (this.completedSequences.has(sequenceId)) return;

                        const sequenceParts = currentParts.filter(
                            (p) => p.sequenceId === sequenceId
                        );
                        const allCompleted =
                            sequenceParts.length > 0 &&
                            sequenceParts.every(
                                (p) => p.status === "completed" || p.status === "cancelled"
                            );

                        if (allCompleted) {
                            this.completedSequences.add(sequenceId);
                            console.log(`Sequence ${sequenceId} completed all parts`);
                            if (onSequenceComplete) {
                                onSequenceComplete(sequenceId);
                            }
                        }
                    });
                }

                private async uploadPart(part: FilePart, partKey: string): Promise<void> {
                    try {
                        console.log(
                            `Starting upload for part ${part.partNumber} of file ${
                                part.fileIndex
                            } (sequence ${part.sequenceId}, attempt ${part.retryCount + 1})`
                        );

                        // Update part status to in-progress
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? {
                                          ...p,
                                          status: "in-progress" as const,
                                          lastAttemptTime: Date.now(),
                                      }
                                    : p
                            )
                        );

                        // Upload with auto-retry
                        const etag = await this.uploadPartWithAutoRetry(part);
                        console.log(`Upload successful for part ${part.partNumber}, etag: ${etag}`);

                        // Update part status to completed
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? { ...p, status: "completed", etag, lastError: undefined }
                                    : p
                            )
                        );

                        // Update counters
                        this.completedCount++;
                        setCompletedParts(this.completedCount);

                        // Call progress callback
                        if (onProgress) {
                            onProgress(this.completedCount, totalPartsCount);
                        }

                        // Update file progress
                        this.updateFileProgress(part.fileIndex);
                    } catch (error: any) {
                        console.error(
                            // Console logging only: a % specifier in the interpolated value can at
                            // most garble this one log line; nothing is executed, stored or
                            // returned from it.
                            // nosemgrep: javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstring
                            `Error uploading part ${part.partNumber} for file ${part.fileIndex} after ${MAX_RETRY_ATTEMPTS} attempts:`,
                            error
                        );

                        // Update part status to failed with error message
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? {
                                          ...p,
                                          status: "failed",
                                          retryCount: p.retryCount + 1,
                                          lastError: error.message || "Upload failed",
                                      }
                                    : p
                            )
                        );

                        this.failedCount++;

                        // The file reads Failed, with this part's error, from the part's status.
                        this.updateFileProgress(part.fileIndex);
                    } finally {
                        // Remove from active uploads
                        this.activeUploads.delete(partKey);
                    }
                }

                private async uploadPartWithAutoRetry(part: FilePart): Promise<string> {
                    for (let attempt = 0; attempt <= MAX_RETRY_ATTEMPTS; attempt++) {
                        try {
                            // Find the file item by matching index property (not array index)
                            const item = fileUploadItems.find((i) => i.index === part.fileIndex);
                            if (!item) {
                                throw new Error(`File item not found for index ${part.fileIndex}`);
                            }

                            // Get the file and create blob
                            const file = await safeGetFile(item.handle);
                            const blob = file.slice(part.start, part.end);

                            // Upload the part
                            const etag = await AssetUploadService.uploadPart(part.uploadUrl, blob);
                            return etag;
                        } catch (error: any) {
                            const isLastAttempt = attempt === MAX_RETRY_ATTEMPTS;

                            if (isLastAttempt) {
                                // Final failure - throw error
                                throw error;
                            }

                            // Calculate exponential backoff: 2^attempt seconds
                            const backoffMs = Math.pow(2, attempt) * 1000;
                            console.log(
                                `Part ${part.partNumber} of file ${part.fileIndex} failed, ` +
                                    `retrying in ${backoffMs}ms (attempt ${
                                        attempt + 1
                                    }/${MAX_RETRY_ATTEMPTS})`
                            );

                            await new Promise((resolve) => setTimeout(resolve, backoffMs));
                        }
                    }
                    throw new Error("Max retries exceeded");
                }

                private updateFileProgress(fileIndex: number): void {
                    // Read from the ref for the same reason as checkSequenceCompletion: the progress
                    // callback sets state of its own and must not run inside a state updater.
                    const filePartsForThisFile = getFileParts().filter(
                        (p) => p.fileIndex === fileIndex
                    );
                    const fileCompletedParts = filePartsForThisFile.filter(
                        (p) => p.status === "completed"
                    ).length;
                    const fileProgress = Math.round(
                        (fileCompletedParts / filePartsForThisFile.length) * 100
                    );
                    const failedPart = filePartsForThisFile.find((p) => p.status === "failed");

                    if (onFileProgress) {
                        onFileProgress(
                            fileIndex,
                            fileProgress,
                            failedPart ? failedPart.lastError || "Upload failed" : undefined
                        );
                    }
                }
            }

            // Create and run the sequence-aware upload queue
            const uploadQueue = new SequenceAwareUploadQueue(pendingParts);
            return await uploadQueue.processQueue();
        },
        [fileParts, totalParts, getFileParts, setFileParts]
    );

    /**
     * Retry failed parts
     */
    const retryFailedParts = useCallback(
        (fileIndex?: number) => {
            setFileParts((prev) =>
                prev.map((part) => {
                    if (
                        part.status === "failed" &&
                        (fileIndex === undefined || part.fileIndex === fileIndex) &&
                        part.retryCount < MAX_RETRY_ATTEMPTS
                    ) {
                        return {
                            ...part,
                            status: "pending",
                            lastError: undefined,
                        };
                    }
                    return part;
                })
            );
        },
        [setFileParts]
    );

    /**
     * Cancel parts for a specific file
     */
    const cancelFileParts = useCallback(
        (fileIndex: number) => {
            setFileParts((prev) =>
                prev.map((part) =>
                    part.fileIndex === fileIndex &&
                    (part.status === "pending" || part.status === "in-progress")
                        ? { ...part, status: "cancelled" }
                        : part
                )
            );
        },
        [setFileParts]
    );

    /**
     * Reset all parts to initial state
     */
    const resetParts = useCallback(() => {
        setFileParts([]);
        setCompletedParts(0);
        setTotalParts(0);
    }, [setFileParts]);

    return {
        fileParts,
        completedParts,
        totalParts,
        setFileParts,
        getFileParts,
        setTotalParts,
        uploadFileParts,
        retryFailedParts,
        cancelFileParts,
        resetParts,
    };
}
