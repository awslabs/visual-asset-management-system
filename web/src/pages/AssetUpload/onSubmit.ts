/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { ProgressBarProps } from "@cloudscape-design/components";
import { NonCancelableCustomEvent } from "@cloudscape-design/components/interfaces";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";

import { Metadata } from "../../components/single/Metadata";
import { AssetDetail } from "./AssetUpload";
import { FileUploadTableItem } from "./FileUploadTable";
import {
    CompleteUploadResponse,
    CompleteFileUpload,
    UploadPartResult,
} from "../../services/AssetUploadService";
import { generateUUID } from "../../common/utils/utils";

export type ExecStatusType = Record<string, StatusIndicatorProps.Type>;

export interface UploadExecutionProps {
    assetDetail: AssetDetail;
    metadata: Metadata;
    setFreezeWizardButtons: (x: boolean) => void;
    setShowUploadAndExecProgress: (x: boolean) => void;
    execStatus: ExecStatusType;
    setExecStatus: (x: ExecStatusType | ((x: ExecStatusType) => ExecStatusType)) => void;
    moveToQueued: (index: number) => void;
    updateProgressForFileUploadItem: (index: number, loaded: number, total: number) => void;
    fileUploadComplete: (index: number, event: any) => void;
    fileUploadError: (index: number, event: any) => void;
    setPreviewUploadProgress: (x: ProgressBarProps) => void;
    setUploadExecutionProps: (x: UploadExecutionProps) => void;
}

export interface UploadAssetWorkflowApi {
    uploadAssetBody: any;
}

export interface GetCredentials {
    assetId: string;
    databaseId: string;
}

export interface ProgressCallbackArgs {
    loaded: number;
    total: number;
}

class OnSubmitProps {
    metadata!: Metadata;
    assetDetail!: AssetDetail;
    setFreezeWizardButtons!: (x: boolean) => void;
    setShowUploadAndExecProgress!: (x: boolean) => void;
    execStatus!: ExecStatusType;
    setExecStatus!: (x: ExecStatusType | ((x: ExecStatusType) => ExecStatusType)) => void;
    moveToQueued!: (index: number) => void;
    updateProgressForFileUploadItem!: (index: number, loaded: number, total: number) => void;
    fileUploadComplete!: (index: number, event: any) => void;
    fileUploadError!: (index: number, event: any) => void;
    setPreviewUploadProgress!: (x: ProgressBarProps) => void;
    setUploadExecutionProps!: (x: UploadExecutionProps) => void;
}

export function onUploadRetry(uploadExecutionProps: UploadExecutionProps) {
    console.log("Retrying uploads");
    window.onbeforeunload = function () {
        return "";
    };

    // Set the execution status to in-progress
    uploadExecutionProps.setExecStatus((prev) => ({
        ...prev,
        "Asset Details": "in-progress",
    }));

    // Reset all failed files to queued
    const failedItems =
        uploadExecutionProps.assetDetail.Asset?.filter((item) => item.status === "Failed") || [];
    failedItems.forEach((item) => {
        uploadExecutionProps.moveToQueued(item.index);
    });
}

/**
 * Lazy-loads the upload task promise function to handle file uploads
 * @param index File index
 * @param key S3 key
 * @param f File to upload
 * @param metadata Metadata for the upload
 * @param progressCallback Callback for upload progress
 * @param completeCallback Callback for upload completion
 * @param errorCallback Callback for upload errors
 * @returns Promise that resolves when upload is complete
 */
