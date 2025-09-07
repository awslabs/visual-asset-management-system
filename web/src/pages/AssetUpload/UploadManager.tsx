/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import {
    Box,
    Button,
    Container,
    Header,
    Link,
    SpaceBetween,
    StatusIndicator,
    ProgressBar,
    Alert,
} from "@cloudscape-design/components";
import { FileUploadTableItem, FileUploadTable } from "./FileUploadTable";
import AssetUploadService, {
    InitializeUploadResponse,
    UploadPartResult,
    CompleteUploadResponse,
    CreateAssetResponse,
} from "../../services/AssetUploadService";
import { Metadata } from "../../components/single/Metadata";
import { AssetDetail } from "./AssetUpload";
import { safeGetFile } from "../../utils/fileHandleCompat";

// Maximum number of concurrent uploads
const MAX_CONCURRENT_UPLOADS = 6;
// Maximum size of each part in bytes (150MB)
const MAX_PART_SIZE = 150 * 1024 * 1024;
// Maximum number of parts per file
const MAX_NUM_PARTS_PER_FILE = 10;

interface UploadManagerProps {
    assetDetail: AssetDetail;
    metadata: Metadata;
    fileItems: FileUploadTableItem[];
    onUploadComplete: (response: CompleteUploadResponse) => void;
    onError: (error: Error) => void;
    isExistingAsset?: boolean;
    onCancel?: () => void;
    keyPrefix?: string;
}

interface UploadState {
    assetCreationStatus: "pending" | "in-progress" | "completed" | "failed";
    assetLinksStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    metadataStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    uploadInitStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    previewUploadInitStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    uploadStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    completionStatus: "pending" | "in-progress" | "completed" | "failed" | "partial" | "skipped";
    previewCompletionStatus:
        | "pending"
        | "in-progress"
        | "completed"
        | "failed"
        | "partial"
        | "skipped";
    createdAssetId?: string;
    uploadId?: string;
    previewUploadId?: string;
    errors: { step: string; message: string }[];
    overallProgress: number;
    finalCompletionTriggered: boolean;
    hasFailedParts: boolean;
    hasSkippedParts: boolean;
    assetLinksErrors: string[];
}

interface FilePart {
    fileIndex: number;
    partNumber: number;
    start: number;
    end: number;
    uploadUrl: string;
    status: "pending" | "in-progress" | "completed" | "failed";
    etag?: string;
    retryCount: number;
}

