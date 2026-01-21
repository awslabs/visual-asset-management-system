/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    Box,
    Button,
    Modal,
    SpaceBetween,
    Alert,
    FormField,
    Textarea,
    Toggle,
} from "@cloudscape-design/components";
import { useParams } from "react-router";
import { revertAssetVersion } from "../../../../services/AssetVersionService";
import { AssetVersion } from "../AssetVersionManager";

interface RevertVersionModalProps {
    visible: boolean;
    onDismiss: () => void;
    version: AssetVersion;
    onSuccess: () => void;
}

export const RevertVersionModal: React.FC<RevertVersionModalProps> = ({
    visible,
    onDismiss,
    version,
    onSuccess,
}) => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();

    // State
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const [comment, setComment] = useState<string>("");
    const [revertMetadata, setRevertMetadata] = useState<boolean>(false);

    // Handle revert
    const handleRevert = async () => {
        if (!databaseId || !assetId) {
            setError("Database ID and Asset ID are required");
            return;
        }

        setLoading(true);
        setError(null);

        try {
            const [success, response] = await revertAssetVersion({
                databaseId,
                assetId,
                assetVersionId: `${version.Version}`,
                comment,
                revertMetadata,
            });

            if (success) {
                onSuccess();
            } else {
                setError(typeof response === "string" ? response : "Failed to revert version");
            }
        } catch (err) {
            setError("An error occurred while reverting the version");
            console.error("Error reverting version:", err);
        } finally {
            setLoading(false);
        }
    };

    // Format date
    const formatDate = (dateString: string): string => {
        try {
            const date = new Date(dateString);
            return date.toLocaleString();
        } catch (e) {
            return dateString;
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Revert to Version v${version.Version}`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleRevert}
                            loading={loading}
                            disabled={loading || !comment.trim()}
                        >
                            Revert
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

                <Alert type="warning">
                    <div>
                        <strong>Warning:</strong> Reverting to this version will create a new
                        version that matches the state of version v{version.Version}.
                    </div>
                    <div>
                        Permanently deleted files will not be restored and will be discarded from
                        the new version.
                    </div>
                    {revertMetadata && (
                        <div style={{ marginTop: "8px" }}>
                            <strong>Note:</strong> Metadata and attributes will also be reverted to
                            match version v{version.Version}, replacing all current metadata and
                            attributes for the asset and its files.
                        </div>
                    )}
                </Alert>

                <Box>
                    <SpaceBetween direction="vertical" size="s">
                        <div>
                            <strong>Version:</strong> v{version.Version}
                        </div>
                        <div>
                            <strong>Created:</strong> {formatDate(version.DateModified)}
                        </div>
                        <div>
                            <strong>Created By:</strong> {version.createdBy || "System"}
                        </div>
                        {version.Comment && (
                            <div>
                                <strong>Comment:</strong> {version.Comment}
                            </div>
                        )}
                    </SpaceBetween>
                </Box>

                <FormField
                    label="Revert Options"
                    description="Choose what to revert from this version"
                >
                    <Toggle
                        checked={revertMetadata}
                        onChange={({ detail }) => setRevertMetadata(detail.checked)}
                    >
                        <Box variant="span">
                            <strong>Revert Metadata and Attributes</strong>
                            <Box variant="p" color="text-body-secondary" margin={{ top: "xxs" }}>
                                Also restore the metadata and attributes from this version to the
                                asset and files (default: off)
                            </Box>
                        </Box>
                    </Toggle>
                </FormField>

                <FormField
                    label="Revert Comment *"
                    description="Add a comment to describe this revert operation (required)"
                    errorText={!comment.trim() ? "Comment is required" : undefined}
                >
                    <Textarea
                        value={comment}
                        onChange={({ detail }) => setComment(detail.value)}
                        placeholder="Enter a comment for the revert operation"
                        invalid={!comment.trim()}
                    />
                </FormField>
            </SpaceBetween>
        </Modal>
    );
};
