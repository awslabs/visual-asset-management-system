/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";
import { Metadata } from "../components/single/Metadata";

export interface AssetLinks {
    parents: string[];
    child: string[];
    related: string[];
}

export interface CreateAssetRequest {
    assetId?: string;
    assetName: string;
    databaseId: string;
    description: string;
    isDistributable: boolean;
    tags?: string[];
    assetLinks?: AssetLinks;
}

export interface CreateAssetResponse {
    assetId: string;
    message: string;
}

export interface FileUploadRequest {
    relativeKey: string;
    file_size: number;
    num_parts?: number;
}

export interface InitializeUploadRequest {
    assetId: string;
    databaseId: string;
    uploadType: "assetFile" | "assetPreview";
    files: FileUploadRequest[];
}

export interface UploadPart {
    PartNumber: number;
    UploadUrl: string;
}

export interface UploadFileResponse {
    relativeKey: string;
    uploadIdS3: string;
    numParts: number;
    partUploadUrls: UploadPart[];
}

export interface InitializeUploadResponse {
    uploadId: string;
    files: UploadFileResponse[];
    message: string;
}

export interface UploadPartResult {
    PartNumber: number;
    ETag: string;
}

export interface CompleteFileUpload {
    relativeKey: string;
    uploadIdS3: string;
    parts: UploadPartResult[];
}

export interface CompleteUploadRequest {
    assetId: string;
    databaseId: string;
    uploadType: "assetFile" | "assetPreview";
    files: CompleteFileUpload[];
}

export interface FileCompletionResult {
    relativeKey: string;
    uploadIdS3: string;
    success: boolean;
    error?: string;
}

export interface CompleteUploadResponse {
    message: string;
    uploadId: string;
    assetId: string;
    assetType?: string;
    version?: string;
    fileResults: FileCompletionResult[];
    overallSuccess: boolean;
}

export class AssetUploadService {
    /**
     * Creates a new asset in the database
     * @param assetData Asset data to create
     * @returns Promise with the created asset ID
     */
    async createAsset(assetData: CreateAssetRequest): Promise<CreateAssetResponse> {
        try {
            const response = await API.post("api", "assets", {
                "Content-type": "application/json",
                body: assetData,
            });
            return response;
        } catch (error) {
            console.error("Error creating asset:", error);
            throw error;
        }
    }

    /**
     * Adds metadata to an existing asset
     * @param databaseId Database ID
     * @param assetId Asset ID
     * @param metadata Metadata to add
     * @returns Promise with the result
     */
    async addMetadata(databaseId: string, assetId: string, metadata: Metadata): Promise<any> {
        try {
            const response = await API.post("api", `database/${databaseId}/assets/${assetId}/metadata`, {
                "Content-type": "application/json",
                body: { 
                    metadata,
                    version: "1" // Required by the backend API
                },
            });
            return response;
        } catch (error) {
            console.error("Error adding metadata:", error);
            throw error;
        }
    }

    /**
     * Initializes a file upload process
     * @param uploadRequest Upload request data
     * @returns Promise with upload ID and presigned URLs
     */
    async initializeUpload(uploadRequest: InitializeUploadRequest): Promise<InitializeUploadResponse> {
        try {
            const response = await API.post("api", "uploads", {
                "Content-type": "application/json",
                body: uploadRequest,
            });
            return response;
        } catch (error) {
            console.error("Error initializing upload:", error);
            throw error;
        }
    }

    /**
     * Uploads a file part to a presigned URL
     * @param url Presigned URL
     * @param data File part data
     * @returns Promise with ETag
     */
    async uploadPart(url: string, data: Blob): Promise<string> {
        try {
            const response = await fetch(url, {
                method: "PUT",
                body: data,
            });

            if (!response.ok) {
                throw new Error(`Failed to upload part: ${response.status} ${response.statusText}`);
            }

            // Extract ETag from response headers
            const etag = response.headers.get("ETag");
            if (!etag) {
                throw new Error("No ETag returned from upload");
            }

            // Remove quotes from ETag if present
            return etag.replace(/"/g, "");
        } catch (error) {
            console.error("Error uploading part:", error);
            throw error;
        }
    }

    /**
     * Completes a file upload process
     * @param uploadId Upload ID
     * @param completionData Completion data
     * @returns Promise with upload result
     */
    async completeUpload(uploadId: string, completionData: CompleteUploadRequest): Promise<CompleteUploadResponse> {
        try {
            const response = await API.post("api", `uploads/${uploadId}/complete`, {
                "Content-type": "application/json",
                body: completionData,
            });
            return response;
        } catch (error) {
            console.error("Error completing upload:", error);
            throw error;
        }
    }
}

export default new AssetUploadService();
