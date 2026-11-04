/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router";
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
    const { databaseId, assetId } = useParams();
    // The file manager seeds its asset, selection and listing from the route once, so it is
    // remounted per asset; otherwise its actions (Download among them) keep the previous asset.
    return (
        <ErrorBoundary key={`${databaseId}/${assetId}`} componentName="File Manager">
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
