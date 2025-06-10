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
    AssetLinks,
} from "../../services/AssetUploadService";
import { Metadata } from "../../components/single/Metadata";
import { AssetDetail } from "../AssetUpload";

// Maximum number of concurrent uploads
const MAX_CONCURRENT_UPLOADS = 6;
// Maximum size of each part in bytes (50MB)
const MAX_PART_SIZE = 50 * 1024 * 1024;

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
    assetCreationStatus: 'pending' | 'in-progress' | 'completed' | 'failed';
    metadataStatus: 'pending' | 'in-progress' | 'completed' | 'failed';
    uploadInitStatus: 'pending' | 'in-progress' | 'completed' | 'failed';
    previewUploadInitStatus: 'pending' | 'in-progress' | 'completed' | 'failed' | 'skipped';
    uploadStatus: 'pending' | 'in-progress' | 'completed' | 'failed';
    completionStatus: 'pending' | 'in-progress' | 'completed' | 'failed';
    previewCompletionStatus: 'pending' | 'in-progress' | 'completed' | 'failed' | 'skipped';
    createdAssetId?: string;
    uploadId?: string;
    previewUploadId?: string;
    errors: { step: string; message: string }[];
    overallProgress: number;
}

interface FilePart {
    fileIndex: number;
    partNumber: number;
    start: number;
    end: number;
    uploadUrl: string;
    status: 'pending' | 'in-progress' | 'completed' | 'failed';
    etag?: string;
    retryCount: number;
}

