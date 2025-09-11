import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    Select,
    Input,
    Alert,
} from "@cloudscape-design/components";
import { SetPrimaryTypeModalProps } from "../types/FileManagerTypes";
import { setPrimaryType } from "../../../services/APIService";

const PRIMARY_TYPE_OPTIONS = [
    { label: "None", value: "" },
    { label: "Primary", value: "primary" },
    { label: "LOD1", value: "lod1" },
    { label: "LOD2", value: "lod2" },
    { label: "LOD3", value: "lod3" },
    { label: "LOD4", value: "lod4" },
    { label: "LOD5", value: "lod5" },
    { label: "Other", value: "other" },
];

export function SetPrimaryTypeModal({
    visible,
    onDismiss,
    selectedFiles,
    databaseId,
    assetId,
    onSuccess,
}: SetPrimaryTypeModalProps) {
    const [selectedPrimaryType, setSelectedPrimaryType] = useState<{
        label: string;
        value: string;
    } | null>(null);
    const [otherPrimaryType, setOtherPrimaryType] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Initialize form when modal opens
    useEffect(() => {
        if (visible && selectedFiles.length > 0) {
            setError(null);

            if (selectedFiles.length === 1) {
                // Single file - pre-populate with existing values
                const file = selectedFiles[0];
                const currentPrimaryType = file.primaryType;

                if (!currentPrimaryType) {
                    // No primary type set
                    setSelectedPrimaryType(PRIMARY_TYPE_OPTIONS[0]); // "None"
                    setOtherPrimaryType("");
                } else {
                    // Check if it matches one of the predefined options
                    const matchingOption = PRIMARY_TYPE_OPTIONS.find(
                        (option) => option.value === currentPrimaryType
                    );

                    if (matchingOption) {
                        setSelectedPrimaryType(matchingOption);
                        setOtherPrimaryType("");
                    } else {
                        // It's a custom value, set to "Other"
                        setSelectedPrimaryType(
                            PRIMARY_TYPE_OPTIONS.find((opt) => opt.value === "other") || null
                        );
                        setOtherPrimaryType(currentPrimaryType);
                    }
                }
            } else {
                // Multiple files - start with "None"
                setSelectedPrimaryType(PRIMARY_TYPE_OPTIONS[0]);
                setOtherPrimaryType("");
            }
        }
    }, [visible, selectedFiles]);

    const handleSubmit = async () => {
        if (!selectedPrimaryType) {
            setError("Please select a primary type");
            return;
        }

        if (selectedPrimaryType.value === "other" && !otherPrimaryType.trim()) {
            setError("Please enter a value for Other Primary Type");
            return;
        }

        if (otherPrimaryType.length > 30) {
            setError("Other Primary Type must be 30 characters or less");
            return;
        }

        setIsLoading(true);
        setError(null);

        try {
            let successCount = 0;
            let errorMessages: string[] = [];

            // Process each selected file
            for (const file of selectedFiles) {
                const [success, message] = await setPrimaryType({
                    databaseId,
                    assetId,
                    filePath: file.relativePath,
                    primaryType: selectedPrimaryType.value,
                    primaryTypeOther:
                        selectedPrimaryType.value === "other" ? otherPrimaryType.trim() : "",
                });

                if (success) {
                    successCount++;
                } else {
                    errorMessages.push(`${file.name}: ${message}`);
                }
            }

            if (successCount === selectedFiles.length) {
                // All succeeded
                onSuccess();
                onDismiss();
            } else if (successCount > 0) {
                // Partial success
                setError(
                    `Successfully updated ${successCount} of ${
                        selectedFiles.length
                    } files. Errors: ${errorMessages.join(", ")}`
                );
                onSuccess(); // Still refresh to show the successful updates
            } else {
                // All failed
                setError(`Failed to update primary type: ${errorMessages.join(", ")}`);
            }
        } catch (error) {
            console.error("Error setting primary type:", error);
            setError("An unexpected error occurred while setting primary type");
        } finally {
            setIsLoading(false);
        }
    };

    const handleCancel = () => {
        setError(null);
        onDismiss();
    };

    const isOtherSelected = selectedPrimaryType?.value === "other";
    const fileCount = selectedFiles.length;
    const isSingleFile = fileCount === 1;

    return (
        <Modal
            onDismiss={handleCancel}
            visible={visible}
            closeAriaLabel="Close modal"
            size="medium"
            header={`Set Primary Type${isSingleFile ? "" : ` (${fileCount} files)`}`}
        >
            <SpaceBetween direction="vertical" size="l">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                {!isSingleFile && (
                    <Box>
                        <strong>Selected files:</strong>
                        <ul style={{ marginTop: "8px", marginBottom: "0" }}>
                            {selectedFiles.slice(0, 5).map((file) => (
                                <li key={file.keyPrefix}>{file.name}</li>
                            ))}
                            {selectedFiles.length > 5 && (
                                <li>... and {selectedFiles.length - 5} more files</li>
                            )}
                        </ul>
                    </Box>
                )}

                <FormField
                    label="Primary Type"
                    description="Select the primary type for the file(s). Choose 'None' to remove the primary type."
                >
                    <Select
                        selectedOption={selectedPrimaryType}
                        onChange={({ detail }) => {
                            setSelectedPrimaryType(
                                detail.selectedOption as { label: string; value: string }
                            );
                            if (detail.selectedOption.value !== "other") {
                                setOtherPrimaryType("");
                            }
                        }}
                        options={PRIMARY_TYPE_OPTIONS}
                        placeholder="Select primary type"
                        disabled={isLoading}
                    />
                </FormField>

                {isOtherSelected && (
                    <FormField
                        label="Other Primary Type"
                        description="Enter a custom primary type (maximum 30 characters)"
                        errorText={
                            otherPrimaryType.length > 30
                                ? "Maximum 30 characters allowed"
                                : undefined
                        }
                    >
                        <Input
                            value={otherPrimaryType}
                            onChange={({ detail }) => setOtherPrimaryType(detail.value)}
                            placeholder="Enter custom primary type"
                            disabled={isLoading}
                            invalid={otherPrimaryType.length > 30}
                        />
                    </FormField>
                )}

                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleCancel} disabled={isLoading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            loading={isLoading}
                            disabled={
                                !selectedPrimaryType ||
                                (isOtherSelected && !otherPrimaryType.trim())
                            }
                        >
                            Set Primary Type
                        </Button>
                    </SpaceBetween>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
