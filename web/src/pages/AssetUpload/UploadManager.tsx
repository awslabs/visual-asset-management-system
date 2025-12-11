/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback, useRef } from "react";
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
    Popover,
} from "@cloudscape-design/components";
import { FileUploadTableItem, FileUploadTable } from "./FileUploadTable";
import { CompleteUploadResponse } from "../../services/AssetUploadService";
import { Metadata } from "../../components/single/Metadata";
import { AssetDetail } from "./AssetUpload";
import {
    FileInfo,
    createUploadSequences,
    needsMultiSequenceUpload,
    getUploadSummary,
} from "../../utils/uploadSequencer";
import { useMultiSequenceUpload } from "./hooks/useMultiSequenceUpload";
import { useFilePartsUpload } from "./hooks/useFilePartsUpload";
import { useAssetOperations } from "./hooks/useAssetOperations";
import { formatRetryMessage } from "../../utils/uploadRetry";

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
    uploadStatus: "pending" | "in-progress" | "completed" | "failed" | "skipped";
    completionStatus: "pending" | "in-progress" | "completed" | "failed" | "partial" | "skipped";
    createdAssetId?: string;
    errors: { step: string; message: string }[];
    overallProgress: number;
    finalCompletionTriggered: boolean;
    largeFileAsynchronousHandling: boolean;
    has503Warnings: boolean;
}

