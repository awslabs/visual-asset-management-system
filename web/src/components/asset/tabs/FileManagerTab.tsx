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
}

export const FileManagerTab: React.FC<FileManagerTabProps> = ({
    assetName,
    filePathToNavigate,
}) => {
    return (
        <ErrorBoundary componentName="File Manager">
            <EnhancedFileManager assetName={assetName} filePathToNavigate={filePathToNavigate} />
        </ErrorBoundary>
    );
};

export default FileManagerTab;