// Helper function to convert AssetDetail.assetLinks to AssetUploadService.AssetLinks
function convertAssetLinks(links?: {
    parents?: any[];
    child?: any[];
    related?: any[];
}): AssetLinks | undefined {
    if (!links) return undefined;
    
    return {
        parents: Array.isArray(links.parents) ? links.parents.map(p => typeof p === 'string' ? p : p.assetId) : [],
        child: Array.isArray(links.child) ? links.child.map(c => typeof c === 'string' ? c : c.assetId) : [],
        related: Array.isArray(links.related) ? links.related.map(r => typeof r === 'string' ? r : r.assetId) : []
    };
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
        assetCreationStatus: isExistingAsset ? 'completed' : 'pending',
        metadataStatus: isExistingAsset ? 'completed' : 'pending',
        uploadInitStatus: 'pending',
        previewUploadInitStatus: assetDetail.Preview ? 'pending' : 'skipped',
        uploadStatus: 'pending',
        completionStatus: 'pending',
        previewCompletionStatus: assetDetail.Preview ? 'pending' : 'skipped',
        errors: [],
        overallProgress: 0,
    });

    const [fileParts, setFileParts] = useState<FilePart[]>([]);
    const [fileUploadItems, setFileUploadItems] = useState<FileUploadTableItem[]>(fileItems);
    const [uploadResponse, setUploadResponse] = useState<InitializeUploadResponse | null>(null);
    const [activeUploads, setActiveUploads] = useState(0);
    const [completedParts, setCompletedParts] = useState(0);
    const [totalParts, setTotalParts] = useState(0);
    const [showRetryButton, setShowRetryButton] = useState(false);
    const [uploadStarted, setUploadStarted] = useState(false);

    // Step 1: Create Asset
    const createAsset = useCallback(async () => {
        if (isExistingAsset || !assetDetail.databaseId) {
            return;
        }

        try {
            setUploadState(prev => ({ ...prev, assetCreationStatus: 'in-progress' }));

            const assetData = {
                assetName: assetDetail.assetName || assetDetail.assetId || '',
                databaseId: assetDetail.databaseId,
                description: assetDetail.description || '',
                isDistributable: assetDetail.isDistributable || false,
                tags: assetDetail.tags || [],
                assetLinks: convertAssetLinks(assetDetail.assetLinks),
            };

            const response = await AssetUploadService.createAsset(assetData);
            
            setUploadState(prev => ({
                ...prev,
                assetCreationStatus: 'completed',
                createdAssetId: response.assetId,
            }));

            return response;
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                assetCreationStatus: 'failed',
                errors: [...prev.errors, { step: 'Asset Creation', message: error.message || 'Failed to create asset' }],
            }));
            throw error;
        }
    }, [assetDetail, isExistingAsset]);

    // Step 2: Add Metadata
    const addMetadata = useCallback(async (assetId: string) => {
        if (isExistingAsset || !assetDetail.databaseId) {
            return;
        }

        try {
            setUploadState(prev => ({ ...prev, metadataStatus: 'in-progress' }));

            await AssetUploadService.addMetadata(
                assetDetail.databaseId,
                assetId,
                metadata
            );

            setUploadState(prev => ({ ...prev, metadataStatus: 'completed' }));
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                metadataStatus: 'failed',
                errors: [...prev.errors, { step: 'Metadata', message: error.message || 'Failed to add metadata' }],
            }));
            throw error;
        }
    }, [assetDetail, metadata, isExistingAsset]);

    // Step 3: Initialize Upload
    const initializeUpload = useCallback(async (assetId: string) => {
        try {
            setUploadState(prev => ({ ...prev, uploadInitStatus: 'in-progress' }));

            // Prepare file information for upload initialization
            const files = await Promise.all(
                fileUploadItems.map(async (item) => {
                    const file = await item.handle.getFile();
                    // Use the item's relativePath which already includes the keyPrefix if it was set
                    return {
                        relativeKey: item.relativePath,
                        file_size: file.size,
                    };
                })
            );

            const uploadRequest = {
                assetId,
                databaseId: assetDetail.databaseId || '',
                uploadType: "assetFile" as const,
                files,
            };

            const response = await AssetUploadService.initializeUpload(uploadRequest);
            setUploadResponse(response);

            // Prepare file parts for upload
            const allParts: FilePart[] = [];
            let totalPartsCount = 0;

            response.files.forEach((file, fileIndex) => {
                file.partUploadUrls.forEach(part => {
                    allParts.push({
                        fileIndex,
                        partNumber: part.PartNumber,
                        start: (part.PartNumber - 1) * MAX_PART_SIZE,
                        end: Math.min(part.PartNumber * MAX_PART_SIZE, fileUploadItems[fileIndex].size),
                        uploadUrl: part.UploadUrl,
                        status: 'pending',
                        retryCount: 0,
                    });
                    totalPartsCount++;
                });
            });

            setFileParts(allParts);
            setTotalParts(totalPartsCount);
            setUploadState(prev => ({
                ...prev,
                uploadInitStatus: 'completed',
                uploadId: response.uploadId,
            }));

            return response;
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                uploadInitStatus: 'failed',
                errors: [...prev.errors, { step: 'Upload Initialization', message: error.message || 'Failed to initialize upload' }],
            }));
            throw error;
        }
    }, [assetDetail, fileUploadItems]);
    
    // Step 3b: Initialize Preview File Upload
    const initializePreviewUpload = useCallback(async (assetId: string) => {
        // Skip if no preview file
        if (!assetDetail.Preview) {
            console.log('No preview file to upload, skipping preview upload initialization');
            setUploadState(prev => ({
                ...prev,
                previewUploadInitStatus: 'skipped',
                previewCompletionStatus: 'skipped'
            }));
            return;
        }
        
        // Make sure we have a valid asset ID
        const validAssetId = uploadState.createdAssetId || assetId;
        if (!validAssetId) {
            console.error('No asset ID available for preview upload initialization');
            setUploadState(prev => ({
                ...prev,
                previewUploadInitStatus: 'failed',
                errors: [...prev.errors, { step: 'Preview Upload Initialization', message: 'No asset ID available for preview upload initialization' }],
            }));
            return;
        }

        try {
            setUploadState(prev => ({ ...prev, previewUploadInitStatus: 'in-progress' }));

            // Prepare file information for preview upload initialization
            const previewFile = assetDetail.Preview;
            
            console.log(`Initializing preview upload with assetId: ${validAssetId}`);
            
            const uploadRequest = {
                assetId: validAssetId,
                databaseId: assetDetail.databaseId || '',
                uploadType: "assetPreview" as const,
                files: [{
                    relativeKey: previewFile.name,
                    file_size: previewFile.size,
                }],
            };

            const response = await AssetUploadService.initializeUpload(uploadRequest);
            
            // Store the preview upload ID and ensure the asset ID is set
            setUploadState(prev => ({
                ...prev,
                previewUploadInitStatus: 'completed',
                previewUploadId: response.uploadId,
                createdAssetId: validAssetId // Ensure the asset ID is set
            }));

            // Store the asset ID in state before uploading the preview file
            console.log(`About to upload preview file with assetId: ${validAssetId}`);
            
            // Upload the preview file directly
            await uploadPreviewFile(response, previewFile);

            return response;
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                previewUploadInitStatus: 'failed',
                errors: [...prev.errors, { step: 'Preview Upload Initialization', message: error.message || 'Failed to initialize preview upload' }],
            }));
            throw error;
        }
    }, [assetDetail]);
    
    // Upload preview file
    const uploadPreviewFile = useCallback(async (initResponse: InitializeUploadResponse, previewFile: File) => {
        try {
            // Get the assetId from state
            const assetId = uploadState.createdAssetId;
            console.log(`Starting preview file upload with assetId: ${assetId}`);
            
            if (!assetId) {
                console.error('No asset ID available for preview upload - this should not happen');
                throw new Error('No asset ID available for preview upload');
            }
            
            // Update UI to show preview upload in progress
            const previewFileItem: FileUploadTableItem = {
                handle: { getFile: () => Promise.resolve(previewFile) },
                index: 999, // Use a high index to distinguish from regular files
                name: previewFile.name,
                size: previewFile.size,
                relativePath: `preview/${previewFile.name}`,
                progress: 0,
                status: "In Progress",
                loaded: 0,
                total: previewFile.size,
            };
            
            // Add preview file to the file items list
            setFileUploadItems(prev => [...prev, previewFileItem]);
            
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
                    ETag: etag
                });
                
                // Update progress
                const progress = Math.round(((i + 1) / fileResponse.partUploadUrls.length) * 100);
                setFileUploadItems(prev => 
                    prev.map(item => 
                        item.index === 999
                            ? { 
                                ...item, 
                                progress: progress,
                                loaded: progress * item.total / 100,
                                status: progress === 100 ? "Completed" : "In Progress"
                            }
                            : item
                    )
                );
            }
            
            // Complete the preview upload with the stored assetId
            await completePreviewUpload(initResponse.uploadId, fileResponse.relativeKey, fileResponse.uploadIdS3, parts, assetId);
            
        } catch (error: any) {
            console.error('Error uploading preview file:', error);
            setUploadState(prev => ({
                ...prev,
                previewCompletionStatus: 'failed',
                errors: [...prev.errors, { step: 'Preview Upload', message: error.message || 'Failed to upload preview file' }],
            }));
            throw error;
        }
    }, [uploadState.createdAssetId]);
    
    // Step 5b: Complete Preview Upload
    const completePreviewUpload = useCallback(async (uploadId: string, relativeKey: string, uploadIdS3: string, parts: UploadPartResult[], assetId: string) => {
        try {
            setUploadState(prev => ({ ...prev, previewCompletionStatus: 'in-progress' }));
            
            // Make sure we have a valid assetId
            if (!assetId) {
                throw new Error('No asset ID available for preview completion');
            }
            
            const completionRequest = {
                assetId: assetId,
                databaseId: assetDetail.databaseId || '',
                uploadType: "assetPreview" as const,
                files: [{
                    relativeKey,
                    uploadIdS3,
                    parts
                }]
            };
            
            // Log the asset ID being used
            console.log(`Using asset ID for preview completion: ${uploadState.createdAssetId}`);
            
            console.log('Sending preview completion request:', JSON.stringify(completionRequest, null, 2));
            
            const response = await AssetUploadService.completeUpload(
                uploadId,
                completionRequest
            );
            
            console.log('Preview completion response:', JSON.stringify(response, null, 2));
            
            setUploadState(prev => ({ ...prev, previewCompletionStatus: 'completed' }));
            
            return response;
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                previewCompletionStatus: 'failed',
                errors: [...prev.errors, { step: 'Preview Upload Completion', message: error.message || 'Failed to complete preview upload' }],
            }));
            throw error;
        }
    }, [assetDetail, uploadState.createdAssetId]);

    // Step 4: Upload File Parts
    const uploadFileParts = useCallback(async () => {
        console.log(`uploadFileParts called: uploadResponse=${!!uploadResponse}, fileParts.length=${fileParts.length}`);
        
        if (!uploadResponse || fileParts.length === 0) {
            console.warn('Cannot upload file parts: uploadResponse or fileParts is missing');
            return;
        }

        try {
            console.log('Setting upload status to in-progress');
            setUploadState(prev => ({ ...prev, uploadStatus: 'in-progress' }));

            // Create a queue of parts to upload - make a deep copy to avoid state mutation issues
            const queue = JSON.parse(JSON.stringify(fileParts.filter(part => part.status !== 'completed')));
            console.log(`Created queue with ${queue.length} parts to upload`);
            
            let completed = fileParts.filter(part => part.status === 'completed').length;
            setCompletedParts(completed);

            // Function to upload a single part
            const uploadPart = async (part: FilePart): Promise<FilePart> => {
                try {
                    console.log(`Starting upload for part ${part.partNumber} of file ${part.fileIndex}, size: ${part.end - part.start} bytes`);
                    setActiveUploads(prev => prev + 1);
                    
                    // Update part status to in-progress
                    setFileParts(prev => 
                        prev.map(p => 
                            p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                ? { ...p, status: 'in-progress' as const }
                                : p
                        )
                    );

                    // Update file item status
                    const fileItem = fileUploadItems[part.fileIndex];
                    if (fileItem.status !== "In Progress") {
                        setFileUploadItems(prev => 
                            prev.map((item, idx) => 
                                idx === part.fileIndex
                                    ? { ...item, status: "In Progress", startedAt: Math.floor(new Date().getTime() / 1000) }
                                    : item
                            )
                        );
                    }

                    // Get the file
                    console.log(`Getting file for part ${part.partNumber} of file ${part.fileIndex}`);
                    const file = await fileUploadItems[part.fileIndex].handle.getFile();
                    console.log(`File retrieved: ${file.name}, size: ${file.size}`);
                    
                    // Create a blob for this part
                    const blob = file.slice(part.start, part.end);
                    console.log(`Created blob for part ${part.partNumber}, size: ${blob.size}`);
                    
                    // Upload the part
                    console.log(`Uploading part ${part.partNumber} to URL: ${part.uploadUrl.substring(0, 100)}...`);
                    const etag = await AssetUploadService.uploadPart(part.uploadUrl, blob);
                    console.log(`Upload successful for part ${part.partNumber}, etag: ${etag}`);
                    
                    // Update part status to completed
                    const updatedPart: FilePart = { ...part, status: 'completed', etag };
                    setFileParts(prev => 
                        prev.map(p => 
                            p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                ? updatedPart
                                : p
                        )
                    );
                    
                    // Update completed count
                    completed++;
                    setCompletedParts(completed);
                    
                    // Update overall progress
                    const progress = Math.round((completed / totalParts) * 100);
                    setUploadState(prev => ({ ...prev, overallProgress: progress }));
                    
                    // Check if this was the last part to complete
                    if (completed === totalParts) {
                        console.log('All parts completed, directly calling completion');
                        // Set upload status to completed
                        setUploadState(prev => ({ ...prev, uploadStatus: 'completed' }));
                        
                        // Call completeUpload directly
                        setTimeout(() => {
                            console.log('Directly calling completeUpload after all parts completed');
                            completeUpload().catch(error => {
                                console.error('Error in direct completeUpload call:', error);
                            });
                        }, 1000);
                    }
                    
                    // Update file progress
                    const filePartsForThisFile = fileParts.filter(p => p.fileIndex === part.fileIndex);
                    const fileCompletedParts = filePartsForThisFile.filter(p => p.status === 'completed').length + 
                        (updatedPart.status === 'completed' ? 1 : 0);
                    const fileProgress = Math.round((fileCompletedParts / filePartsForThisFile.length) * 100);
                    
                    setFileUploadItems(prev => 
                        prev.map((item, idx) => 
                            idx === part.fileIndex
                                ? { 
                                    ...item, 
                                    progress: fileProgress,
                                    loaded: fileProgress * item.total / 100,
                                    status: fileProgress === 100 ? "Completed" : "In Progress"
                                }
                                : item
                        )
                    );
                    
                    return updatedPart;
                } catch (error) {
                    console.error(`Error uploading part ${part.partNumber} for file ${part.fileIndex}:`, error);
                    
                    // Update part status to failed
                    const updatedPart: FilePart = { ...part, status: 'failed', retryCount: part.retryCount + 1 };
                    setFileParts(prev => 
                        prev.map(p => 
                            p.fileIndex === part.fileIndex && p.partNumber === part.partNumber
                                ? updatedPart
                                : p
                        )
                    );
                    
                    // Update file status if this is the first failure
                    if (fileUploadItems[part.fileIndex].status !== "Failed") {
                        setFileUploadItems(prev => 
                            prev.map((item, idx) => 
                                idx === part.fileIndex
                                    ? { ...item, status: "Failed" }
                                    : item
                            )
                        );
                    }
                    
                    setShowRetryButton(true);
                    return updatedPart;
                } finally {
                    setActiveUploads(prev => prev - 1);
                }
            };

                // Process queue with concurrency limit
            const processQueue = async () => {
                // Keep track of all upload promises and their parts
                const uploadTasks: { promise: Promise<FilePart>; part: FilePart }[] = [];
                
                console.log(`Starting to process queue with ${queue.length} parts`);
                
                // Process initial batch - start up to MAX_CONCURRENT_UPLOADS uploads immediately
                while (queue.length > 0 && uploadTasks.length < MAX_CONCURRENT_UPLOADS) {
                    const part = queue.shift();
                    if (part) {
                        console.log(`Starting initial upload for part ${part.partNumber} of file ${part.fileIndex}`);
                        try {
                            // Start the upload and store the promise
                            const promise = uploadPart(part);
                            uploadTasks.push({ promise, part });
                        } catch (error) {
                            console.error(`Error starting upload for part ${part.partNumber}:`, error);
                        }
                    }
                }
                
                // Process remaining queue as uploads complete
                while (queue.length > 0 || uploadTasks.length > 0) {
                    if (uploadTasks.length === 0 && queue.length > 0) {
                        // If we somehow have no active tasks but still have queue items, start a new batch
                        const part = queue.shift();
                        if (part) {
                            console.log(`Starting new batch upload for part ${part.partNumber} of file ${part.fileIndex}`);
                            try {
                                const promise = uploadPart(part);
                                uploadTasks.push({ promise, part });
                            } catch (error) {
                                console.error(`Error starting upload for part ${part.partNumber}:`, error);
                            }
                        }
                    }
                    
                    if (uploadTasks.length > 0) {
                        try {
                            // Create a Promise.race with a timeout to prevent hanging
                            const timeoutPromise = new Promise<FilePart>((_, reject) => {
                                setTimeout(() => reject(new Error('Upload task timeout')), 10000); // 10 second timeout
                            });
                            
                            // Wait for any upload to complete or timeout
                            const promises = uploadTasks.map(task => {
                                // Wrap each promise to handle rejection
                                return task.promise.catch(error => {
                                    console.error(`Upload error caught for part ${task.part.partNumber}:`, error);
                                    return { ...task.part, status: 'failed' as const }; // Return the part so we can identify which one failed
                                });
                            });
                            
                            // Add the timeout promise to the race
                            try {
                                // Wait for the first promise to resolve or timeout
                                await Promise.race([...promises, timeoutPromise]);
                            } catch (error) {
                                console.warn('Upload task timeout or error:', error);
                                // Continue processing - we'll check for completed tasks below
                            }
                            
                            // Get the latest state
                            const latestFileParts = fileParts;
                            let tasksRemoved = false;
                            
                            // Remove completed or failed tasks
                            for (let i = uploadTasks.length - 1; i >= 0; i--) {
                                const task = uploadTasks[i];
                                // Check if this promise is settled
                                const isSettled = await Promise.race([
                                    task.promise.then(() => true).catch(() => true),
                                    new Promise(resolve => setTimeout(() => resolve(false), 0))
                                ]);
                                
                                if (isSettled) {
                                    // Find the current state of this part
                                    const currentPart = latestFileParts.find(
                                        p => p.fileIndex === task.part.fileIndex && p.partNumber === task.part.partNumber
                                    );
                                    
                                    if (currentPart && (currentPart.status === 'completed' || currentPart.status === 'failed')) {
                                        console.log(`Removing ${currentPart.status} task for part ${task.part.partNumber}`);
                                        uploadTasks.splice(i, 1);
                                        tasksRemoved = true;
                                    }
                                }
                            }
                            
                            // If no tasks were removed but we're still waiting, add a small delay
                            // to prevent CPU spinning
                            if (!tasksRemoved && uploadTasks.length > 0) {
                                await new Promise(resolve => setTimeout(resolve, 100));
                            }
                        } catch (error) {
                            console.error('Error waiting for upload tasks:', error);
                            // Add a delay to prevent CPU spinning in case of repeated errors
                            await new Promise(resolve => setTimeout(resolve, 500));
                        }
                    }
                    
                    // Fill up to max concurrent uploads
                    while (queue.length > 0 && uploadTasks.length < MAX_CONCURRENT_UPLOADS) {
                        const part = queue.shift();
                        if (part) {
                            console.log(`Starting additional upload for part ${part.partNumber} of file ${part.fileIndex}`);
                            try {
                                const promise = uploadPart(part);
                                uploadTasks.push({ promise, part });
                            } catch (error) {
                                console.error(`Error starting upload for part ${part.partNumber}:`, error);
                            }
                        }
                    }
                    
                    // If we still have tasks but no more queue items, wait a bit before checking again
                    if (uploadTasks.length > 0) {
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                }
                
                console.log('All uploads completed or failed');
                
                // Check if all parts are completed
                const allParts = [...fileParts];
                const allCompleted = allParts.every(part => part.status === 'completed');
                
                if (allCompleted) {
                    console.log('All parts completed in processQueue, directly calling completion');
                    // Set upload status to completed
                    setUploadState(prev => ({ ...prev, uploadStatus: 'completed' }));
                    
                    // Call completeUpload directly
                    setTimeout(() => {
                        console.log('Directly calling completeUpload from processQueue');
                        completeUpload().catch(error => {
                            console.error('Error in direct completeUpload call from processQueue:', error);
                        });
                    }, 1000);
                }
            };

            // Start processing the queue
            await processQueue();
            
            // Get the latest state of file parts from the state
            const latestFileParts = [...fileParts];
            
            // Check if all parts completed successfully
            const failedParts = latestFileParts.filter(part => part.status === 'failed');
            const completedParts = latestFileParts.filter(part => part.status === 'completed');
            
            console.log(`Upload status check: ${completedParts.length} completed, ${failedParts.length} failed out of ${latestFileParts.length} total parts`);
            console.log('Latest file parts:', JSON.stringify(latestFileParts));
            
            // If we have any completed parts and no failed parts, consider it a success
            if (failedParts.length === 0 && completedParts.length > 0) {
                console.log('All parts completed successfully, setting upload status to completed');
                
                // Make sure all file items show as completed
                setFileUploadItems(prev => 
                    prev.map(item => ({ 
                        ...item, 
                        status: "Completed",
                        progress: 100,
                        loaded: item.total
                    }))
                );
                
                // Hide retry button
                setShowRetryButton(false);
                
                // Set upload status to completed immediately to trigger completion step
                console.log('Setting upload status to completed to trigger completion step');
                setUploadState(prev => ({ ...prev, uploadStatus: 'completed' }));
                
                // Call completeUpload directly to ensure it's triggered
                setTimeout(() => {
                    console.log('Directly calling completeUpload from uploadFileParts');
                    completeUpload().catch(error => {
                        console.error('Error in direct completeUpload call:', error);
                    });
                }, 1000);
            } else if (failedParts.length > 0) {
                console.log(`${failedParts.length} parts failed to upload, setting upload status to failed`);
                setUploadState(prev => ({
                    ...prev,
                    uploadStatus: 'failed',
                    errors: [...prev.errors, { step: 'File Upload', message: `${failedParts.length} file parts failed to upload` }],
                }));
                setShowRetryButton(true);
            } else {
                // This shouldn't happen, but just in case
                console.warn('No completed parts found, but no failures either');
                setUploadState(prev => ({ ...prev, uploadStatus: 'completed' }));
                setShowRetryButton(false);
            }
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                uploadStatus: 'failed',
                errors: [...prev.errors, { step: 'File Upload', message: error.message || 'Failed to upload files' }],
            }));
            setShowRetryButton(true);
        }
    }, [uploadResponse, fileParts, fileUploadItems, totalParts, activeUploads]);

    // Step 5: Complete Upload
    const completeUpload = useCallback(async () => {
        console.log('completeUpload called with uploadResponse:', !!uploadResponse, 'uploadId:', uploadState.uploadId);
        
        if (!uploadResponse || !uploadState.uploadId) {
            console.error('Cannot complete upload: uploadResponse or uploadId is missing');
            return;
        }

        try {
            console.log('Setting completion status to in-progress');
            setUploadState(prev => ({ ...prev, completionStatus: 'in-progress' }));

            // Group parts by file
            const filePartsMap = new Map<number, UploadPartResult[]>();
            console.log('Total file parts:', fileParts.length);
            
            fileParts.forEach(part => {
                if (part.status === 'completed' && part.etag) {
                    if (!filePartsMap.has(part.fileIndex)) {
                        filePartsMap.set(part.fileIndex, []);
                    }
                    
                    filePartsMap.get(part.fileIndex)?.push({
                        PartNumber: part.partNumber,
                        ETag: part.etag,
                    });
                }
            });

            // Prepare completion request
            console.log('Preparing completion request with filePartsMap size:', filePartsMap.size);
            const completionFiles = Array.from(filePartsMap.entries()).map(([fileIndex, parts]) => {
                const fileResponse = uploadResponse.files[fileIndex];
                console.log(`File ${fileIndex}: ${fileResponse.relativeKey}, parts: ${parts.length}, expected parts: ${fileResponse.numParts}`);
                return {
                    relativeKey: fileResponse.relativeKey,
                    uploadIdS3: fileResponse.uploadIdS3,
                    parts: parts,
                };
            });

            // Only include files that have all parts completed
            const validCompletionFiles = completionFiles.filter(file => {
                const fileResponse = uploadResponse.files.find(f => f.relativeKey === file.relativeKey);
                const isValid = fileResponse && file.parts.length === fileResponse.numParts;
                if (!isValid) {
                    console.warn(`File ${file.relativeKey} has ${file.parts.length} parts but expected ${fileResponse?.numParts}`);
                }
                return isValid;
            });
            
            console.log(`Valid completion files: ${validCompletionFiles.length} out of ${completionFiles.length}`);

            if (validCompletionFiles.length === 0) {
                throw new Error('No files were successfully uploaded');
            }

            const completionRequest = {
                assetId: uploadState.createdAssetId || assetDetail.assetId || '',
                databaseId: assetDetail.databaseId || '',
                uploadType: "assetFile" as const,
                files: validCompletionFiles,
            };

            console.log('Sending completion request:', JSON.stringify(completionRequest, null, 2));
            console.log('Using uploadId:', uploadState.uploadId);

            const response = await AssetUploadService.completeUpload(
                uploadState.uploadId,
                completionRequest
            );
            
            console.log('Completion response:', JSON.stringify(response, null, 2));

            // Only mark as complete if there are no preview errors
            const hasPreviewErrors = uploadState.previewUploadInitStatus === 'failed' || 
                                    uploadState.previewCompletionStatus === 'failed';
            
            if (hasPreviewErrors) {
                console.log('Asset files upload completed, but preview upload had errors');
                setUploadState(prev => ({ 
                    ...prev, 
                    completionStatus: 'completed',
                    errors: [...prev.errors, { 
                        step: 'Upload Process', 
                        message: 'Asset files were uploaded successfully, but there were errors with the preview file upload.' 
                    }]
                }));
                // Still call onUploadComplete but with a modified response indicating preview errors
                const modifiedResponse = {
                    ...response,
                    message: response.message + " (Preview file upload failed)",
                };
                onUploadComplete(modifiedResponse);
            } else {
                setUploadState(prev => ({ ...prev, completionStatus: 'completed' }));
                onUploadComplete(response);
            }
            
            return response;
        } catch (error: any) {
            setUploadState(prev => ({
                ...prev,
                completionStatus: 'failed',
                errors: [...prev.errors, { step: 'Upload Completion', message: error.message || 'Failed to complete upload' }],
            }));
            throw error;
        }
    }, [uploadResponse, uploadState.uploadId, uploadState.createdAssetId, assetDetail, fileParts, onUploadComplete]);

    // Effect to start file uploads after initialization
    useEffect(() => {
        // Only run this effect when uploadInitStatus changes to 'completed' and uploads haven't started yet
        if (uploadState.uploadInitStatus === 'completed' && fileParts.length > 0 && !uploadStarted) {
            console.log('Upload initialization completed, starting file uploads');
            
            // Set flag to prevent multiple upload starts
            setUploadStarted(true);
            
            // Start the file uploads
            const startUploads = async () => {
                try {
                    await uploadFileParts();
                } catch (error: any) {
                    console.error('Upload process error:', error);
                    onError(error);
                }
            };
            
            startUploads();
        }
    }, [uploadState.uploadInitStatus, fileParts.length, uploadFileParts, onError, uploadStarted]);
    
    // Separate effect to monitor upload status and trigger completion
    useEffect(() => {
        // Only run when upload status changes to completed
        if (uploadState.uploadStatus === 'completed' && uploadState.completionStatus === 'pending') {
            console.log('Upload completed, starting completion process');
            
            // Call completeUpload directly without setTimeout
            const finishUpload = async () => {
                try {
                    console.log('Executing completeUpload...');
                    await completeUpload();
                } catch (error: any) {
                    console.error('Completion error:', error);
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
                let assetId = assetDetail.assetId || '';
                if (!isExistingAsset) {
                    const assetResponse = await createAsset();
                    if (assetResponse) {
                        assetId = assetResponse.assetId;
                        
                        // Update state with the created asset ID
                        setUploadState(prev => ({
                            ...prev,
                            createdAssetId: assetId
                        }));
                        
                        // Step 2: Add Metadata
                        await addMetadata(assetId);
                    }
                } else if (assetId) {
                    // For existing assets, make sure we set the createdAssetId in state
                    setUploadState(prev => ({
                        ...prev,
                        createdAssetId: assetId
                    }));
                }

                // Step 3a: Initialize Asset Files Upload
                await initializeUpload(assetId);
                
                // Step 3b: Initialize Preview File Upload (if applicable)
                // Only initialize preview upload if we have a valid asset ID
                if (assetDetail.Preview && assetId) {
                    await initializePreviewUpload(assetId);
                }
                
                // Steps 4 and 5 (Upload File Parts and Complete Upload) are handled by the other useEffect
            } catch (error: any) {
                console.error('Upload process error:', error);
                onError(error);
            }
        };

        performUpload();
    }, []); // Run once on mount

    // Retry failed uploads
    const handleRetry = useCallback(async () => {
        setShowRetryButton(false);
        
        // Reset failed parts to pending
        setFileParts(prev => 
            prev.map(part => 
                part.status === 'failed'
                    ? { ...part, status: 'pending' }
                    : part
            )
        );
        
        // Reset failed files to queued
        setFileUploadItems(prev => 
            prev.map(item => 
                item.status === "Failed"
                    ? { ...item, status: "Queued", progress: 0 }
                    : item
            )
        );
        
        // Reset upload started flag to allow the upload to start again
        setUploadStarted(false);
        
        // Retry upload
        await uploadFileParts();
        
        // If upload is now successful, complete it
        if (uploadState.uploadStatus === 'completed') {
            await completeUpload();
        }
    }, [uploadFileParts, completeUpload, uploadState.uploadStatus]);

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
            if (uploadState.assetCreationStatus === 'failed') {
                // Retry from asset creation
                const assetResponse = await createAsset();
                if (assetResponse) {
                    const assetId = assetResponse.assetId;
                    await addMetadata(assetId);
                    await initializeUpload(assetId);
                    // uploadFileParts will be called by the useEffect
                }
            } else if (uploadState.metadataStatus === 'failed') {
                // Retry from metadata
                const assetId = uploadState.createdAssetId || assetDetail.assetId || '';
                await addMetadata(assetId);
                await initializeUpload(assetId);
                // uploadFileParts will be called by the useEffect
            } else if (uploadState.uploadInitStatus === 'failed') {
                // Retry from upload initialization
                const assetId = uploadState.createdAssetId || assetDetail.assetId || '';
                await initializeUpload(assetId);
                // uploadFileParts will be called by the useEffect
            }
        } catch (error: any) {
            console.error('Retry error:', error);
            onError(error);
        }
    }, [
        uploadState.assetCreationStatus,
        uploadState.metadataStatus,
        uploadState.uploadInitStatus,
        uploadState.uploadStatus,
        uploadState.createdAssetId,
        assetDetail.assetId,
        createAsset,
        addMetadata,
        initializeUpload,
        uploadFileParts,
        completeUpload,
        onError
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

                {/* Retry and Back buttons for API errors */}
                {(uploadState.assetCreationStatus === 'failed' || 
                  uploadState.metadataStatus === 'failed' || 
                  uploadState.uploadInitStatus === 'failed') && (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={() => handleRetryFromStep()} variant="primary">
                            Retry from Failed Step
                        </Button>
                        {onCancel && (
                            <Button onClick={onCancel}>
                                Back to Review
                            </Button>
                        )}
                    </SpaceBetween>
                )}

                <SpaceBetween direction="vertical" size="m">
                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Asset Creation</Box>
                            <SpaceBetween direction="horizontal" size="xs">
                                <StatusIndicator type={getStatusIndicatorType(uploadState.assetCreationStatus)}>
                                    {getStatusText(uploadState.assetCreationStatus)}
                                </StatusIndicator>
                                {uploadState.assetCreationStatus === 'completed' && uploadState.createdAssetId && (
                                    <Link href={`#/databases/${assetDetail.databaseId}/assets/${uploadState.createdAssetId}`}>
                                        {uploadState.createdAssetId}
                                    </Link>
                                )}
                            </SpaceBetween>
                        </SpaceBetween>
                    </Box>

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Metadata</Box>
                            <StatusIndicator type={getStatusIndicatorType(uploadState.metadataStatus)}>
                                {getStatusText(uploadState.metadataStatus)}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>

                    <Box>
                        <SpaceBetween direction="vertical" size="xs">
                            <Box variant="awsui-key-label">Asset Files Upload Initialization</Box>
                            <StatusIndicator type={getStatusIndicatorType(uploadState.uploadInitStatus)}>
                                {getStatusText(uploadState.uploadInitStatus)}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>
                    
                    {uploadState.previewUploadInitStatus !== 'skipped' && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Preview File Upload Initialization</Box>
                                <StatusIndicator type={getStatusIndicatorType(uploadState.previewUploadInitStatus)}>
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
                            <StatusIndicator type={getStatusIndicatorType(uploadState.completionStatus)}>
                                {getStatusText(uploadState.completionStatus)}
                            </StatusIndicator>
                        </SpaceBetween>
                    </Box>
                    
                    {uploadState.previewCompletionStatus !== 'skipped' && (
                        <Box>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="awsui-key-label">Preview File Upload Completion</Box>
                                <StatusIndicator type={getStatusIndicatorType(uploadState.previewCompletionStatus)}>
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
                />

                {showRetryButton && (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={handleRetry} variant="primary">
                            Retry Failed Uploads
                        </Button>
                        <Button onClick={handleManualCompletion}>
                            Complete with Successful Files Only
                        </Button>
                    </SpaceBetween>
                )}
                
                {/* Manual completion button when uploads are completed but completion is pending */}
                {uploadState.uploadStatus === 'completed' && uploadState.completionStatus === 'pending' && (
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
function getStatusIndicatorType(status: string): "pending" | "loading" | "success" | "error" | "info" | "warning" | "stopped" {
    switch (status) {
        case 'pending':
            return 'pending';
        case 'in-progress':
            return 'loading';
        case 'completed':
            return 'success';
        case 'failed':
            return 'error';
        default:
            return 'info';
    }
}

function getStatusText(status: string): string {
    switch (status) {
        case 'pending':
            return 'Pending';
        case 'in-progress':
            return 'In Progress';
        case 'completed':
            return 'Completed';
        case 'failed':
            return 'Failed';
        default:
            return 'Unknown';
    }
}

function getProgressBarStatus(status: string): "in-progress" | "success" | "error" {
    switch (status) {
        case 'in-progress':
            return 'in-progress';
        case 'completed':
            return 'success';
        case 'failed':
            return 'error';
        default:
            return 'in-progress';
    }
}