export default function UploadManager({
    assetDetail,
    metadata,
    fileItems,
    onUploadComplete,
    onError,
    isExistingAsset = false,
    onCancel,
}: UploadManagerProps) {
    // State
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
        uploadStatus: fileItems.length > 0 ? "pending" : "skipped",
        completionStatus: fileItems.length > 0 ? "pending" : "skipped",
        errors: [],
        overallProgress: 0,
        finalCompletionTriggered: false,
        largeFileAsynchronousHandling: false,
        has503Warnings: false,
    });

    const [fileUploadItems, setFileUploadItems] = useState<FileUploadTableItem[]>(fileItems);
    const [isMultiSequence, setIsMultiSequence] = useState(false);
    const [totalSequences, setTotalSequences] = useState(0);
    const [completedInitSequences, setCompletedInitSequences] = useState(0);
    const [completedCompleteSequences, setCompletedCompleteSequences] = useState(0);
    const [retryMessage, setRetryMessage] = useState<string | null>(null);
    const [isRateLimitRetry, setIsRateLimitRetry] = useState(false);
    const [uploadSequences, setUploadSequences] = useState<any[]>([]);
    const [sequenceInitResults, setSequenceInitResults] = useState<any[]>([]);
    const [cancelledFiles, setCancelledFiles] = useState<Set<number>>(new Set());
    const [completingSequences, setCompletingSequences] = useState<Set<number>>(new Set());

    // Ref to store the completion handler so it's accessible in callbacks
    const handleSequenceCompletionRef = useRef<((sequenceId: number) => Promise<void>) | null>(
        null
    );

    // Custom hooks
    const { createAsset, addMetadata, createAssetLinks } = useAssetOperations();
    const {
        sequenceUploadIds,
        sequenceInitStatuses,
        sequenceCompleteStatuses,
        initializeSequence,
        completeSequence,
        createFilePartsFromSequences,
    } = useMultiSequenceUpload();
    const {
        fileParts,
        completedParts,
        totalParts,
        setFileParts,
        setTotalParts,
        uploadFileParts,
        retryFailedParts,
        cancelFileParts,
    } = useFilePartsUpload();

    // Handle completion of individual sequences in parallel
    const handleSequenceCompletion = useCallback(
        async (sequenceId: number) => {
            console.log(`handleSequenceCompletion called for sequence ${sequenceId}`);

            // Prevent duplicate completion attempts
            if (completingSequences.has(sequenceId)) {
                console.log(`Sequence ${sequenceId} already being completed, skipping`);
                return;
            }

            const sequence = uploadSequences.find((s) => s.sequenceId === sequenceId);
            if (!sequence) {
                console.error(`Sequence ${sequenceId} not found`);
                return;
            }

            // Check if this is a preview file sequence
            const isPreviewSequence = sequence.files.some((f: any) => f.isPreviewFile);

            if (isPreviewSequence) {
                // For preview sequences, wait for all regular file sequences to complete first
                const regularSequences = uploadSequences.filter(
                    (s) => !s.files.some((f: any) => f.isPreviewFile)
                );

                if (regularSequences.length > 0) {
                    const allRegularSequencesCompleted = regularSequences.every(
                        (s) => sequenceCompleteStatuses.get(s.sequenceId) === "completed"
                    );

                    if (!allRegularSequencesCompleted) {
                        console.log(
                            `Preview sequence ${sequenceId} waiting for regular sequences to complete first`
                        );
                        // Re-check in 1 second
                        setTimeout(() => {
                            if (handleSequenceCompletionRef.current) {
                                handleSequenceCompletionRef.current(sequenceId);
                            }
                        }, 1000);
                        return;
                    }
                }
            }

            setCompletingSequences((prev) => new Set(prev).add(sequenceId));

            try {
                const uploadId = sequenceUploadIds.get(sequenceId);
                if (!uploadId) {
                    console.error(`No uploadId found for sequence ${sequenceId}`);
                    return;
                }

                const initResult = sequenceInitResults.find(
                    (r: any) => r.sequenceId === sequenceId
                );
                if (!initResult) {
                    console.error(`No init result found for sequence ${sequenceId}`);
                    return;
                }

                const assetId = uploadState.createdAssetId || assetDetail.assetId || "";
                console.log(`Completing sequence ${sequenceId} with uploadId ${uploadId}`);

                const result = await completeSequence(
                    sequence,
                    uploadId,
                    assetId,
                    assetDetail.databaseId || "",
                    fileParts,
                    fileUploadItems,
                    initResult.response,
                    (retryCount, error, backoffMs) => {
                        const { message, isRateLimit } = formatRetryMessage(
                            `Sequence ${sequenceId} completion`,
                            retryCount,
                            error,
                            backoffMs
                        );
                        setRetryMessage(message);
                        setIsRateLimitRetry(isRateLimit);
                    },
                    cancelledFiles
                );

                if (result && typeof result === "object") {
                    if ("is503" in result) {
                        setUploadState((prev) => ({ ...prev, has503Warnings: true }));
                    } else if ("largeFileAsynchronousHandling" in result) {
                        if (result.largeFileAsynchronousHandling === true) {
                            setUploadState((prev) => ({
                                ...prev,
                                largeFileAsynchronousHandling: true,
                            }));
                        }
                    }
                }

                setCompletedCompleteSequences((prev) => prev + 1);
                setRetryMessage(null);
                console.log(`Sequence ${sequenceId} completed successfully`);
            } catch (error: any) {
                console.error(`Failed to complete sequence ${sequenceId}:`, error);
                setRetryMessage(null);
                setUploadState((prev) => ({
                    ...prev,
                    errors: [
                        ...prev.errors,
                        {
                            step: `Sequence ${sequenceId} Completion`,
                            message: error.message,
                        },
                    ],
                }));
            }
        },
        [
            completingSequences,
            uploadSequences,
            sequenceUploadIds,
            sequenceInitResults,
            uploadState.createdAssetId,
            assetDetail.assetId,
            assetDetail.databaseId,
            fileParts,
            fileUploadItems,
            completeSequence,
            cancelledFiles,
            sequenceCompleteStatuses,
        ]
    );

    // Store the handler in ref so it's accessible in callbacks
    handleSequenceCompletionRef.current = handleSequenceCompletion;

    // Main upload process
    useEffect(() => {
        const performUpload = async () => {
            try {
                // Step 1: Create Asset (if not existing)
                let assetId = assetDetail.assetId || "";
                if (!isExistingAsset && assetDetail.databaseId) {
                    setUploadState((prev) => ({ ...prev, assetCreationStatus: "in-progress" }));
                    const assetResponse = await createAsset(assetDetail);
                    assetId = assetResponse.assetId;
                    setUploadState((prev) => ({
                        ...prev,
                        assetCreationStatus: "completed",
                        createdAssetId: assetId,
                    }));

                    // Step 2: Add Metadata
                    if (Object.keys(metadata).length > 0) {
                        setUploadState((prev) => ({ ...prev, metadataStatus: "in-progress" }));
                        await addMetadata(assetDetail.databaseId, assetId, metadata);
                        setUploadState((prev) => ({ ...prev, metadataStatus: "completed" }));
                    }

                    // Step 2.5: Create Asset Links
                    if (
                        assetDetail.assetLinksFe &&
                        (assetDetail.assetLinksFe.parents?.length ||
                            assetDetail.assetLinksFe.child?.length ||
                            assetDetail.assetLinksFe.related?.length)
                    ) {
                        setUploadState((prev) => ({ ...prev, assetLinksStatus: "in-progress" }));
                        const linkResult = await createAssetLinks(assetId, assetDetail);
                        if (linkResult.success) {
                            setUploadState((prev) => ({ ...prev, assetLinksStatus: "completed" }));
                        } else {
                            setUploadState((prev) => ({
                                ...prev,
                                assetLinksStatus: "failed",
                                errors: [
                                    ...prev.errors,
                                    ...linkResult.errors.map((err) => ({
                                        step: "Asset Links",
                                        message: err,
                                    })),
                                ],
                            }));
                        }
                    }
                } else if (assetId) {
                    setUploadState((prev) => ({ ...prev, createdAssetId: assetId }));
                }

                // Step 3: Initialize Upload Sequences
                if (fileItems.length > 0 && assetId) {
                    // Convert fileItems to FileInfo
                    const fileInfos: FileInfo[] = fileItems.map((item, index) => ({
                        index: item.index, // Use original index to preserve special indices like 99999
                        name: item.name,
                        size: item.size,
                        relativePath: item.relativePath,
                        handle: item.handle,
                        isPreviewFile: item.relativePath.includes(".previewFile."),
                        isAssetPreview: item.index === 99999, // Mark asset preview files
                    }));

                    // Create sequences and store in state
                    const sequences = createUploadSequences(fileInfos);
                    setUploadSequences(sequences);

                    const isMulti = needsMultiSequenceUpload(fileInfos);
                    setIsMultiSequence(isMulti);
                    setTotalSequences(sequences.length);

                    console.log(
                        `Created ${sequences.length} upload sequences (multi-sequence: ${isMulti})`
                    );

                    // Initialize all sequences
                    setUploadState((prev) => ({ ...prev, uploadInitStatus: "in-progress" }));
                    const initResults = [];

                    for (const sequence of sequences) {
                        try {
                            const result = await initializeSequence(
                                sequence,
                                assetId,
                                assetDetail.databaseId || "",
                                fileUploadItems,
                                (retryCount, error, backoffMs) => {
                                    // Show retry message
                                    const { message, isRateLimit } = formatRetryMessage(
                                        `Sequence ${sequence.sequenceId} initialization`,
                                        retryCount,
                                        error,
                                        backoffMs
                                    );
                                    setRetryMessage(message);
                                    setIsRateLimitRetry(isRateLimit);
                                }
                            );
                            initResults.push(result);
                            setCompletedInitSequences((prev) => prev + 1);
                            setRetryMessage(null); // Clear retry message on success
                        } catch (error: any) {
                            console.error(
                                `Failed to initialize sequence ${sequence.sequenceId}:`,
                                error
                            );
                            setRetryMessage(null); // Clear retry message on final failure
                            setUploadState((prev) => ({
                                ...prev,
                                uploadInitStatus: "failed",
                                errors: [
                                    ...prev.errors,
                                    {
                                        step: "Upload Initialization",
                                        message: error.message,
                                    },
                                ],
                            }));
                            throw error;
                        }
                    }

                    // Store init results in state
                    setSequenceInitResults(initResults);
                    setUploadState((prev) => ({ ...prev, uploadInitStatus: "completed" }));

                    // Create file parts from all init results
                    const allParts = createFilePartsFromSequences(
                        sequences,
                        initResults,
                        fileUploadItems
                    );

                    console.log(
                        `Created ${allParts.length} file parts from ${sequences.length} sequences`
                    );
                    console.log(`Sample parts:`, allParts.slice(0, 3));

                    // Set file parts and total parts synchronously
                    setFileParts(allParts);
                    setTotalParts(allParts.length);

                    // Mark zero-byte files as completed
                    setFileUploadItems((prev) =>
                        prev.map((item) =>
                            item.size === 0
                                ? {
                                      ...item,
                                      status: "Completed",
                                      progress: 100,
                                      loaded: item.total,
                                  }
                                : item
                        )
                    );

                    // Wait a tick to ensure state is updated
                    await new Promise((resolve) => setTimeout(resolve, 100));

                    // Step 4: Upload File Parts with sequence completion callback
                    if (allParts.length > 0) {
                        console.log(`Starting upload of ${allParts.length} parts`);
                        setUploadState((prev) => ({
                            ...prev,
                            uploadStatus: "in-progress",
                            completionStatus: "in-progress",
                        }));

                        const uploadResult = await uploadFileParts(
                            fileUploadItems,
                            allParts,
                            (completed, total) => {
                                const progress = Math.round((completed / total) * 100);
                                console.log(
                                    `Upload progress: ${completed}/${total} parts (${progress}%)`
                                );
                                setUploadState((prev) => ({ ...prev, overallProgress: progress }));
                            },
                            (fileIndex, progress) => {
                                setFileUploadItems((prev) =>
                                    prev.map((item) =>
                                        item.index === fileIndex
                                            ? {
                                                  ...item,
                                                  progress,
                                                  loaded: Math.round((progress * item.total) / 100),
                                                  status:
                                                      progress === 100
                                                          ? "Completed"
                                                          : progress > 0
                                                          ? "In Progress"
                                                          : "Queued",
                                              }
                                            : item
                                    )
                                );
                            },
                            // Sequence completion callback - triggers parallel completion
                            (sequenceId) => {
                                console.log(
                                    `Sequence ${sequenceId} parts completed, triggering completion`
                                );
                                // Trigger completion for this sequence asynchronously using ref
                                if (handleSequenceCompletionRef.current) {
                                    handleSequenceCompletionRef.current(sequenceId);
                                }
                            }
                        );

                        console.log(`Upload result:`, uploadResult);

                        if (uploadResult.success) {
                            console.log(`All parts uploaded successfully`);
                            setUploadState((prev) => ({ ...prev, uploadStatus: "completed" }));
                        } else {
                            console.error(
                                `Upload failed with ${uploadResult.failedCount} failed parts`
                            );
                            setUploadState((prev) => ({
                                ...prev,
                                uploadStatus: "failed",
                                errors: [
                                    ...prev.errors,
                                    {
                                        step: "File Upload",
                                        message: `${uploadResult.failedCount} file parts failed to upload`,
                                    },
                                ],
                            }));
                        }
                    } else {
                        // No parts to upload (all zero-byte files)
                        console.log(`No parts to upload (all zero-byte files)`);
                        setUploadState((prev) => ({
                            ...prev,
                            uploadStatus: "completed",
                            completionStatus: "in-progress",
                        }));
                        // Trigger completion for all sequences
                        if (handleSequenceCompletionRef.current) {
                            uploadSequences.forEach((seq) =>
                                handleSequenceCompletionRef.current!(seq.sequenceId)
                            );
                        }
                    }
                } else if (fileItems.length === 0) {
                    // No files to upload
                    const mockResponse: CompleteUploadResponse = {
                        assetId,
                        message: "Asset created successfully without files",
                        uploadId: "no-upload-required",
                        fileResults: [],
                        overallSuccess: true,
                    };
                    setUploadState((prev) => ({ ...prev, finalCompletionTriggered: true }));
                    onUploadComplete(mockResponse);
                }
            } catch (error: any) {
                console.error("Upload process error:", error);
                onError(error);
            }
        };

        performUpload();
    }, []); // Run once on mount - handleSequenceCompletion is stable via useCallback

    // Monitor for all sequences completed
    useEffect(() => {
        if (
            uploadSequences.length > 0 &&
            completedCompleteSequences === uploadSequences.length &&
            !uploadState.finalCompletionTriggered
        ) {
            console.log("All sequences completed, triggering final completion");

            setUploadState((prev) => ({
                ...prev,
                completionStatus: "completed",
                finalCompletionTriggered: true,
            }));

            const firstUploadId = sequenceUploadIds.values().next().value;
            const finalResponse: CompleteUploadResponse = {
                assetId: uploadState.createdAssetId || assetDetail.assetId || "",
                message:
                    cancelledFiles.size > 0
                        ? `Upload completed with ${cancelledFiles.size} file(s) cancelled`
                        : "Upload completed successfully",
                uploadId: firstUploadId || "no-upload-required",
                fileResults: [],
                overallSuccess: true,
                largeFileAsynchronousHandling: uploadState.largeFileAsynchronousHandling,
            };
            onUploadComplete(finalResponse);
        }
    }, [
        completedCompleteSequences,
        uploadSequences.length,
        uploadState.finalCompletionTriggered,
        uploadState.createdAssetId,
        uploadState.largeFileAsynchronousHandling,
        assetDetail.assetId,
        sequenceUploadIds,
        cancelledFiles.size,
        onUploadComplete,
    ]);

    // State for cancel confirmation
    const [cancelConfirmFileIndex, setCancelConfirmFileIndex] = useState<number | null>(null);

    // Handle file cancellation with confirmation
    const handleCancelFile = useCallback((fileIndex: number) => {
        setCancelConfirmFileIndex(fileIndex);
    }, []);

    // Confirm file cancellation
    const confirmCancelFile = useCallback(
        (fileIndex: number) => {
            console.log(`Cancelling file at index ${fileIndex}`);

            // Add to cancelled files set
            setCancelledFiles((prev) => new Set(prev).add(fileIndex));

            // Cancel all parts for this file
            cancelFileParts(fileIndex);

            // Update file item status
            setFileUploadItems((prev) =>
                prev.map((item) =>
                    item.index === fileIndex
                        ? { ...item, status: "Cancelled" as any, progress: 0 }
                        : item
                )
            );

            // Clear confirmation state
            setCancelConfirmFileIndex(null);
        },
        [cancelFileParts]
    );

    // Retry handler
    const handleRetry = useCallback(async () => {
        retryFailedParts();

        // Re-trigger upload with current fileParts
        const uploadResult = await uploadFileParts(
            fileUploadItems,
            fileParts,
            (completed, total) => {
                const progress = Math.round((completed / total) * 100);
                setUploadState((prev) => ({ ...prev, overallProgress: progress }));
            },
            (fileIndex, progress) => {
                setFileUploadItems((prev) =>
                    prev.map((item) =>
                        item.index === fileIndex
                            ? {
                                  ...item,
                                  progress,
                                  loaded: Math.round((progress * item.total) / 100),
                                  status:
                                      progress === 100
                                          ? "Completed"
                                          : progress > 0
                                          ? "In Progress"
                                          : "Queued",
                              }
                            : item
                    )
                );
            },
            (sequenceId) => {
                if (handleSequenceCompletionRef.current) {
                    handleSequenceCompletionRef.current(sequenceId);
                }
            }
        );

        if (uploadResult.success) {
            setUploadState((prev) => ({ ...prev, uploadStatus: "completed" }));
        }
    }, [retryFailedParts, uploadFileParts, fileUploadItems, fileParts]);

    // Retry handler for individual file
    const handleRetryFile = useCallback(
        async (fileIndex: number) => {
            console.log(`Retrying file at index ${fileIndex}`);
            retryFailedParts(fileIndex);

            // Update file status
            setFileUploadItems((prev) =>
                prev.map((item) =>
                    item.index === fileIndex ? { ...item, status: "Queued", progress: 0 } : item
                )
            );

            // Re-trigger upload
            await handleRetry();
        },
        [retryFailedParts, handleRetry]
    );

    // Count failed files
    const failedFilesCount = fileUploadItems.filter((item) => item.status === "Failed").length;

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

                {cancelledFiles.size > 0 && uploadState.completionStatus === "completed" && (
                    <Alert type="warning" header="Files Cancelled">
                        {cancelledFiles.size} file(s) were cancelled during upload. The remaining{" "}
                        {fileItems.length - cancelledFiles.size} file(s) uploaded successfully.
                    </Alert>
                )}

                {(uploadState.largeFileAsynchronousHandling || uploadState.has503Warnings) && (
                    <Alert type="warning" header="Extended Processing Time">
                        The service returned some responses indicating longer processing times. Your
                        files may not be immediately available yet and may take a few minutes to
                        process. Please check the asset's file manager shortly to verify your files.
                    </Alert>
                )}

                {isMultiSequence && totalSequences > 1 && (
                    <Alert type="info" header="Multi-Sequence Upload">
                        Your files are being uploaded in {totalSequences} separate batches. This is
                        handled automatically.
                    </Alert>
                )}

                {retryMessage && (
                    <Alert
                        type={isRateLimitRetry ? "info" : "warning"}
                        header={isRateLimitRetry ? "Upload Pacing" : "Retrying Operation"}
                    >
                        {retryMessage}
                    </Alert>
                )}

                <SpaceBetween direction="vertical" size="m">
                    {!isExistingAsset && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Asset Creation</Box>
                                <SpaceBetween direction="horizontal" size="xs">
                                    <StatusIndicator
                                        type={getStatusIndicatorType(
                                            uploadState.assetCreationStatus
                                        )}
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
                    )}

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
                            </SpaceBetween>
                        </Box>
                    )}

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Upload Initialization</Box>
                            <StatusIndicator
                                type={getStatusIndicatorType(uploadState.uploadInitStatus)}
                            >
                                {getStatusText(uploadState.uploadInitStatus)}
                                {isMultiSequence &&
                                    uploadState.uploadInitStatus === "in-progress" &&
                                    ` (${completedInitSequences}/${totalSequences} sequences)`}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>

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
                            <Box variant="awsui-key-label">Upload Completion</Box>
                            <StatusIndicator
                                type={getStatusIndicatorType(uploadState.completionStatus)}
                            >
                                {getStatusText(uploadState.completionStatus)}
                                {isMultiSequence &&
                                    uploadState.completionStatus === "in-progress" &&
                                    ` (${completedCompleteSequences}/${totalSequences} sequences)`}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>
                </SpaceBetween>

                {uploadState.uploadStatus === "failed" && (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={handleRetry} variant="primary">
                            Retry Failed Uploads
                        </Button>
                        {onCancel && <Button onClick={onCancel}>Back to Review</Button>}
                    </SpaceBetween>
                )}

                <FileUploadTable
                    allItems={fileUploadItems}
                    resume={false}
                    showCount={true}
                    onRetry={failedFilesCount > 0 ? handleRetry : undefined}
                    onRetryItem={handleRetryFile}
                    onCancelItem={handleCancelFile}
                    cancelConfirmFileIndex={cancelConfirmFileIndex}
                    onConfirmCancel={confirmCancelFile}
                    onDismissCancel={() => setCancelConfirmFileIndex(null)}
                />
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