export default function UploadManager({
    assetDetail,
    metadata,
    fileItems,
    onUploadComplete,
    onError,
    isExistingAsset = false,
    onCancel,
    keyPrefix,
}: UploadManagerProps) {
    const [uploadState, setUploadState] = useState<UploadState>({
        assetCreationStatus: isExistingAsset ? "completed" : "pending",
        assetLinksStatus:
            !isExistingAsset &&
            assetDetail.assetLinksFe &&
            (assetDetail.assetLinksFe.parents?.length ||
                assetDetail.assetLinksFe.child?.length ||
                assetDetail.assetLinksFe.related?.length)
                ? "pending"
                : "skipped",
        metadataStatus:
            isExistingAsset || Object.keys(metadata).length === 0 ? "skipped" : "pending",
        uploadInitStatus: fileItems.length > 0 ? "pending" : "skipped",
        previewUploadInitStatus: assetDetail.Preview ? "pending" : "skipped",
        uploadStatus: fileItems.length > 0 ? "pending" : "skipped",
        completionStatus: fileItems.length > 0 ? "pending" : "skipped",
        previewCompletionStatus: assetDetail.Preview ? "pending" : "skipped",
        errors: [],
        overallProgress: 0,
        finalCompletionTriggered: false,
        hasFailedParts: false,
        hasSkippedParts: false,
        assetLinksErrors: [],
    });

    const [fileParts, setFileParts] = useState<FilePart[]>([]);
    const [fileUploadItems, setFileUploadItems] = useState<FileUploadTableItem[]>(fileItems);
    const [uploadResponse, setUploadResponse] = useState<InitializeUploadResponse | null>(null);
    const [activeUploads, setActiveUploads] = useState(0);
    const [completedParts, setCompletedParts] = useState(0);
    const [totalParts, setTotalParts] = useState(0);
    const [showRetryButton, setShowRetryButton] = useState(false);
    const [uploadStarted, setUploadStarted] = useState(false);
    const [hasIncreasedPartSizes, setHasIncreasedPartSizes] = useState(false);

    // Get active upload types based on what files are provided
    const getActiveUploadTypes = useCallback(() => {
        const types: ("assetFiles" | "preview")[] = [];
        if (fileItems.length > 0) types.push("assetFiles");
        if (assetDetail.Preview) types.push("preview");
        return types;
    }, [fileItems.length, assetDetail.Preview]);

    // Centralized completion logic to prevent race conditions
    const checkAndTriggerFinalCompletion = useCallback(() => {
        // Don't trigger if already triggered
        if (uploadState.finalCompletionTriggered) {
            console.log("Final completion already triggered, skipping");
            return;
        }

        const activeTypes = getActiveUploadTypes();
        console.log("Checking completion with active types:", activeTypes);

        // If no files at all, complete immediately after asset/metadata steps
        if (activeTypes.length === 0) {
            const prerequisitesComplete =
                isExistingAsset ||
                (uploadState.assetCreationStatus === "completed" &&
                    uploadState.metadataStatus === "completed");

            if (prerequisitesComplete) {
                console.log(
                    "No files to upload and prerequisites complete - triggering final completion"
                );
                triggerFinalCompletion("Asset created successfully without files", true);
                return;
            }
        }

        // Check if all active upload types are complete
        const assetFilesComplete =
            !activeTypes.includes("assetFiles") ||
            uploadState.completionStatus === "completed" ||
            uploadState.completionStatus === "skipped";
        const previewComplete =
            !activeTypes.includes("preview") ||
            uploadState.previewCompletionStatus === "completed" ||
            uploadState.previewCompletionStatus === "skipped";

        console.log("Completion status check:", {
            assetFilesComplete,
            previewComplete,
            assetFilesStatus: uploadState.completionStatus,
            previewStatus: uploadState.previewCompletionStatus,
        });

        if (assetFilesComplete && previewComplete) {
            // Determine completion message based on what was uploaded
            let message = "Upload completed successfully";
            let isFullSuccess = true;

            if (activeTypes.includes("assetFiles") && activeTypes.includes("preview")) {
                const hasAssetErrors = uploadState.completionStatus === "failed";
                const hasPreviewErrors = uploadState.previewCompletionStatus === "failed";

                if (hasAssetErrors || hasPreviewErrors) {
                    message = "Upload completed with some issues";
                    isFullSuccess = false;
                } else {
                    message = "Asset files and preview uploaded successfully";
                }
            } else if (activeTypes.includes("assetFiles")) {
                const hasErrors = uploadState.completionStatus === "failed";
                message = hasErrors
                    ? "Asset files uploaded with some issues"
                    : "Asset files uploaded successfully";
                isFullSuccess = !hasErrors;
            } else if (activeTypes.includes("preview")) {
                const hasErrors = uploadState.previewCompletionStatus === "failed";
                message = hasErrors
                    ? "Preview file uploaded with some issues"
                    : "Preview file uploaded successfully";
                isFullSuccess = !hasErrors;
            }

            console.log("All upload types complete - triggering final completion:", message);
            triggerFinalCompletion(message, isFullSuccess);
        }
    }, [uploadState, isExistingAsset, getActiveUploadTypes]);

    // Safe completion trigger to prevent multiple calls
    const triggerFinalCompletion = useCallback(
        (message: string, isFullSuccess: boolean) => {
            console.log("Triggering final completion:", message, "Success:", isFullSuccess);

            setUploadState((prev) => ({ ...prev, finalCompletionTriggered: true }));

            const finalResponse: CompleteUploadResponse = {
                assetId: uploadState.createdAssetId || assetDetail.assetId || "",
                message,
                uploadId:
                    uploadState.uploadId || uploadState.previewUploadId || "no-upload-required",
                fileResults: [],
                overallSuccess: isFullSuccess,
            };

            onUploadComplete(finalResponse);
        },
        [uploadState, assetDetail, onUploadComplete]
    );

    // Step 4: Upload File Parts (simplified and more reliable)
    const uploadFileParts = useCallback(async () => {
        console.log(
            `uploadFileParts called: uploadResponse=${!!uploadResponse}, fileParts.length=${
                fileParts.length
            }`
        );

        if (!uploadResponse || fileParts.length === 0) {
            console.warn("Cannot upload file parts: uploadResponse or fileParts is missing");
            return;
        }

        try {
            console.log("Setting upload status to in-progress");
            setUploadState((prev) => ({ ...prev, uploadStatus: "in-progress" }));

            // Create a queue of parts to upload - only include pending parts
            const pendingParts = fileParts.filter((part) => part.status === "pending");
            console.log(`Created queue with ${pendingParts.length} parts to upload`);

            let completed = fileParts.filter((part) => part.status === "completed").length;
            setCompletedParts(completed);

            // Simple queue manager with proper concurrency control
            class UploadQueue {
                private queue: FilePart[] = [];
                private activeUploads = new Set<string>();
                private completedCount = 0;
                private failedCount = 0;

                constructor(parts: FilePart[]) {
                    this.queue = [...parts];
                    this.completedCount = completed;
                }

                private getPartKey(part: FilePart): string {
                    return `${part.fileIndex}-${part.partNumber}`;
                }

                async processQueue(): Promise<void> {
                    console.log(`Starting queue processing with ${this.queue.length} parts`);

                    // Start initial batch
                    this.fillActiveSlots();

                    // Wait for all uploads to complete
                    while (this.activeUploads.size > 0 || this.queue.length > 0) {
                        await new Promise((resolve) => setTimeout(resolve, 100));
                        this.fillActiveSlots();
                    }

                    console.log(
                        `Queue processing completed. Completed: ${this.completedCount}, Failed: ${this.failedCount}`
                    );
                }

                private fillActiveSlots(): void {
                    while (
                        this.queue.length > 0 &&
                        this.activeUploads.size < MAX_CONCURRENT_UPLOADS
                    ) {
                        const part = this.queue.shift();
                        if (part) {
                            const partKey = this.getPartKey(part);
                            this.activeUploads.add(partKey);
                            this.uploadPart(part, partKey);
                        }
                    }
                }

                private async uploadPart(part: FilePart, partKey: string): Promise<void> {
                    try {
                        console.log(
                            `Starting upload for part ${part.partNumber} of file ${part.fileIndex}`
                        );

                        // Update part status to in-progress
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? { ...p, status: "in-progress" as const }
                                    : p
                            )
                        );

                        // Update file item status if needed
                        setFileUploadItems((prev) =>
                            prev.map((item, idx) =>
                                idx === part.fileIndex && item.status === "Queued"
                                    ? {
                                          ...item,
                                          status: "In Progress",
                                          startedAt: Math.floor(new Date().getTime() / 1000),
                                      }
                                    : item
                            )
                        );

                        // Get the file and create blob using our safe utility
                        const file = await safeGetFile(fileUploadItems[part.fileIndex].handle);
                        const blob = file.slice(part.start, part.end);

                        // Upload the part
                        const etag = await AssetUploadService.uploadPart(part.uploadUrl, blob);
                        console.log(`Upload successful for part ${part.partNumber}, etag: ${etag}`);

                        // Update part status to completed
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? { ...p, status: "completed", etag }
                                    : p
                            )
                        );

                        // Update counters
                        this.completedCount++;
                        setCompletedParts(this.completedCount);

                        // Update overall progress
                        const progress = Math.round((this.completedCount / totalParts) * 100);
                        setUploadState((prev) => ({ ...prev, overallProgress: progress }));

                        // Update file progress
                        this.updateFileProgress(part.fileIndex);

                        // Check if all parts are completed
                        if (this.completedCount === totalParts) {
                            console.log("All parts completed, setting upload status to completed");
                            setUploadState((prev) => ({ ...prev, uploadStatus: "completed" }));
                        }
                    } catch (error) {
                        console.error(
                            `Error uploading part ${part.partNumber} for file ${part.fileIndex}:`,
                            error
                        );

                        // Update part status to failed
                        setFileParts((prev) =>
                            prev.map((p) =>
                                p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                    ? { ...p, status: "failed", retryCount: p.retryCount + 1 }
                                    : p
                            )
                        );

                        // Update file status to failed
                        setFileUploadItems((prev) =>
                            prev.map((item, idx) =>
                                idx === part.fileIndex ? { ...item, status: "Failed" } : item
                            )
                        );

                        this.failedCount++;
                        setShowRetryButton(true);
                    } finally {
                        // Remove from active uploads
                        this.activeUploads.delete(partKey);
                    }
                }

                private updateFileProgress(fileIndex: number): void {
                    setFileParts((currentFileParts) => {
                        const filePartsForThisFile = currentFileParts.filter(
                            (p) => p.fileIndex === fileIndex
                        );
                        const fileCompletedParts = filePartsForThisFile.filter(
                            (p) => p.status === "completed"
                        ).length;
                        const fileProgress = Math.round(
                            (fileCompletedParts / filePartsForThisFile.length) * 100
                        );

                        setFileUploadItems((prev) =>
                            prev.map((item, idx) =>
                                idx === fileIndex
                                    ? {
                                          ...item,
                                          progress: fileProgress,
                                          loaded: Math.round((fileProgress * item.total) / 100),
                                          status:
                                              fileProgress === 100 ? "Completed" : "In Progress",
                                      }
                                    : item
                            )
                        );

                        return currentFileParts;
                    });
                }
            }

            // Create and run the upload queue
            const uploadQueue = new UploadQueue(pendingParts);
            await uploadQueue.processQueue();

            // Final status check
            const currentFileParts = fileParts;
            const failedParts = currentFileParts.filter((part) => part.status === "failed");
            const completedParts = currentFileParts.filter((part) => part.status === "completed");

            console.log(
                `Final status: ${completedParts.length} completed, ${failedParts.length} failed out of ${currentFileParts.length} total parts`
            );

            if (failedParts.length === 0 && completedParts.length > 0) {
                console.log("All parts completed successfully");
                setFileUploadItems((prev) =>
                    prev.map((item) => ({
                        ...item,
                        status: "Completed",
                        progress: 100,
                        loaded: item.total,
                    }))
                );
                setShowRetryButton(false);
                setUploadState((prev) => ({ ...prev, uploadStatus: "completed" }));
            } else if (failedParts.length > 0) {
                console.log(`${failedParts.length} parts failed to upload`);
                setUploadState((prev) => ({
                    ...prev,
                    uploadStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "File Upload",
                            message: `${failedParts.length} file parts failed to upload`,
                        },
                    ],
                }));
                setShowRetryButton(true);
            }
        } catch (error: any) {
            console.error("Upload process error:", error);
            setUploadState((prev) => ({
                ...prev,
                uploadStatus: "failed",
                errors: [
                    ...prev.errors,
                    { step: "File Upload", message: error.message || "Failed to upload files" },
                ],
            }));
            setShowRetryButton(true);
        }
    }, [uploadResponse, fileParts, fileUploadItems, totalParts]);

    // Enhanced retry logic for failed parts
    const retryFailedParts = useCallback(
        async (fileIndex?: number) => {
            console.log("Retrying failed parts for file index:", fileIndex);

            // Reset specific file parts or all failed parts
            setFileParts((prev) =>
                prev.map((part) => {
                    if (
                        part.status === "failed" &&
                        (fileIndex === undefined || part.fileIndex === fileIndex) &&
                        part.retryCount < 3
                    ) {
                        // Max 3 retries
                        return {
                            ...part,
                            status: "pending",
                            retryCount: part.retryCount + 1,
                        };
                    }
                    return part;
                })
            );

            // Reset file status
            setFileUploadItems((prev) =>
                prev.map((item, idx) => {
                    if (fileIndex === undefined || idx === fileIndex) {
                        if (item.status === "Failed") {
                            return { ...item, status: "Queued", progress: 0 };
                        }
                    }
                    return item;
                })
            );

            // Reset upload started flag to allow retry
            setUploadStarted(false);
            setShowRetryButton(false);

            // Update upload state to allow retry
            setUploadState((prev) => ({
                ...prev,
                uploadStatus: "in-progress",
                hasFailedParts: false,
            }));

            // Restart upload process
            await uploadFileParts();
        },
        [uploadFileParts]
    );

    // Skip failed parts and continue with successful ones
    const skipFailedParts = useCallback(
        (fileIndex?: number) => {
            console.log("Skipping failed parts for file index:", fileIndex);

            setFileParts((prev) =>
                prev.map((part) => {
                    if (
                        part.status === "failed" &&
                        (fileIndex === undefined || part.fileIndex === fileIndex)
                    ) {
                        return { ...part, status: "completed", etag: "skipped" }; // Mark as completed to allow progress
                    }
                    return part;
                })
            );

            // Update file status to reflect skipped parts
            setFileUploadItems((prev) =>
                prev.map((item, idx) => {
                    if (fileIndex === undefined || idx === fileIndex) {
                        const itemParts = fileParts.filter((p) => p.fileIndex === idx);
                        const failedParts = itemParts.filter((p) => p.status === "failed");
                        const completedParts = itemParts.filter((p) => p.status === "completed");

                        if (failedParts.length > 0 && completedParts.length > 0) {
                            return { ...item, status: "Completed", progress: 100 }; // Mark as completed with issues
                        } else if (failedParts.length === itemParts.length) {
                            return { ...item, status: "Completed", progress: 100 }; // All parts skipped
                        }
                    }
                    return item;
                })
            );

            setUploadState((prev) => ({
                ...prev,
                hasSkippedParts: true,
                uploadStatus: "completed", // Allow completion with skipped parts
            }));

            setShowRetryButton(false);

            // Trigger completion check
            setTimeout(() => checkAndTriggerFinalCompletion(), 500);
        },
        [fileParts, checkAndTriggerFinalCompletion]
    );

    // Step 1: Create Asset
    const createAsset = useCallback(async () => {
        if (isExistingAsset || !assetDetail.databaseId) {
            return;
        }

        try {
            setUploadState((prev) => ({ ...prev, assetCreationStatus: "in-progress" }));

            const assetData = {
                assetName: assetDetail.assetName || assetDetail.assetId || "",
                databaseId: assetDetail.databaseId,
                description: assetDetail.description || "",
                isDistributable: assetDetail.isDistributable || false,
                tags: assetDetail.tags || [],
            };

            const response = await AssetUploadService.createAsset(assetData);

            setUploadState((prev) => ({
                ...prev,
                assetCreationStatus: "completed",
                createdAssetId: response.assetId,
            }));

            return response;
        } catch (error: any) {
            setUploadState((prev) => ({
                ...prev,
                assetCreationStatus: "failed",
                errors: [
                    ...prev.errors,
                    { step: "Asset Creation", message: error.message || "Failed to create asset" },
                ],
            }));
            throw error;
        }
    }, [assetDetail, isExistingAsset]);

    // Step 2: Add Metadata
    const addMetadata = useCallback(
        async (assetId: string) => {
            if (isExistingAsset || !assetDetail.databaseId) {
                return;
            }

            // Skip if no metadata to add
            if (Object.keys(metadata).length === 0) {
                setUploadState((prev) => ({ ...prev, metadataStatus: "skipped" }));
                return;
            }

            try {
                setUploadState((prev) => ({ ...prev, metadataStatus: "in-progress" }));

                await AssetUploadService.addMetadata(assetDetail.databaseId, assetId, metadata);

                setUploadState((prev) => ({ ...prev, metadataStatus: "completed" }));
            } catch (error: any) {
                setUploadState((prev) => ({
                    ...prev,
                    metadataStatus: "failed",
                    errors: [
                        ...prev.errors,
                        { step: "Metadata", message: error.message || "Failed to add metadata" },
                    ],
                }));
                throw error;
            }
        },
        [assetDetail, metadata, isExistingAsset]
    );

    // Step 2.5: Create Asset Links
    const createAssetLinks = useCallback(
        async (assetId: string) => {
            // Skip if no asset links provided or if this is an existing asset
            if (
                isExistingAsset ||
                !assetDetail.assetLinksFe ||
                (!assetDetail.assetLinksFe.parents?.length &&
                    !assetDetail.assetLinksFe.child?.length &&
                    !assetDetail.assetLinksFe.related?.length)
            ) {
                setUploadState((prev) => ({ ...prev, assetLinksStatus: "skipped" }));
                return;
            }

            try {
                setUploadState((prev) => ({ ...prev, assetLinksStatus: "in-progress" }));

                const linkPromises = [];
                const errors: string[] = [];
                const createdLinks: {
                    assetLinkId: string;
                    assetId: string;
                    relationshipType: string;
                }[] = [];

                console.log("Creating asset links with assetId:", assetId);
                console.log(
                    "Asset links data (assetLinksFe):",
                    JSON.stringify(assetDetail.assetLinksFe, null, 2)
                );
                console.log(
                    "Asset links metadata:",
                    JSON.stringify(assetDetail.assetLinksMetadata, null, 2)
                );

                // Create parent links (parentChild relationship)
                if (assetDetail.assetLinksFe.parents?.length) {
                    console.log("Creating parent links:", assetDetail.assetLinksFe.parents);
                    for (const parentAsset of assetDetail.assetLinksFe.parents) {
                        // Validate that parentAsset has required fields
                        if (!parentAsset.assetId || !parentAsset.databaseId) {
                            const errorMsg = `Parent asset missing required fields: assetId=${parentAsset.assetId}, databaseId=${parentAsset.databaseId}`;
                            errors.push(errorMsg);
                            console.error(errorMsg, parentAsset);
                            continue;
                        }

                        linkPromises.push(
                            AssetUploadService.createAssetLink({
                                fromAssetId: parentAsset.assetId,
                                fromAssetDatabaseId: parentAsset.databaseId,
                                toAssetId: assetId,
                                toAssetDatabaseId: assetDetail.databaseId || "",
                                relationshipType: "parentChild",
                            })
                                .then((response) => {
                                    console.log("Parent link created successfully:", response);
                                    createdLinks.push({
                                        assetLinkId: response.assetLinkId,
                                        assetId: parentAsset.assetId,
                                        relationshipType: "parents",
                                    });
                                    return response;
                                })
                                .catch((error) => {
                                    const errorMsg = `Parent link failed for ${parentAsset.assetId}: ${error.message}`;
                                    errors.push(errorMsg);
                                    console.error(errorMsg, error);
                                })
                        );
                    }
                }

                // Create child links (parentChild relationship)
                if (assetDetail.assetLinksFe.child?.length) {
                    console.log("Creating child links:", assetDetail.assetLinksFe.child);
                    for (const childAsset of assetDetail.assetLinksFe.child) {
                        // Validate that childAsset has required fields
                        if (!childAsset.assetId || !childAsset.databaseId) {
                            const errorMsg = `Child asset missing required fields: assetId=${childAsset.assetId}, databaseId=${childAsset.databaseId}`;
                            errors.push(errorMsg);
                            console.error(errorMsg, childAsset);
                            continue;
                        }

                        linkPromises.push(
                            AssetUploadService.createAssetLink({
                                fromAssetId: assetId,
                                fromAssetDatabaseId: assetDetail.databaseId || "",
                                toAssetId: childAsset.assetId,
                                toAssetDatabaseId: childAsset.databaseId,
                                relationshipType: "parentChild",
                            })
                                .then((response) => {
                                    console.log("Child link created successfully:", response);
                                    createdLinks.push({
                                        assetLinkId: response.assetLinkId,
                                        assetId: childAsset.assetId,
                                        relationshipType: "child",
                                    });
                                    return response;
                                })
                                .catch((error) => {
                                    const errorMsg = `Child link failed for ${childAsset.assetId}: ${error.message}`;
                                    errors.push(errorMsg);
                                    console.error(errorMsg, error);
                                })
                        );
                    }
                }

                // Create related links (related relationship)
                if (assetDetail.assetLinksFe.related?.length) {
                    console.log("Creating related links:", assetDetail.assetLinksFe.related);
                    for (const relatedAsset of assetDetail.assetLinksFe.related) {
                        // Validate that relatedAsset has required fields
                        if (!relatedAsset.assetId || !relatedAsset.databaseId) {
                            const errorMsg = `Related asset missing required fields: assetId=${relatedAsset.assetId}, databaseId=${relatedAsset.databaseId}`;
                            errors.push(errorMsg);
                            console.error(errorMsg, relatedAsset);
                            continue;
                        }

                        linkPromises.push(
                            AssetUploadService.createAssetLink({
                                fromAssetId: assetId,
                                fromAssetDatabaseId: assetDetail.databaseId || "",
                                toAssetId: relatedAsset.assetId,
                                toAssetDatabaseId: relatedAsset.databaseId,
                                relationshipType: "related",
                            })
                                .then((response) => {
                                    console.log("Related link created successfully:", response);
                                    createdLinks.push({
                                        assetLinkId: response.assetLinkId,
                                        assetId: relatedAsset.assetId,
                                        relationshipType: "related",
                                    });
                                    return response;
                                })
                                .catch((error) => {
                                    const errorMsg = `Related link failed for ${relatedAsset.assetId}: ${error.message}`;
                                    errors.push(errorMsg);
                                    console.error(errorMsg, error);
                                })
                        );
                    }
                }

                // Wait for all link creation attempts to complete
                await Promise.allSettled(linkPromises);

                console.log("Asset links creation completed. Created links:", createdLinks);
                console.log("Errors:", errors);

                // Now create metadata for the successfully created links
                if (createdLinks.length > 0) {
                    console.log("Creating asset link metadata for created links:", createdLinks);
                    const metadataPromises = [];

                    for (const link of createdLinks) {
                        // Find the original asset data to get the metadata
                        let originalAsset = null;
                        let relationshipKey = "";

                        if (link.relationshipType === "parents") {
                            originalAsset = assetDetail.assetLinksFe.parents?.find(
                                (asset) => asset.assetId === link.assetId
                            );
                            relationshipKey = "parents";
                        } else if (link.relationshipType === "child") {
                            originalAsset = assetDetail.assetLinksFe.child?.find(
                                (asset) => asset.assetId === link.assetId
                            );
                            relationshipKey = "child";
                        } else if (link.relationshipType === "related") {
                            originalAsset = assetDetail.assetLinksFe.related?.find(
                                (asset) => asset.assetId === link.assetId
                            );
                            relationshipKey = "related";
                        }

                        // Check if the original asset has metadata
                        if (
                            originalAsset &&
                            originalAsset.metadata &&
                            originalAsset.metadata.length > 0
                        ) {
                            console.log(
                                `Creating metadata for link ${link.assetLinkId} from original asset:`,
                                originalAsset.metadata
                            );

                            for (const metadataItem of originalAsset.metadata) {
                                metadataPromises.push(
                                    AssetUploadService.createAssetLinkMetadata(link.assetLinkId, {
                                        metadataKey: metadataItem.metadataKey,
                                        metadataValue: metadataItem.metadataValue,
                                        metadataValueType: metadataItem.metadataValueType,
                                    })
                                        .then((response) => {
                                            console.log(
                                                `Asset link metadata created successfully for ${link.assetLinkId}:`,
                                                response
                                            );
                                            return response;
                                        })
                                        .catch((error) => {
                                            const errorMsg = `Asset link metadata creation failed for ${link.assetLinkId}: ${error.message}`;
                                            errors.push(errorMsg);
                                            console.error(errorMsg, error);
                                        })
                                );
                            }
                        } else {
                            console.log(
                                `No metadata found for asset ${link.assetId} in relationship ${link.relationshipType}`
                            );
                        }

                        // Also check the assetLinksMetadata structure as fallback
                        if (assetDetail.assetLinksMetadata) {
                            const fallbackMetadata =
                                assetDetail.assetLinksMetadata[
                                    link.relationshipType as keyof typeof assetDetail.assetLinksMetadata
                                ]?.[link.assetId];

                            if (fallbackMetadata && fallbackMetadata.length > 0) {
                                console.log(
                                    `Creating metadata for link ${link.assetLinkId} from fallback structure:`,
                                    fallbackMetadata
                                );
                                for (const metadataItem of fallbackMetadata) {
                                    metadataPromises.push(
                                        AssetUploadService.createAssetLinkMetadata(
                                            link.assetLinkId,
                                            {
                                                metadataKey: metadataItem.metadataKey,
                                                metadataValue: metadataItem.metadataValue,
                                                metadataValueType: metadataItem.metadataValueType,
                                            }
                                        )
                                            .then((response) => {
                                                console.log(
                                                    `Asset link metadata created successfully for ${link.assetLinkId} (fallback):`,
                                                    response
                                                );
                                                return response;
                                            })
                                            .catch((error) => {
                                                const errorMsg = `Asset link metadata creation failed for ${link.assetLinkId} (fallback): ${error.message}`;
                                                errors.push(errorMsg);
                                                console.error(errorMsg, error);
                                            })
                                    );
                                }
                            }
                        }
                    }

                    if (metadataPromises.length > 0) {
                        console.log(
                            `Executing ${metadataPromises.length} metadata creation promises`
                        );
                        await Promise.allSettled(metadataPromises);
                    } else {
                        console.log("No metadata promises to execute");
                    }
                }

                if (errors.length > 0) {
                    setUploadState((prev) => ({
                        ...prev,
                        assetLinksStatus: "failed",
                        assetLinksErrors: errors,
                        errors: [
                            ...prev.errors,
                            ...errors.map((err) => ({ step: "Asset Links", message: err })),
                        ],
                    }));
                } else {
                    setUploadState((prev) => ({ ...prev, assetLinksStatus: "completed" }));
                }
            } catch (error: any) {
                console.error("Asset links creation error:", error);
                setUploadState((prev) => ({
                    ...prev,
                    assetLinksStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Asset Links",
                            message: error.message || "Failed to create asset links",
                        },
                    ],
                }));
                throw error;
            }
        },
        [assetDetail, isExistingAsset]
    );

    // Step 3: Initialize Upload
    const initializeUpload = useCallback(
        async (assetId: string) => {
            try {
                setUploadState((prev) => ({ ...prev, uploadInitStatus: "in-progress" }));

                // Prepare file information for upload initialization with scaled part sizes
                let hasLargeFiles = false;
                let hasZeroByteFiles = false;
                const files = await Promise.all(
                    fileUploadItems.map(async (item) => {
                        const file = await safeGetFile(item.handle);

                        // Handle zero-byte files
                        if (file.size === 0) {
                            hasZeroByteFiles = true;
                            console.log(`Zero-byte file detected: ${file.name}`);
                            return {
                                relativeKey: item.relativePath,
                                file_size: 0,
                                num_parts: 0,
                            };
                        }

                        // Calculate if this file would exceed MAX_NUM_PARTS_PER_FILE with standard part size
                        const standardNumParts = Math.ceil(file.size / MAX_PART_SIZE);
                        let actualNumParts = standardNumParts;

                        if (standardNumParts > MAX_NUM_PARTS_PER_FILE) {
                            hasLargeFiles = true;
                            actualNumParts = MAX_NUM_PARTS_PER_FILE;
                            console.log(
                                `File ${file.name} would have ${standardNumParts} parts, scaling to ${actualNumParts} parts`
                            );
                        }

                        // Use the item's relativePath which already includes the keyPrefix if it was set
                        return {
                            relativeKey: item.relativePath,
                            file_size: file.size,
                            num_parts: actualNumParts,
                        };
                    })
                );

                // Set flag if we have files that require increased part sizes
                if (hasLargeFiles) {
                    setHasIncreasedPartSizes(true);
                }

                const uploadRequest = {
                    assetId,
                    databaseId: assetDetail.databaseId || "",
                    uploadType: "assetFile" as const,
                    files,
                };

                const response = await AssetUploadService.initializeUpload(uploadRequest);
                setUploadResponse(response);

                // Prepare file parts for upload with scaled part sizes
                const allParts: FilePart[] = [];
                let totalPartsCount = 0;

                response.files.forEach((file, fileIndex) => {
                    const fileSize = fileUploadItems[fileIndex].size;
                    
                    // Handle zero-byte files - they have no parts to upload
                    if (fileSize === 0 || file.partUploadUrls.length === 0) {
                        console.log(`Zero-byte file ${fileUploadItems[fileIndex].name} - no parts to upload`);
                        // Mark zero-byte files as completed immediately since they don't need part uploads
                        setFileUploadItems((prev) =>
                            prev.map((item, idx) =>
                                idx === fileIndex
                                    ? {
                                          ...item,
                                          status: "Completed",
                                          progress: 100,
                                          loaded: item.total,
                                      }
                                    : item
                            )
                        );
                        return; // Skip creating parts for zero-byte files
                    }

                    // Calculate the actual part size for this file
                    const standardNumParts = Math.ceil(fileSize / MAX_PART_SIZE);

                    let actualPartSize = MAX_PART_SIZE;
                    if (standardNumParts > MAX_NUM_PARTS_PER_FILE) {
                        // Scale up part size to limit to MAX_NUM_PARTS_PER_FILE
                        actualPartSize = Math.ceil(fileSize / MAX_NUM_PARTS_PER_FILE);
                        console.log(
                            `File ${fileUploadItems[fileIndex].name}: scaled part size from ${MAX_PART_SIZE} to ${actualPartSize} bytes`
                        );
                    }

                    file.partUploadUrls.forEach((part) => {
                        allParts.push({
                            fileIndex,
                            partNumber: part.PartNumber,
                            start: (part.PartNumber - 1) * actualPartSize,
                            end: Math.min(part.PartNumber * actualPartSize, fileSize),
                            uploadUrl: part.UploadUrl,
                            status: "pending",
                            retryCount: 0,
                        });
                        totalPartsCount++;
                    });
                });

                setFileParts(allParts);
                setTotalParts(totalPartsCount);

                // If we have zero-byte files and no parts to upload, mark upload as completed
                if (hasZeroByteFiles && totalPartsCount === 0) {
                    console.log("All files are zero-byte files - skipping upload phase");
                    setUploadState((prev) => ({
                        ...prev,
                        uploadInitStatus: "completed",
                        uploadStatus: "completed",
                        uploadId: response.uploadId,
                    }));
                } else {
                    setUploadState((prev) => ({
                        ...prev,
                        uploadInitStatus: "completed",
                        uploadId: response.uploadId,
                    }));
                }

                return response;
            } catch (error: any) {
                console.error("Upload initialization error:", error);

                // Check if this is a 503 error (service unavailable/timeout)
                const is503Error =
                    error.message?.includes("503") ||
                    error.status === 503 ||
                    error.response?.status === 503 ||
                    error.message?.toLowerCase().includes("service unavailable") ||
                    error.message?.toLowerCase().includes("timeout");

                let errorMessage = error.message || "Failed to initialize upload";

                if (is503Error) {
                    errorMessage = `Upload initialization API timed out (503 error). You may have uploaded too many individual files or file parts, which has caused the API to timeout. Consider reducing the number of files in this batch or breaking large files into smaller ones. Original error: ${
                        error.message || "Service unavailable"
                    }`;
                }

                setUploadState((prev) => ({
                    ...prev,
                    uploadInitStatus: "failed",
                    errors: [
                        ...prev.errors,
                        { step: "Upload Initialization", message: errorMessage },
                    ],
                }));
                throw error;
            }
        },
        [assetDetail, fileUploadItems]
    );

    // Step 3b: Initialize Preview File Upload
    const initializePreviewUpload = useCallback(
        async (assetId: string) => {
            // Skip if no preview file
            if (!assetDetail.Preview) {
                console.log("No preview file to upload, skipping preview upload initialization");
                setUploadState((prev) => ({
                    ...prev,
                    previewUploadInitStatus: "skipped",
                    previewCompletionStatus: "skipped",
                }));
                return;
            }

            // Always use the provided assetId parameter directly, which should be valid at this point
            if (!assetId) {
                console.error("No asset ID provided for preview upload initialization");
                setUploadState((prev) => ({
                    ...prev,
                    previewUploadInitStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Preview Upload Initialization",
                            message: "No asset ID provided for preview upload initialization",
                        },
                    ],
                }));
                return;
            }

            // Store the asset ID in state for future reference
            setUploadState((prev) => ({
                ...prev,
                createdAssetId: assetId,
            }));

            try {
                setUploadState((prev) => ({ ...prev, previewUploadInitStatus: "in-progress" }));

                // Prepare file information for preview upload initialization
                const previewFile = assetDetail.Preview;

                console.log(`Initializing preview upload with assetId: ${assetId}`);

                const uploadRequest = {
                    assetId: assetId,
                    databaseId: assetDetail.databaseId || "",
                    uploadType: "assetPreview" as const,
                    files: [
                        {
                            relativeKey: previewFile.name,
                            file_size: previewFile.size,
                        },
                    ],
                };

                const response = await AssetUploadService.initializeUpload(uploadRequest);

                // Store the preview upload ID and ensure the asset ID is set
                setUploadState((prev) => ({
                    ...prev,
                    previewUploadInitStatus: "completed",
                    previewUploadId: response.uploadId,
                    createdAssetId: assetId, // Ensure the asset ID is set
                }));

                // Store the asset ID in state before uploading the preview file
                console.log(`About to upload preview file with assetId: ${assetId}`);

                // Upload the preview file directly
                await uploadPreviewFile(response, previewFile, assetId);

                return response;
            } catch (error: any) {
                setUploadState((prev) => ({
                    ...prev,
                    previewUploadInitStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Preview Upload Initialization",
                            message: error.message || "Failed to initialize preview upload",
                        },
                    ],
                }));
                throw error;
            }
        },
        [assetDetail]
    );

    // Upload preview file
    const uploadPreviewFile = useCallback(
        async (initResponse: InitializeUploadResponse, previewFile: File, assetId: string) => {
            try {
                console.log(`Starting preview file upload with assetId: ${assetId}`);

                if (!assetId) {
                    console.error(
                        "No asset ID available for preview upload - this should not happen"
                    );
                    throw new Error("No asset ID available for preview upload");
                }

                // Update UI to show preview upload in progress
                const previewFileItem: FileUploadTableItem = {
                    handle: { getFile: () => Promise.resolve(previewFile) },
                    index: 99999, // Use a high index to distinguish from regular files
                    name: previewFile.name,
                    size: previewFile.size,
                    relativePath: `previews/${previewFile.name}`,
                    progress: 0,
                    status: "In Progress",
                    loaded: 0,
                    total: previewFile.size,
                };

                // Add preview file to the file items list
                setFileUploadItems((prev) => [...prev, previewFileItem]);

                // Get the file response for the preview file
                const fileResponse = initResponse.files[0];
                const parts: UploadPartResult[] = [];

                // Upload each part
                for (let i = 0; i < fileResponse.partUploadUrls.length; i++) {
                    const part = fileResponse.partUploadUrls[i];
                    const partNumber = part.PartNumber;
                    const partSize = MAX_PART_SIZE;
                    const start = (partNumber - 1) * partSize;
                    const end = Math.min(start + partSize, previewFile.size);

                    // Create blob for this part
                    const blob = previewFile.slice(start, end);

                    // Upload the part
                    const etag = await AssetUploadService.uploadPart(part.UploadUrl, blob);

                    // Add to parts list
                    parts.push({
                        PartNumber: partNumber,
                        ETag: etag,
                    });

                    // Update progress
                    const progress = Math.round(
                        ((i + 1) / fileResponse.partUploadUrls.length) * 100
                    );
                    setFileUploadItems((prev) =>
                        prev.map((item) =>
                            item.index === 99999
                                ? {
                                      ...item,
                                      progress: progress,
                                      loaded: (progress * item.total) / 100,
                                      status: progress === 100 ? "Completed" : "In Progress",
                                  }
                                : item
                        )
                    );
                }

                // Complete the preview upload with the provided assetId
                await completePreviewUpload(
                    initResponse.uploadId,
                    fileResponse.relativeKey,
                    fileResponse.uploadIdS3,
                    parts,
                    assetId
                );
            } catch (error: any) {
                console.error("Error uploading preview file:", error);
                setUploadState((prev) => ({
                    ...prev,
                    previewCompletionStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Preview Upload",
                            message: error.message || "Failed to upload preview file",
                        },
                    ],
                }));
                throw error;
            }
        },
        []
    );

    // Step 5b: Complete Preview Upload
    const completePreviewUpload = useCallback(
        async (
            uploadId: string,
            relativeKey: string,
            uploadIdS3: string,
            parts: UploadPartResult[],
            assetId: string
        ) => {
            try {
                setUploadState((prev) => ({ ...prev, previewCompletionStatus: "in-progress" }));

                // Make sure we have a valid assetId
                if (!assetId) {
                    throw new Error("No asset ID available for preview completion");
                }

                const completionRequest = {
                    assetId: assetId,
                    databaseId: assetDetail.databaseId || "",
                    uploadType: "assetPreview" as const,
                    files: [
                        {
                            relativeKey,
                            uploadIdS3,
                            parts,
                        },
                    ],
                };

                // Log the asset ID being used
                console.log(`Using asset ID for preview completion: ${assetId}`);

                console.log(
                    "Sending preview completion request:",
                    JSON.stringify(completionRequest, null, 2)
                );

                const response = await AssetUploadService.completeUpload(
                    uploadId,
                    completionRequest
                );

                console.log("Preview completion response:", JSON.stringify(response, null, 2));

                setUploadState((prev) => ({ ...prev, previewCompletionStatus: "completed" }));

                // If this is the only upload (no asset files), trigger final completion
                if (fileItems.length === 0) {
                    console.log(
                        "Preview upload completed and no asset files - triggering final completion"
                    );
                    const finalResponse: CompleteUploadResponse = {
                        assetId: assetId,
                        message: "Asset created successfully with preview file",
                        uploadId: uploadId,
                        fileResults: [],
                        overallSuccess: true,
                    };
                    onUploadComplete(finalResponse);
                }

                return response;
            } catch (error: any) {
                setUploadState((prev) => ({
                    ...prev,
                    previewCompletionStatus: "failed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Preview Upload Completion",
                            message: error.message || "Failed to complete preview upload",
                        },
                    ],
                }));
                throw error;
            }
        },
        [assetDetail]
    );

    // Step 5: Complete Upload
    const completeUpload = useCallback(async () => {
        console.log(
            "completeUpload called with uploadResponse:",
            !!uploadResponse,
            "uploadId:",
            uploadState.uploadId
        );

        if (!uploadResponse || !uploadState.uploadId) {
            console.error("Cannot complete upload: uploadResponse or uploadId is missing");
            return;
        }

        try {
            console.log("Setting completion status to in-progress");
            setUploadState((prev) => ({ ...prev, completionStatus: "in-progress" }));

            // Group parts by file
            const filePartsMap = new Map<number, UploadPartResult[]>();
            console.log("Total file parts:", fileParts.length);

            fileParts.forEach((part) => {
                if (part.status === "completed" && part.etag) {
                    if (!filePartsMap.has(part.fileIndex)) {
                        filePartsMap.set(part.fileIndex, []);
                    }

                    filePartsMap.get(part.fileIndex)?.push({
                        PartNumber: part.partNumber,
                        ETag: part.etag,
                    });
                }
            });

            // Prepare completion request - include all files from the upload response
            console.log("Preparing completion request with filePartsMap size:", filePartsMap.size);
            const completionFiles = uploadResponse.files.map((fileResponse, fileIndex) => {
                const parts = filePartsMap.get(fileIndex) || [];
                console.log(
                    `File ${fileIndex}: ${fileResponse.relativeKey}, parts: ${parts.length}, expected parts: ${fileResponse.numParts}`
                );
                
                // For zero-byte files, numParts will be 0 and parts array will be empty - this is correct
                return {
                    relativeKey: fileResponse.relativeKey,
                    uploadIdS3: fileResponse.uploadIdS3,
                    parts: parts,
                };
            });

            // Filter for valid completion files - include zero-byte files (which have 0 expected parts)
            const validCompletionFiles = completionFiles.filter((file) => {
                const fileResponse = uploadResponse.files.find(
                    (f) => f.relativeKey === file.relativeKey
                );
                
                if (!fileResponse) {
                    console.warn(`File response not found for ${file.relativeKey}`);
                    return false;
                }
                
                // For zero-byte files, both parts.length and numParts should be 0
                const isValid = file.parts.length === fileResponse.numParts;
                
                if (!isValid) {
                    console.warn(
                        `File ${file.relativeKey} has ${file.parts.length} parts but expected ${fileResponse.numParts}`
                    );
                } else if (fileResponse.numParts === 0) {
                    console.log(`Zero-byte file ${file.relativeKey} ready for completion`);
                }
                
                return isValid;
            });

            console.log(
                `Valid completion files: ${validCompletionFiles.length} out of ${completionFiles.length}`
            );

            if (validCompletionFiles.length === 0) {
                throw new Error("No files were successfully uploaded");
            }

            const completionRequest = {
                assetId: uploadState.createdAssetId || assetDetail.assetId || "",
                databaseId: assetDetail.databaseId || "",
                uploadType: "assetFile" as const,
                files: validCompletionFiles,
            };

            console.log("Sending completion request:", JSON.stringify(completionRequest, null, 2));
            console.log("Using uploadId:", uploadState.uploadId);

            const response = await AssetUploadService.completeUpload(
                uploadState.uploadId,
                completionRequest
            );

            console.log("Completion response:", JSON.stringify(response, null, 2));

            // Check if the response indicates partial failure (overallSuccess is false)
            const hasPartialFailure = response.overallSuccess === false;
            const failedFiles = response.fileResults?.filter((file) => !file.success) || [];
            const allFilesFailed = failedFiles.length === response.fileResults?.length;

            // Only mark as complete if there are no preview errors
            const hasPreviewErrors =
                uploadState.previewUploadInitStatus === "failed" ||
                uploadState.previewCompletionStatus === "failed";

            if (hasPreviewErrors) {
                console.log("Asset files upload completed, but preview upload had errors");
                setUploadState((prev) => ({
                    ...prev,
                    completionStatus: "completed",
                    errors: [
                        ...prev.errors,
                        {
                            step: "Upload Process",
                            message:
                                "Asset files were uploaded successfully, but there were errors with the preview file upload.",
                        },
                    ],
                }));
                // Still call onUploadComplete but with a modified response indicating preview errors
                const modifiedResponse = {
                    ...response,
                    message: response.message + " (Preview file upload failed)",
                };
                onUploadComplete(modifiedResponse);
            } else if (hasPartialFailure) {
                console.log("Upload completed with partial failures:", failedFiles);

                // Add error messages for failed files
                const failedFileErrors = failedFiles.map((file) => ({
                    step: "File Upload",
                    message: `${file.relativeKey}: ${file.error || "Unknown error"}`,
                }));

                setUploadState((prev) => ({
                    ...prev,
                    completionStatus: allFilesFailed ? "failed" : "partial",
                    errors: [...prev.errors, ...failedFileErrors],
                    hasFailedParts: true,
                }));

                // Call onUploadComplete with the response
                onUploadComplete(response);
            } else {
                setUploadState((prev) => ({ ...prev, completionStatus: "completed" }));
                onUploadComplete(response);
            }

            return response;
        } catch (error: any) {
            console.error("Upload completion error:", error);

            // Check if this is a 503 error (service unavailable/timeout)
            const is503Error =
                error.message?.includes("503") ||
                error.status === 503 ||
                error.response?.status === 503 ||
                error.message?.toLowerCase().includes("service unavailable") ||
                error.message?.toLowerCase().includes("timeout");

            let errorMessage = error.message || "Failed to complete upload";

            if (is503Error) {
                errorMessage = `Upload completion API timed out (503 error). The files may have been successfully uploaded despite this error. Please check the asset page's file manager in a few minutes to verify which files actually completed. Original error: ${
                    error.message || "Service unavailable"
                }`;
            }

            setUploadState((prev) => ({
                ...prev,
                completionStatus: "failed",
                errors: [...prev.errors, { step: "Upload Completion", message: errorMessage }],
            }));
            throw error;
        }
    }, [
        uploadResponse,
        uploadState.uploadId,
        uploadState.createdAssetId,
        assetDetail,
        fileParts,
        onUploadComplete,
    ]);

    // Effect to start file uploads after initialization
    useEffect(() => {
        // Only run this effect when uploadInitStatus changes to 'completed' and uploads haven't started yet
        if (
            uploadState.uploadInitStatus === "completed" &&
            fileParts.length > 0 &&
            !uploadStarted
        ) {
            console.log("Upload initialization completed, starting file uploads");

            // Set flag to prevent multiple upload starts
            setUploadStarted(true);

            // Start the file uploads
            const startUploads = async () => {
                try {
                    await uploadFileParts();
                } catch (error: any) {
                    console.error("Upload process error:", error);
                    onError(error);
                }
            };

            startUploads();
        }
    }, [uploadState.uploadInitStatus, fileParts.length, uploadFileParts, onError, uploadStarted]);

    // Separate effect to monitor upload status and trigger completion
    useEffect(() => {
        // Only run when upload status changes to completed
        if (
            uploadState.uploadStatus === "completed" &&
            uploadState.completionStatus === "pending"
        ) {
            console.log("Upload completed, starting completion process");

            // Call completeUpload directly without setTimeout
            const finishUpload = async () => {
                try {
                    console.log("Executing completeUpload...");
                    await completeUpload();
                } catch (error: any) {
                    console.error("Completion error:", error);
                    onError(error);
                }
            };

            finishUpload();
        }
    }, [uploadState.uploadStatus, uploadState.completionStatus, completeUpload, onError]);

    // Main upload process
    useEffect(() => {
        const performUpload = async () => {
            try {
                // Step 1: Create Asset (if not existing)
                let assetId = assetDetail.assetId || "";
                if (!isExistingAsset) {
                    const assetResponse = await createAsset();
                    if (assetResponse) {
                        assetId = assetResponse.assetId;

                        // Update state with the created asset ID
                        setUploadState((prev) => ({
                            ...prev,
                            createdAssetId: assetId,
                        }));

                        // Step 2: Add Metadata
                        await addMetadata(assetId);

                        // Step 2.5: Create Asset Links
                        await createAssetLinks(assetId);
                    }
                } else if (assetId) {
                    // For existing assets, make sure we set the createdAssetId in state
                    setUploadState((prev) => ({
                        ...prev,
                        createdAssetId: assetId,
                    }));
                }

                // Step 3a: Initialize Asset Files Upload (only if we have files)
                if (fileItems.length > 0) {
                    await initializeUpload(assetId);
                } else {
                    console.log(
                        "No asset files to upload, skipping asset file upload initialization"
                    );
                    setUploadState((prev) => ({
                        ...prev,
                        uploadInitStatus: "skipped",
                        uploadStatus: "skipped",
                        completionStatus: "skipped",
                    }));
                }

                // Step 3b: Initialize Preview File Upload (if applicable)
                // Only initialize preview upload if we have a valid asset ID
                if (assetDetail.Preview && assetId) {
                    // Ensure we have the latest assetId before initializing preview upload
                    console.log(`Initializing preview upload with confirmed assetId: ${assetId}`);
                    await initializePreviewUpload(assetId);
                }

                // If no files at all (neither asset files nor preview), complete the process
                if (fileItems.length === 0 && !assetDetail.Preview) {
                    console.log("No files to upload, completing asset creation process");
                    // Create a mock completion response for assets with no files
                    const mockResponse: CompleteUploadResponse = {
                        assetId: assetId,
                        message: "Asset created successfully without files",
                        uploadId: "no-upload-required",
                        fileResults: [],
                        overallSuccess: true,
                    };
                    onUploadComplete(mockResponse);
                }

                // Steps 4 and 5 (Upload File Parts and Complete Upload) are handled by the other useEffect
            } catch (error: any) {
                console.error("Upload process error:", error);
                onError(error);
            }
        };

        performUpload();
    }, []); // Run once on mount

    // Retry failed uploads
    const handleRetry = useCallback(async () => {
        setShowRetryButton(false);

        // Reset failed parts to pending
        setFileParts((prev) =>
            prev.map((part) => (part.status === "failed" ? { ...part, status: "pending" } : part))
        );

        // Reset failed files to queued
        setFileUploadItems((prev) =>
            prev.map((item) =>
                item.status === "Failed" ? { ...item, status: "Queued", progress: 0 } : item
            )
        );

        // Reset upload started flag to allow the upload to start again
        setUploadStarted(false);

        // Retry upload
        await uploadFileParts();

        // If upload is now successful, complete it
        if (uploadState.uploadStatus === "completed") {
            await completeUpload();
        }
    }, [uploadFileParts, completeUpload, uploadState.uploadStatus]);

    // Retry a single failed file
    const handleRetryItem = useCallback(
        async (fileIndex: number) => {
            // Reset all parts for this file to pending
            setFileParts((prev) =>
                prev.map((part) =>
                    part.fileIndex === fileIndex && part.status === "failed"
                        ? { ...part, status: "pending" }
                        : part
                )
            );

            // Reset the file status to queued
            setFileUploadItems((prev) =>
                prev.map((item) =>
                    item.index === fileIndex && item.status === "Failed"
                        ? { ...item, status: "Queued", progress: 0 }
                        : item
                )
            );

            // Reset upload started flag to allow the upload to start again
            setUploadStarted(false);

            // Retry upload
            await uploadFileParts();

            // If upload is now successful, complete it
            if (uploadState.uploadStatus === "completed") {
                await completeUpload();
            }
        },
        [uploadFileParts, completeUpload, uploadState.uploadStatus]
    );

    // Handle manual completion (with only successful files)
    const handleManualCompletion = useCallback(async () => {
        await completeUpload();
    }, [completeUpload]);

    // Handle retry from the last failed step
    const handleRetryFromStep = useCallback(async () => {
        try {
            // Reset upload started flag to allow the upload to start again
            setUploadStarted(false);

            // Determine which step failed and retry from there
            if (uploadState.assetCreationStatus === "failed") {
                // Retry from asset creation
                const assetResponse = await createAsset();
                if (assetResponse) {
                    const assetId = assetResponse.assetId;
                    await addMetadata(assetId);
                    await initializeUpload(assetId);
                    // uploadFileParts will be called by the useEffect
                }
            } else if (uploadState.metadataStatus === "failed") {
                // Retry from metadata
                const assetId = uploadState.createdAssetId || assetDetail.assetId || "";
                await addMetadata(assetId);
                await initializeUpload(assetId);
                // uploadFileParts will be called by the useEffect
            } else if (uploadState.uploadInitStatus === "failed") {
                // Retry from upload initialization
                const assetId = uploadState.createdAssetId || assetDetail.assetId || "";
                await initializeUpload(assetId);
                // uploadFileParts will be called by the useEffect
            } else if (uploadState.previewUploadInitStatus === "failed") {
                // Retry preview upload initialization
                const assetId = assetDetail.assetId || "";
                if (assetId && assetDetail.Preview) {
                    console.log(`Retrying preview upload initialization with assetId: ${assetId}`);
                    await initializePreviewUpload(assetId);
                }
            } else if (uploadState.uploadStatus === "failed") {
                // Retry file uploads
                await handleRetry();
            } else if (uploadState.completionStatus === "failed") {
                // Retry completion
                await completeUpload();
            } else if (uploadState.previewCompletionStatus === "failed") {
                // Retry preview completion if we have the necessary data
                if (uploadState.previewUploadId && assetDetail.Preview) {
                    // We need to re-upload the preview file
                    const assetId = assetDetail.assetId || "";
                    if (assetId) {
                        console.log(
                            `Retrying preview upload after completion failure with assetId: ${assetId}`
                        );
                        await initializePreviewUpload(assetId);
                    }
                }
            }
        } catch (error: any) {
            console.error("Retry error:", error);
            onError(error);
        }
    }, [
        uploadState.assetCreationStatus,
        uploadState.metadataStatus,
        uploadState.uploadInitStatus,
        uploadState.uploadStatus,
        uploadState.completionStatus,
        uploadState.previewUploadInitStatus,
        uploadState.previewCompletionStatus,
        uploadState.createdAssetId,
        uploadState.previewUploadId,
        assetDetail,
        createAsset,
        addMetadata,
        initializeUpload,
        initializePreviewUpload,
        uploadFileParts,
        completeUpload,
        handleRetry,
        onError,
    ]);

    return (
        <Container header={<Header variant="h2">Upload Progress</Header>}>
            <SpaceBetween direction="vertical" size="l">
                {uploadState.errors.length > 0 && (
                    <Alert type="error" header="Upload Errors">
                        <ul>
                            {uploadState.errors.map((error, index) => (
                                <li key={index}>
                                    <strong>{error.step}:</strong> {error.message}
                                </li>
                            ))}
                        </ul>
                    </Alert>
                )}

                {hasIncreasedPartSizes && (
                    <Alert type="warning" header="Increased Part Sizes">
                        Some files in this upload are very large and have been automatically
                        configured with increased part sizes to limit the number of parts per file
                        to {MAX_NUM_PARTS_PER_FILE}. This may result in larger individual upload
                        chunks but helps prevent initialization timeouts.
                    </Alert>
                )}

                {/* Retry and Back buttons for all steps that can fail */}
                {(uploadState.assetCreationStatus === "failed" ||
                    uploadState.metadataStatus === "failed" ||
                    uploadState.uploadInitStatus === "failed" ||
                    uploadState.previewUploadInitStatus === "failed" ||
                    uploadState.completionStatus === "failed" ||
                    uploadState.previewCompletionStatus === "failed") && (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={() => handleRetryFromStep()} variant="primary">
                            Retry from Failed Step
                        </Button>
                        {onCancel && <Button onClick={onCancel}>Back to Review</Button>}
                    </SpaceBetween>
                )}

                <SpaceBetween direction="vertical" size="m">
                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Asset Creation</Box>
                            <SpaceBetween direction="horizontal" size="xs">
                                <StatusIndicator
                                    type={getStatusIndicatorType(uploadState.assetCreationStatus)}
                                >
                                    {getStatusText(uploadState.assetCreationStatus)}
                                </StatusIndicator>
                                {uploadState.assetCreationStatus === "completed" &&
                                    uploadState.createdAssetId && (
                                        <Link
                                            href={`#/databases/${assetDetail.databaseId}/assets/${uploadState.createdAssetId}`}
                                        >
                                            {uploadState.createdAssetId}
                                        </Link>
                                    )}
                            </SpaceBetween>
                        </SpaceBetween>
                    </Box>

                    {uploadState.metadataStatus !== "skipped" && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Metadata</Box>
                                <StatusIndicator
                                    type={getStatusIndicatorType(uploadState.metadataStatus)}
                                >
                                    {getStatusText(uploadState.metadataStatus)}
                                </StatusIndicator>
                            </SpaceBetween>
                        </Box>
                    )}

                    {uploadState.assetLinksStatus !== "skipped" && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Asset Links Creation</Box>
                                <StatusIndicator
                                    type={getStatusIndicatorType(uploadState.assetLinksStatus)}
                                >
                                    {getStatusText(uploadState.assetLinksStatus)}
                                </StatusIndicator>
                                {uploadState.assetLinksStatus === "failed" &&
                                    uploadState.assetLinksErrors.length > 0 && (
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <Button
                                                onClick={() => {
                                                    // Retry asset links creation
                                                    const assetId =
                                                        uploadState.createdAssetId ||
                                                        assetDetail.assetId ||
                                                        "";
                                                    if (assetId) {
                                                        createAssetLinks(assetId);
                                                    }
                                                }}
                                            >
                                                Retry Asset Links
                                            </Button>
                                            <Button
                                                onClick={() => {
                                                    // Continue without asset links
                                                    setUploadState((prev) => ({
                                                        ...prev,
                                                        assetLinksStatus: "skipped",
                                                    }));
                                                }}
                                                variant="link"
                                            >
                                                Continue Without Failed Links
                                            </Button>
                                        </SpaceBetween>
                                    )}
                            </SpaceBetween>
                        </Box>
                    )}

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Asset Files Upload Initialization</Box>
                            <StatusIndicator
                                type={getStatusIndicatorType(uploadState.uploadInitStatus)}
                            >
                                {getStatusText(uploadState.uploadInitStatus)}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>

                    {uploadState.previewUploadInitStatus !== "skipped" && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">
                                    Preview File Upload Initialization
                                </Box>
                                <StatusIndicator
                                    type={getStatusIndicatorType(
                                        uploadState.previewUploadInitStatus
                                    )}
                                >
                                    {getStatusText(uploadState.previewUploadInitStatus)}
                                </StatusIndicator>
                            </SpaceBetween>
                        </Box>
                    )}

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">File Upload</Box>
                            <ProgressBar
                                value={uploadState.overallProgress}
                                status={getProgressBarStatus(uploadState.uploadStatus)}
                                label="Overall Upload Progress"
                                additionalInfo={`${completedParts}/${totalParts} parts completed`}
                            />
                        </SpaceBetween>
                    </Box>

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Asset Files Upload Completion</Box>
                            <StatusIndicator
                                type={getStatusIndicatorType(uploadState.completionStatus)}
                            >
                                {getStatusText(uploadState.completionStatus)}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>

                    {uploadState.previewCompletionStatus !== "skipped" && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Preview File Upload Completion</Box>
                                <StatusIndicator
                                    type={getStatusIndicatorType(
                                        uploadState.previewCompletionStatus
                                    )}
                                >
                                    {getStatusText(uploadState.previewCompletionStatus)}
                                </StatusIndicator>
                            </SpaceBetween>
                        </Box>
                    )}
                </SpaceBetween>

                <FileUploadTable
                    allItems={fileUploadItems}
                    resume={false}
                    showCount={true}
                    onRetry={handleRetry}
                    onRetryItem={handleRetryItem}
                />

                {showRetryButton && (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={handleRetry} variant="primary">
                            Retry Failed Uploads
                        </Button>
                        <Button onClick={handleManualCompletion}>
                            Continue and ignore failed files
                        </Button>
                    </SpaceBetween>
                )}

                {/* Manual completion button when uploads are completed but completion is pending */}
                {uploadState.uploadStatus === "completed" &&
                    uploadState.completionStatus === "pending" && (
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={handleManualCompletion} variant="primary">
                                Finalize Upload
                            </Button>
                        </SpaceBetween>
                    )}
            </SpaceBetween>
        </Container>
    );
}

// Helper functions
function getStatusIndicatorType(
    status: string
): "pending" | "loading" | "success" | "error" | "info" | "warning" | "stopped" {
    switch (status) {
        case "pending":
            return "pending";
        case "in-progress":
            return "loading";
        case "completed":
            return "success";
        case "partial":
            return "warning";
        case "failed":
            return "error";
        default:
            return "info";
    }
}

function getStatusText(status: string): string {
    switch (status) {
        case "pending":
            return "Pending";
        case "in-progress":
            return "In Progress";
        case "completed":
            return "Completed";
        case "partial":
            return "Completed with Errors";
        case "failed":
            return "Failed";
        default:
            return "Unknown";
    }
}

function getProgressBarStatus(status: string): "in-progress" | "success" | "error" {
    switch (status) {
        case "in-progress":
            return "in-progress";
        case "completed":
            return "success";
        case "failed":
            return "error";
        default:
            return "in-progress";
    }
}
