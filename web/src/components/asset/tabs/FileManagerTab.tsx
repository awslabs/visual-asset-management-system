/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { EnhancedFileManager } from "../../filemanager/EnhancedFileManager";
import ErrorBoundary from "../../common/ErrorBoundary";

interface FileManagerTabProps {
    assetName: string;
    filePathToNavigate?: string; // Optional file path to navigate to
    assetVersionId?: string; // Optional version ID to filter files
    /** Bubbled-up notification when the user changes the file/folder selection. */
    onSelectedPathChange?: (path: string | null) => void;
}

export const FileManagerTab: React.FC<FileManagerTabProps> = ({
    assetName,
    filePathToNavigate,
    assetVersionId,
    onSelectedPathChange,
}) => {
    return (
        <ErrorBoundary componentName="File Manager">
            <EnhancedFileManager
                assetName={assetName}
                filePathToNavigate={filePathToNavigate}
                assetVersionId={assetVersionId}
                onSelectedPathChange={onSelectedPathChange}
            />
        </ErrorBoundary>
    );
};

export default FileManagerTab;
