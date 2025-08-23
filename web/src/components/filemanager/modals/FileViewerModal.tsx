/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Modal, Box, SpaceBetween, Button } from "@cloudscape-design/components";
import DynamicViewer from "../../../visualizerPlugin/components/DynamicViewer";
import { FileInfo } from "../../../visualizerPlugin/core/types";

interface FileViewerModalProps {
    visible: boolean;
    onDismiss: () => void;
    files: FileInfo[];
    databaseId: string;
    assetId: string;
}

export const FileViewerModal: React.FC<FileViewerModalProps> = ({
    visible,
    onDismiss,
    files,
    databaseId,
    assetId,
}) => {
    const [viewerMode, setViewerMode] = useState("collapse");

    // Reset viewer mode when modal is opened/closed or files change
    React.useEffect(() => {
        if (visible) {
            setViewerMode("collapse");
        }
    }, [visible, files]);

    const handleViewerModeChange = (mode: string) => {
        // In modal context, we don't support fullscreen mode
        // Only allow collapse and wide modes
        if (mode === "fullscreen") {
            setViewerMode("wide");
        } else {
            setViewerMode(mode);
        }
    };

    const getModalTitle = () => {
        if (files.length === 1) {
            return `File Viewer - ${files[0].filename}`;
        }
        return `File Viewer - ${files.length} Files`;
    };

    // Generate a unique key for DynamicViewer to force re-mounting when files change
    const getViewerKey = () => {
        if (files.length === 0) return "empty";
        if (files.length === 1) {
            return `single-${files[0].key}-${files[0].versionId || "no-version"}`;
        }
        // For multi-file, create a stable key based on file keys
        const sortedKeys = files
            .map((f) => f.key)
            .sort()
            .join("|");
        return `multi-${sortedKeys}`;
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={getModalTitle()}
            size="max"
            footer={
                <Box float="right">
                    <Button variant="primary" onClick={onDismiss}>
                        Close
                    </Button>
                </Box>
            }
        >
            <Box padding={{ vertical: "s" }}>
                {files.length > 0 ? (
                    <div key={getViewerKey()}>
                        <DynamicViewer
                            files={files}
                            assetId={assetId}
                            databaseId={databaseId}
                            viewerMode={viewerMode}
                            onViewerModeChange={handleViewerModeChange}
                            showViewerSelector={true}
                            isPreviewMode={false}
                            hideFullscreenControls={true}
                        />
                    </div>
                ) : (
                    <Box textAlign="center" padding="xl">
                        <Box variant="h3">No Files to Display</Box>
                        <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                            No viewable files were selected.
                        </Box>
                    </Box>
                )}
            </Box>
        </Modal>
    );
};

export default FileViewerModal;
