/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Modal, Box, SpaceBetween, Button, Alert, Spinner } from "@cloudscape-design/components";
import { unarchiveFile } from "../../services/FileOperationsService";

interface UnarchiveFileModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: () => void;
    selectedFiles: any[];
    databaseId?: string;
    assetId?: string;
}

const UnarchiveFileModal: React.FC<UnarchiveFileModalProps> = ({
    visible,
    onDismiss,
    onSuccess,
    selectedFiles,
    databaseId,
    assetId,
}) => {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [processedCount, setProcessedCount] = useState(0);

    // Reset state when modal opens/closes
    React.useEffect(() => {
        if (visible) {
            setLoading(false);
            setError(null);
            setProcessedCount(0);
        }
    }, [visible]);

    const isMultipleFiles = selectedFiles.length > 1;
    const fileName = isMultipleFiles
        ? `${selectedFiles.length} files`
        : selectedFiles[0]?.name || selectedFiles[0]?.displayName || "file";

    const handleSubmit = async () => {
        if (!databaseId || !assetId) {
            setError("Missing database ID or asset ID");
            return;
        }

        setLoading(true);
        setError(null);

        try {
            // Process each file sequentially
            for (let i = 0; i < selectedFiles.length; i++) {
                setProcessedCount(i);

                const file = selectedFiles[i];
                await unarchiveFile(databaseId, assetId, {
                    filePath: file.relativePath,
                });
            }

            setLoading(false);
            setProcessedCount(selectedFiles.length);
            onSuccess();
        } catch (error: any) {
            console.error("Error unarchiving files:", error);
            setLoading(false);
            setError(error.message || "An error occurred while unarchiving the files.");
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Unarchive ${isMultipleFiles ? "Files" : "File"}`}
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={handleSubmit} disabled={loading}>
                            {loading ? (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Spinner />
                                    {`Processing ${processedCount + 1}/${selectedFiles.length}`}
                                </SpaceBetween>
                            ) : (
                                "Unarchive"
                            )}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                <Box variant="p">
                    Are you sure you want to unarchive <b>{fileName}</b>?
                    <br />
                    Unarchived files will appear in normal search results and file listings.
                </Box>
            </SpaceBetween>
        </Modal>
    );
};

export default UnarchiveFileModal;
