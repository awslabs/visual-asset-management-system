/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { FileInfo } from "../core/types";
import DynamicViewer from "./DynamicViewer";

export interface StandaloneViewerProps {
    files: FileInfo[];
    assetId: string;
    databaseId: string;
    className?: string;
    isPreviewMode?: boolean;
    onDeletePreview?: () => void;
}

export const StandaloneViewer: React.FC<StandaloneViewerProps> = ({
    files,
    assetId,
    databaseId,
    className,
    isPreviewMode = false,
    onDeletePreview,
}) => {
    const [viewerMode, setViewerMode] = useState("wide");

    return (
        <div className={className}>
            <DynamicViewer
                files={files}
                assetId={assetId}
                databaseId={databaseId}
                viewerMode={viewerMode}
                onViewerModeChange={setViewerMode}
                showViewerSelector={true}
                isPreviewMode={isPreviewMode}
                onDeletePreview={onDeletePreview}
            />
        </div>
    );
};

export default StandaloneViewer;
