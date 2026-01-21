/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * FileMetadata Component - Refactored to use MetadataV2
 *
 * This component has been completely refactored to use the new MetadataContainer
 * which provides enhanced functionality including:
 * - Schema integration with visual indicators
 * - Bulk edit mode for quick editing
 * - Better validation with real-time feedback
 * - Change tracking with batch commit
 * - Performance optimizations with React.memo
 * - Support for both file metadata and file attributes
 */

import React from "react";
import { Container, Header } from "@cloudscape-design/components";
import { MetadataContainer } from "../metadataV2";

/**
 * Props interface for the FileMetadata component
 */
export interface FileMetadataProps {
    /** Database ID for the metadata */
    databaseId: string;
    /** Asset ID for the metadata */
    assetId: string;
    /** File prefix/path for the metadata */
    prefix: string;
    /** Optional CSS class name for styling */
    className?: string;
    /** Whether to show the metadata header */
    showHeader?: boolean;
    /** Whether to show validation errors */
    showErrors?: boolean;
    /** Callback for error handling */
    onError?: (error: string) => void;
    /** Callback for loading state changes */
    onLoading?: (loading: boolean) => void;
    /** Callback for validation state changes */
    onValidationChange?: (isValid: boolean) => void;
    /** Callback for save success */
    onSaveSuccess?: () => void;
    /** Callback for save error */
    onSaveError?: (error: string) => void;
}

/**
 * FileMetadata component - Now uses MetadataV2 Container
 *
 * Provides a consistent interface for displaying file metadata with tabs for
 * both metadata and attributes.
 */
export default function FileMetadata({
    databaseId,
    assetId,
    prefix,
    className,
    showHeader = true,
    showErrors = true,
    onError,
    onLoading,
    onValidationChange,
    onSaveSuccess,
    onSaveError,
}: FileMetadataProps) {
    const commonProps = {
        "data-testid": "file-metadata",
        role: "region",
        "aria-label": "File metadata",
        className: className,
    };

    // The new MetadataContainer handles file metadata with tabs
    const metadataContent = (
        <MetadataContainer
            entityType="file"
            entityId={assetId}
            databaseId={databaseId}
            filePath={prefix}
            fileType="metadata"
            mode="online"
        />
    );

    if (showHeader) {
        return (
            <Container
                header={
                    <Header variant="h2" headingTagOverride="h3">
                        Metadata
                    </Header>
                }
                {...commonProps}
            >
                {metadataContent}
            </Container>
        );
    }

    return <div {...commonProps}>{metadataContent}</div>;
}

// Export cache cleanup functions for backward compatibility
export const cleanupMetadataCache = () => {
    console.log("Cache cleanup called - no longer needed with MetadataV2");
};

export const cancelAllMetadataRequests = () => {
    console.log("Cancel requests called - no longer needed with MetadataV2");
};

export const clearMetadataCache = () => {
    console.log("Clear cache called - no longer needed with MetadataV2");
};
