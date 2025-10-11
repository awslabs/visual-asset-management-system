/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useState } from "react";
import {
    Alert,
    Box,
    Button,
    Container,
    Grid,
    Header,
    SpaceBetween,
} from "@cloudscape-design/components";
import { AssetDetail } from "./AssetUpload";
import { Metadata } from "../../components/single/Metadata";
import { FileUploadTableItem } from "./FileUploadTable";
import UploadManager from "./UploadManager";
import { CompleteUploadResponse } from "../../services/AssetUploadService";
import { useNavigate } from "react-router";

interface AssetUploadWorkflowProps {
    assetDetail: AssetDetail;
    metadata: Metadata;
    fileItems: FileUploadTableItem[];
    onComplete: (response: CompleteUploadResponse) => void;
    onCancel: () => void;
    isExistingAsset?: boolean;
    keyPrefix?: string;
}

export default function AssetUploadWorkflow({
    assetDetail,
    metadata,
    fileItems,
    onComplete,
    onCancel,
    isExistingAsset = false,
    keyPrefix,
}: AssetUploadWorkflowProps) {
    const [uploadComplete, setUploadComplete] = useState(false);
    const [uploadError, setUploadError] = useState<Error | null>(null);
    const [uploadResponse, setUploadResponse] = useState<CompleteUploadResponse | null>(null);
    const navigate = useNavigate();

    // Handle upload completion
    const handleUploadComplete = (response: CompleteUploadResponse) => {
        // Only mark as complete if there are no preview errors in the message
        if (!response.message?.includes("Preview file upload failed")) {
            setUploadComplete(true);
            setUploadResponse(response);
            onComplete(response);
        } else {
            // Still store the response but don't show the completion message yet
            setUploadResponse(response);
            onComplete(response);
        }
    };

    // Handle upload error
    const handleUploadError = (error: Error) => {
        setUploadError(error);
    };

    // Handle view asset button click
    const handleViewAsset = () => {
        if (uploadResponse) {
            navigate(`/databases/${assetDetail.databaseId}/assets/${uploadResponse.assetId}`);
        }
    };

    return (
        <Container header={<Header variant="h2">Asset Upload</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <UploadManager
                    assetDetail={assetDetail}
                    metadata={metadata}
                    fileItems={fileItems}
                    onUploadComplete={handleUploadComplete}
                    onError={(error) => {
                        // Just log the error but don't switch to error state
                        console.error("Upload error:", error);
                    }}
                    isExistingAsset={isExistingAsset}
                    onCancel={onCancel}
                    keyPrefix={keyPrefix}
                />

                {uploadComplete && (
                    <Box>
                        <SpaceBetween direction="vertical" size="m">
                            {/* Display large file processing alert if applicable */}
                            {uploadResponse?.largeFileAsynchronousHandling && (
                                <Alert type="info" header="Large File Processing">
                                    This upload contains large files that are undergoing separate
                                    processing. The files may take additional time to appear in the
                                    asset files list. Please check back in a few minutes.
                                </Alert>
                            )}

                            {isExistingAsset ? (
                                // Message for existing assets
                                uploadResponse?.message?.includes("Preview file upload failed") ? (
                                    <Alert type="warning" header="Upload Completed with Warnings">
                                        Files have been added to asset {uploadResponse?.assetId},
                                        but there were issues with the preview file upload.
                                    </Alert>
                                ) : uploadResponse?.overallSuccess === false ? (
                                    <Alert
                                        type="warning"
                                        header="Upload Completed with Some Failures"
                                    >
                                        Some files have been added to asset{" "}
                                        {uploadResponse?.assetId}, but there were issues with some
                                        file uploads. See the error details below.
                                    </Alert>
                                ) : (
                                    <Alert type="success" header="Upload Complete">
                                        Files have been successfully added to asset{" "}
                                        {uploadResponse?.assetId}.
                                    </Alert>
                                )
                            ) : // Message for new assets
                            uploadResponse?.message?.includes("Preview file upload failed") ? (
                                <Alert type="warning" header="Upload Completed with Warnings">
                                    Asset {uploadResponse?.assetId} has been created and asset files
                                    have been uploaded, but there were issues with the preview file
                                    upload.
                                </Alert>
                            ) : uploadResponse?.overallSuccess === false ? (
                                <Alert type="warning" header="Upload Completed with Some Failures">
                                    Asset {uploadResponse?.assetId} has been created, but there were
                                    issues with some file uploads. See the error details below.
                                </Alert>
                            ) : (
                                <Alert type="success" header="Upload Complete">
                                    Asset {uploadResponse?.assetId} has been successfully created
                                    and all files have been uploaded.
                                </Alert>
                            )}

                            {/* Display failed file details if there are any */}
                            {uploadResponse?.overallSuccess === false &&
                                uploadResponse?.fileResults && (
                                    <Alert type="error" header="Failed File Details">
                                        <ul>
                                            {uploadResponse.fileResults
                                                .filter((file) => !file.success)
                                                .map((file, index) => (
                                                    <li key={index}>
                                                        <strong>{file.relativeKey}:</strong>{" "}
                                                        {file.error || "Unknown error"}
                                                    </li>
                                                ))}
                                        </ul>
                                    </Alert>
                                )}

                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={handleViewAsset} variant="primary">
                                    View Asset
                                </Button>
                                <Button onClick={onCancel}>
                                    {isExistingAsset ? "Return to Asset Files" : "Return to Assets"}
                                </Button>
                            </SpaceBetween>
                        </SpaceBetween>
                    </Box>
                )}
            </SpaceBetween>
        </Container>
    );
}
