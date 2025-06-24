import React, { useEffect, useState } from "react";
import {
    Box,
    Button,
    SpaceBetween,
    Modal,
    FormField,
    Input,
    Form,
} from "@cloudscape-design/components";
import { CreateFolderModalProps } from "../types/FileManagerTypes";

export function CreateFolderModal({
    visible,
    onDismiss,
    onSubmit,
    parentFolder,
    isLoading,
}: CreateFolderModalProps) {
    const [folderName, setFolderName] = useState("");
    const [error, setError] = useState("");

    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setFolderName("");
            setError("");
        }
    }, [visible]);

    // Validate folder name
    const validateFolderName = (name: string): boolean => {
        if (!name || name.trim() === "") {
            setError("Folder name cannot be empty");
            return false;
        }

        // Check for invalid characters (/, \, :, *, ?, ", <, >, |)
        const invalidChars = /[/\\:*?"<>|]/;
        if (invalidChars.test(name)) {
            setError('Folder name contains invalid characters (/, \\, :, *, ?, ", <, >, |)');
            return false;
        }

        setError("");
        return true;
    };

    const handleSubmit = () => {
        if (validateFolderName(folderName)) {
            onSubmit(folderName);
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Create sub-folder in ${parentFolder || "root"}`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={handleSubmit} loading={isLoading}>
                            Create
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={error}>
                <FormField label="Folder name" description="Enter a name for the new folder">
                    <Input
                        value={folderName}
                        onChange={({ detail }) => setFolderName(detail.value)}
                        placeholder="New folder"
                        autoFocus
                    />
                </FormField>
            </Form>
        </Modal>
    );
}