export function getUploadTaskPromiseLazy(
    index: number,
    key: string,
    f: File,
    metadata: { [p: string]: string } & GetCredentials,
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
): Promise<void> {
    // This is a new implementation that uses our AssetUploadService instead of the old S3 upload
    return new Promise(async (resolve, reject) => {
        try {
            // Import the AssetUploadService
            const { default: AssetUploadService } = await import(
                "../../services/AssetUploadService"
            );

            // Ensure we have a valid asset ID
            if (!metadata.assetId) {
                throw new Error("No asset ID available for preview upload");
            }

            console.log(`Starting preview file upload with assetId: ${metadata.assetId}`);

            // Initialize upload for a single preview file
            const uploadRequest = {
                assetId: metadata.assetId,
                databaseId: metadata.databaseId,
                uploadType: "assetPreview" as const,
                files: [
                    {
                        relativeKey: f.name,
                        file_size: f.size,
                    },
                ],
            };

            // Step 1: Initialize upload
            const initResponse = await AssetUploadService.initializeUpload(uploadRequest);
            const fileResponse = initResponse.files[0];

            // Step 2: Upload file parts
            const parts = [];
            let totalUploaded = 0;

            // Use the same part size as in UploadManager.tsx for consistency
            const partSize = 50 * 1024 * 1024; // 50MB parts

            for (let i = 0; i < fileResponse.numParts; i++) {
                const partNumber = i + 1;
                const partUrl = fileResponse.partUploadUrls.find(
                    (p) => p.PartNumber === partNumber
                )?.UploadUrl;

                if (!partUrl) {
                    throw new Error(`Missing presigned URL for part ${partNumber}`);
                }

                // Calculate part start and end
                const start = i * partSize;
                const end = Math.min(start + partSize, f.size);
                const blob = f.slice(start, end);

                // Upload the part
                const etag = await AssetUploadService.uploadPart(partUrl, blob);

                // Update progress
                totalUploaded += end - start;
                progressCallback(index, {
                    loaded: totalUploaded,
                    total: f.size,
                });

                // Add part to completion list
                parts.push({
                    PartNumber: partNumber,
                    ETag: etag,
                });
            }

            // Step 3: Complete upload
            const completionRequest = {
                assetId: metadata.assetId,
                databaseId: metadata.databaseId,
                uploadType: "assetPreview" as const,
                files: [
                    {
                        relativeKey: f.name,
                        uploadIdS3: fileResponse.uploadIdS3,
                        parts: parts,
                    },
                ],
            };

            console.log(
                "Sending preview completion request:",
                JSON.stringify(completionRequest, null, 2)
            );

            const completionResponse = await AssetUploadService.completeUpload(
                initResponse.uploadId,
                completionRequest
            );

            console.log(
                "Preview completion response:",
                JSON.stringify(completionResponse, null, 2)
            );

            // Call completion callback
            completeCallback(index, completionResponse);
            resolve();
        } catch (error) {
            console.error("Error uploading preview file:", error);
            errorCallback(index, error);
            reject(error);
        }
    });
}

/**
 * Creates upload promises for asset files
 * @param isMultiFile Whether multiple files are being uploaded
 * @param files Files to upload
 * @param keyPrefix Prefix for S3 keys
 * @param metadata Metadata for the upload
 * @param moveToQueued Callback to move a file to queued status
 * @param progressCallback Callback for upload progress
 * @param completeCallback Callback for upload completion
 * @param errorCallback Callback for upload errors
 * @returns Array of promises for file uploads
 */
export function createAssetUploadPromises(
    isMultiFile: boolean,
    files: FileUploadTableItem[],
    keyPrefix: string,
    metadata: { [p: string]: string } & GetCredentials,
    moveToQueued: (index: number) => void,
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
): Promise<void>[] {
    // This is a new implementation that uses our AssetUploadService instead of the old S3 upload
    const promises: Promise<void>[] = [];

    // Group files by status
    const queuedFiles = files.filter((file) => file.status === "Queued");
    const failedFiles = files.filter((file) => file.status === "Failed");

    // Process queued and failed files
    const filesToProcess = [...queuedFiles, ...failedFiles];

    if (filesToProcess.length === 0) {
        return promises;
    }

    // Create a single promise that handles all files
    promises.push(
        new Promise<void>(async (resolve, reject) => {
            try {
                // Import the AssetUploadService
                const { default: AssetUploadService } = await import(
                    "../../services/AssetUploadService"
                );

                // Step 1: Initialize upload for all files
                const uploadRequest = {
                    assetId: metadata.assetId,
                    databaseId: metadata.databaseId,
                    uploadType: "assetFile" as const,
                    files: filesToProcess.map((file) => ({
                        relativeKey: isMultiFile ? file.relativePath : file.name,
                        file_size: file.size,
                    })),
                };

                // Move all files to queued status
                filesToProcess.forEach((file) => {
                    moveToQueued(file.index);
                });

                // Initialize upload
                const initResponse = await AssetUploadService.initializeUpload(uploadRequest);

                // Step 2: Upload file parts
                const fileCompletions: CompleteFileUpload[] = [];
                const MAX_CONCURRENT_UPLOADS = 6;
                let activeUploads = 0;
                let completedFiles = 0;

                // Process each file
                for (let i = 0; i < filesToProcess.length; i++) {
                    const file = filesToProcess[i];
                    const fileResponse = initResponse.files[i];
                    const parts: UploadPartResult[] = [];

                    // Process each part
                    for (let j = 0; j < fileResponse.numParts; j++) {
                        const partNumber = j + 1;
                        const partUrl = fileResponse.partUploadUrls.find(
                            (p) => p.PartNumber === partNumber
                        )?.UploadUrl;

                        if (!partUrl) {
                            throw new Error(
                                `Missing presigned URL for part ${partNumber} of file ${file.name}`
                            );
                        }

                        // Wait if we've reached the concurrent upload limit
                        while (activeUploads >= MAX_CONCURRENT_UPLOADS) {
                            await new Promise((resolve) => setTimeout(resolve, 100));
                        }

                        // Upload the part
                        activeUploads++;

                        // Calculate part start and end
                        const partSize = 5 * 1024 * 1024; // 5MB parts
                        const start = j * partSize;
                        const end = Math.min(start + partSize, file.size);
                        const blob = await file.handle
                            .getFile()
                            .then((f: File) => f.slice(start, end));

                        // Upload the part and update progress
                        AssetUploadService.uploadPart(partUrl, blob)
                            .then((etag) => {
                                // Add part to completion list
                                parts.push({
                                    PartNumber: partNumber,
                                    ETag: etag,
                                });

                                // Update progress
                                const totalParts = fileResponse.numParts;
                                const completedParts = parts.length;
                                const progress = Math.round(
                                    (completedParts / totalParts) * file.size
                                );

                                progressCallback(file.index, {
                                    loaded: progress,
                                    total: file.size,
                                });

                                // Check if all parts are uploaded
                                if (completedParts === totalParts) {
                                    // Add to file completions
                                    fileCompletions.push({
                                        relativeKey: isMultiFile ? file.relativePath : file.name,
                                        uploadIdS3: fileResponse.uploadIdS3,
                                        parts: parts,
                                    });

                                    completedFiles++;

                                    // Call completion callback for this file
                                    completeCallback(file.index, { success: true });
                                }

                                activeUploads--;
                            })
                            .catch((error) => {
                                activeUploads--;
                                errorCallback(file.index, error);
                            });
                    }
                }

                // Wait for all uploads to complete
                while (completedFiles < filesToProcess.length) {
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }

                // Step 3: Complete upload
                const completionRequest = {
                    assetId: metadata.assetId,
                    databaseId: metadata.databaseId,
                    uploadType: "assetFile" as const,
                    files: fileCompletions,
                };

                await AssetUploadService.completeUpload(initResponse.uploadId, completionRequest);

                resolve();
            } catch (error) {
                reject(error);
            }
        })
    );

    return promises;
}

