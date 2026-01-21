/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    Input,
    Checkbox,
    Alert,
} from "@cloudscape-design/components";
import { MetadataSchema } from "./types";

interface DeleteSchemaModalProps {
    visible: boolean;
    onDismiss: () => void;
    onConfirm: () => Promise<void>;
    schema: MetadataSchema | null;
}

export const DeleteSchemaModal: React.FC<DeleteSchemaModalProps> = ({
    visible,
    onDismiss,
    onConfirm,
    schema,
}) => {
    const [confirmChecked, setConfirmChecked] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleConfirm = async () => {
        if (!confirmChecked) {
            setError("You must confirm deletion by checking the box");
            return;
        }

        setLoading(true);
        setError(null);

        try {
            await onConfirm();
            // Reset form
            setConfirmChecked(false);
            onDismiss();
        } catch (err: any) {
            console.error("Error deleting schema:", err);
            setError(err.message || "Failed to delete schema");
        } finally {
            setLoading(false);
        }
    };

    const handleDismiss = () => {
        setConfirmChecked(false);
        setError(null);
        onDismiss();
    };

    if (!schema) {
        return null;
    }

    return (
        <Modal
            visible={visible}
            onDismiss={handleDismiss}
            size="medium"
            header="Delete Metadata Schema"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleConfirm}
                            loading={loading}
                            disabled={!confirmChecked || loading}
                        >
                            Delete Schema
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween size="m">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                <Alert type="warning">
                    You are about to delete the metadata schema "{schema.schemaName}". This action
                    cannot be undone.
                </Alert>

                <Box>
                    <SpaceBetween size="xs">
                        <Box variant="awsui-key-label">Schema Name</Box>
                        <Box>{schema.schemaName}</Box>
                    </SpaceBetween>
                </Box>

                <Box>
                    <SpaceBetween size="xs">
                        <Box variant="awsui-key-label">Entity Type</Box>
                        <Box>{schema.metadataSchemaEntityType}</Box>
                    </SpaceBetween>
                </Box>

                <Box>
                    <SpaceBetween size="xs">
                        <Box variant="awsui-key-label">Number of Fields</Box>
                        <Box>{schema.fields.fields.length}</Box>
                    </SpaceBetween>
                </Box>

                <FormField>
                    <Checkbox
                        checked={confirmChecked}
                        onChange={({ detail }) => setConfirmChecked(detail.checked)}
                    >
                        I understand that this action cannot be undone and confirm deletion of this
                        schema
                    </Checkbox>
                </FormField>
            </SpaceBetween>
        </Modal>
    );
};
