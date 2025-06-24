/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Modal, Box, Button, Alert } from "@cloudscape-design/components";
import { FileVersionsTable } from "../components/FileVersionsTable";
import { useNavigate } from "react-router";
import "./FileVersionsModal.css";

// TypeScript interfaces
interface FileVersionsModalProps {
    visible: boolean;
    onDismiss: () => void;
    databaseId: string;
    assetId: string;
    filePath: string;
    fileName: string;
    currentVersionId?: string; // For ViewFile context
    onVersionRevert?: () => void; // Refresh callback
}

interface RevertConfirmationModalProps {
    visible: boolean;
    onDismiss: () => void;
    onConfirm: () => void;
    versionId: string;
    fileName: string;
    isLoading: boolean;
}

// Revert Confirmation Modal Component
const RevertConfirmationModal: React.FC<RevertConfirmationModalProps> = ({
    visible,
    onDismiss,
    onConfirm,
    versionId,
    fileName,
    isLoading,
}) => {
    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header="Confirm Version Revert"
            footer={
                <Box float="right">
                    <Button variant="link" onClick={onDismiss} disabled={isLoading}>
                        Cancel
                    </Button>
                    <Button variant="primary" onClick={onConfirm} loading={isLoading}>
                        Revert
                    </Button>
                </Box>
            }
        >
            <Alert type="warning">
                This will create a new current version with the contents of version{" "}
                <strong>{versionId}</strong>.
            </Alert>
            <Box>
                <p>
                    Are you sure you want to revert <strong>{fileName}</strong> to version{" "}
                    <strong>{versionId}</strong>?
                </p>
                <p>
                    This action will create a new version that becomes the current version,
                    containing the same content as the selected version.
                </p>
            </Box>
        </Modal>
    );
};

// Main FileVersionsModal Component
export const FileVersionsModal: React.FC<FileVersionsModalProps> = ({
    visible,
    onDismiss,
    databaseId,
    assetId,
    filePath,
    fileName,
    currentVersionId,
    onVersionRevert,
}) => {
    const navigate = useNavigate();

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`File Versions - ${fileName}`}
            size="max"
            footer={
                <Box float="right">
                    <Button variant="link" onClick={onDismiss}>
                        Close
                    </Button>
                </Box>
            }
        >
            <FileVersionsTable
                databaseId={databaseId}
                assetId={assetId}
                filePath={filePath}
                fileName={fileName}
                currentVersionId={currentVersionId}
                onVersionRevert={onVersionRevert}
                displayMode="modal"
                visible={visible}
            />
        </Modal>
    );
};

export default FileVersionsModal;