/**
 * Executes upload promises
 * @param promises Array of promises to execute
 * @returns Promise that resolves when all uploads are complete
 */
export function executeUploads(promises: Promise<void>[]): Promise<void> {
    return Promise.all(promises).then(() => {});
}

export default function onSubmit({
    assetDetail,
    setFreezeWizardButtons,
    metadata,
    execStatus,
    setExecStatus,
    setShowUploadAndExecProgress,
    moveToQueued,
    updateProgressForFileUploadItem,
    fileUploadComplete,
    fileUploadError,
    setPreviewUploadProgress,
    setUploadExecutionProps,
}: OnSubmitProps) {
    return async (detail: NonCancelableCustomEvent<{}>) => {
        setFreezeWizardButtons(true);

        // Check if we have required asset details (assetId and databaseId are required)
        if (assetDetail.assetId && assetDetail.databaseId) {
            // Initialize with empty status
            setExecStatus({});

            // Set window beforeunload handler to prevent accidental navigation
            window.onbeforeunload = function () {
                return "";
            };

            // Show the upload progress screen
            setShowUploadAndExecProgress(true);

            // Create upload execution props
            const uploadExecutionProps: UploadExecutionProps = {
                assetDetail,
                metadata,
                setFreezeWizardButtons,
                setShowUploadAndExecProgress,
                execStatus,
                setExecStatus,
                moveToQueued,
                updateProgressForFileUploadItem,
                fileUploadComplete,
                fileUploadError,
                setPreviewUploadProgress,
                setUploadExecutionProps,
            };

            // Store the upload execution props for potential retry
            setUploadExecutionProps(uploadExecutionProps);

            // Handle upload completion
            const handleUploadComplete = (response: CompleteUploadResponse) => {
                console.log("Upload completed:", response);

                // Update execution status
                setExecStatus((prev) => ({
                    ...prev,
                    "Asset Details": "success",
                }));

                // Remove window beforeunload handler
                window.onbeforeunload = null;
            };

            // Handle upload error
            const handleUploadError = (error: Error) => {
                console.error("Upload error:", error);

                // Update execution status
                setExecStatus((prev) => ({
                    ...prev,
                    "Asset Details": "error",
                }));

                // Remove window beforeunload handler
                window.onbeforeunload = null;
            };
        } else {
            console.log("Asset detail not right - missing required fields");
            console.log(assetDetail);
            setFreezeWizardButtons(false);
        }
    };
}
