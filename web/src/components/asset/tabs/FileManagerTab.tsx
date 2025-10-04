/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { EnhancedFileManager } from "../../filemanager/EnhancedFileManager";
import ErrorBoundary from "../../common/ErrorBoundary";
import { FileKey } from "../../filemanager/types/FileManagerTypes";
import { LoadingSpinner } from "../../common/LoadingSpinner";

interface FileManagerTabProps {
    assetName: string;
    assetFiles: FileKey[];
    assetId: string;
    databaseId: string;
    loading: boolean;
    onExecuteWorkflow: () => void; // Keeping prop for compatibility, but not using it
    filePathToNavigate?: string; // Optional file path to navigate to
}

export const FileManagerTab: React.FC<FileManagerTabProps> = ({
    assetName,
    assetFiles,
    loading,
    filePathToNavigate,
}) => {
    return (
        <ErrorBoundary componentName="File Manager">
            {loading ? (
                <LoadingSpinner text="Loading files..." />
            ) : (
                <EnhancedFileManager
                    assetName={assetName}
                    assetFiles={assetFiles}
                    filePathToNavigate={filePathToNavigate}
                />
            )}
        </ErrorBoundary>
    );
};

export default FileManagerTab;
