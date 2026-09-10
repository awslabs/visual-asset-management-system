/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { fileIdentity } from "../../../visualizerPlugin/core/fileIdentity";
import { Modal, Box, Button, SegmentedControl, SpaceBetween } from "@cloudscape-design/components";
import { DynamicViewer } from "../../../visualizerPlugin/components/DynamicViewer";
import { FileInfo } from "../../../visualizerPlugin/core/types";
import { ViewerMode } from "../../../visualizerPlugin/core/PluginRegistry";

interface FileViewerModalProps {
    visible: boolean;
    onDismiss: () => void;
    files: FileInfo[];
    databaseId: string;
    assetId: string;
    assetVersionId?: string;
    /** Initial mode. "compare" opens the modal on the Compare tab (compare-capable viewers only).
     *  Defaults to "visualize". The user can still flip modes with the in-modal toggle unless
     *  `allowModeToggle` is false. */
    initialMode?: ViewerMode;
    /** When false, hides the Visualize/Compare toggle and locks the modal to `initialMode`.
     *  Used by callers (e.g. version compare) that only ever want the compare surface. */
    allowModeToggle?: boolean;
}

export const FileViewerModal: React.FC<FileViewerModalProps> = ({
    visible,
    onDismiss,
    files,
    databaseId,
    assetId,
    assetVersionId,
    initialMode = "visualize",
    allowModeToggle = true,
}) => {
    const [viewerMode, setViewerMode] = useState("collapse");
    const [mode, setMode] = useState<ViewerMode>(initialMode);

    // Reset viewer mode when modal is opened/closed or files change
    React.useEffect(() => {
        if (visible) {
            setViewerMode("collapse");
            setMode(initialMode);
        }
    }, [visible, files, initialMode]);

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
        const prefix = mode === "compare" ? "Compare Files" : "File Viewer";
        if (files.length === 1) {
            return `${prefix} - ${files[0].filename}`;
        }
        return `${prefix} - ${files.length} Files`;
    };

    // Generate a unique key for DynamicViewer to force re-mounting when files change
    // Identity is database + asset + key, not the key alone. The same asset-relative path exists in
    // many assets, so keying on the path let a different set of files produce the same viewer key —
    // swapping assetA/model.glb for assetB/model.glb left the previous file on screen because React
    // saw no reason to remount. The mode is part of the key so flipping Visualize/Compare remounts
    // the viewer with the correct props and viewer filtering.
    const getViewerKey = () => {
        if (files.length === 0) return "empty";
        if (files.length === 1) {
            return `${mode}-single-${fileIdentity(files[0])}-${files[0].versionId || "no-version"}`;
        }
        const sortedIdentities = files.map(fileIdentity).sort().join("|");
        return `${mode}-multi-${sortedIdentities}`;
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
            {files.length > 0 ? (
                <SpaceBetween size="s">
                    {allowModeToggle && (
                        <Box>
                            <SegmentedControl
                                selectedId={mode}
                                onChange={({ detail }) => setMode(detail.selectedId as ViewerMode)}
                                label="Viewer mode"
                                options={[
                                    { id: "visualize", text: "Visualize" },
                                    { id: "compare", text: "Compare" },
                                ]}
                            />
                        </Box>
                    )}
                    <div
                        key={getViewerKey()}
                        className="file-viewer-modal-content"
                        style={{
                            width: "100%",
                        }}
                    >
                        <DynamicViewer
                            files={files}
                            assetId={assetId}
                            databaseId={databaseId}
                            assetVersionId={assetVersionId}
                            viewerMode={viewerMode}
                            onViewerModeChange={handleViewerModeChange}
                            showViewerSelector={true}
                            isPreviewMode={false}
                            hideFullscreenControls={true}
                            mode={mode}
                        />
                    </div>
                </SpaceBetween>
            ) : (
                <Box textAlign="center" padding="xl">
                    <Box variant="h3">No Files to Display</Box>
                    <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                        No viewable files were selected.
                    </Box>
                </Box>
            )}
        </Modal>
    );
};

export default FileViewerModal;
