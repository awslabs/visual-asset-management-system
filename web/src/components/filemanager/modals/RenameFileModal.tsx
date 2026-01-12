import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    Input,
    Alert,
} from "@cloudscape-design/components";
import { FileTree } from "../types/FileManagerTypes";
import { moveFile } from "../../../services/FileOperationsService";

export interface RenameFileModalProps {
    visible: boolean;
    onDismiss: () => void;
    selectedFile: FileTree | null;
    databaseId: string;
    assetId: string;
    onSuccess: () => void;
}

export function RenameFileModal({
    visible,
    onDismiss,
    selectedFile,
    databaseId,
    assetId,
    onSuccess,
}: RenameFileModalProps) {
    const [newFileName, setNewFileName] = useState("");
    const [isProcessing, setIsProcessing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible && selectedFile) {
            // Pre-populate with current filename
            setNewFileName(selectedFile.name);
            setError(null);
        } else {
            setNewFileName("");
            setError(null);
        }
    }, [visible, selectedFile]);

    // Validate filename
    const validateFileName = (filename: string): string | null => {
        // Check if filename is blank or only whitespace
        if (!filename || filename.trim() === "") {
            return "Filename cannot be blank";
        }

        // Check if filename has an extension (contains a dot with characters after it)
        const lastDotIndex = filename.lastIndexOf(".");
        if (lastDotIndex === -1 || lastDotIndex === filename.length - 1) {
            return "Filename must have an extension (e.g., .txt, .jpg, .pdf)";
        }

        // Check if there are characters before the extension
        if (lastDotIndex === 0) {
            return "Filename must have a name before the extension";
        }

        return null;
    };

    // Handle rename operation
    const handleRename = async () => {
        if (!selectedFile) return;

        // Validate filename
        const validationError = validateFileName(newFileName);
        if (validationError) {
            setError(validationError);
            return;
        }

        // Check if filename hasn't changed
        if (newFileName === selectedFile.name) {
            setError("New filename is the same as the current filename");
            return;
        }

        setIsProcessing(true);
        setError(null);

        try {
            // Extract the directory path from the current file path
            const currentPath = selectedFile.relativePath;
            const lastSlashIndex = currentPath.lastIndexOf("/");
            const directoryPath =
                lastSlashIndex > 0 ? currentPath.substring(0, lastSlashIndex + 1) : "/";

            // Construct the new path with the new filename
            const destinationPath =
                directoryPath === "/" ? `/${newFileName}` : `${directoryPath}${newFileName}`;

            // Call the move API to rename the file
            const response = await moveFile(databaseId, assetId, {
                sourcePath: currentPath,
                destinationPath: destinationPath,
            });

            if (response.success) {
                // Call onSuccess to refresh the file list and clear selection
                onSuccess();
            } else {
                setError(response.message || "Failed to rename file");
            }
        } catch (error: any) {
            setError(error.message || "Failed to rename file");
        } finally {
            setIsProcessing(false);
        }
    };

    // Handle modal dismiss
    const handleDismiss = () => {
        if (!isProcessing) {
            onDismiss();
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={handleDismiss}
            size="medium"
            header="Rename File"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleDismiss} disabled={isProcessing}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleRename}
                            disabled={isProcessing}
                            loading={isProcessing}
                        >
                            Rename
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Current filename display */}
                {selectedFile && (
                    <Box>
                        <strong>Current filename:</strong> {selectedFile.name}
                    </Box>
                )}

                {/* New filename input */}
                <FormField
                    label="New filename"
                    description="Enter the new filename including the extension"
                    errorText={error}
                >
                    <Input
                        value={newFileName}
                        onChange={({ detail }) => {
                            setNewFileName(detail.value);
                            setError(null);
                        }}
                        placeholder="Enter new filename"
                        disabled={isProcessing}
                        onKeyDown={(e) => {
                            if (e.detail.key === "Enter" && !isProcessing) {
                                handleRename();
                            }
                        }}
                    />
                </FormField>

                {/* Info alert */}
                <Alert type="info" statusIconAriaLabel="Info">
                    This will rename the file by moving it to the same location with a new name. The
                    file will be archived and a new version will be created with the new name.
                </Alert>
            </SpaceBetween>
        </Modal>
    );
}
