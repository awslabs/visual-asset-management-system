/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import { AssetVersion } from "../AssetVersionManager";
import { updateAssetVersion } from "../../../../services/AssetVersionService";

interface EditVersionModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: () => void;
    version: AssetVersion;
    databaseId: string;
    assetId: string;
}

export const EditVersionModal: React.FC<EditVersionModalProps> = ({
    visible,
    onDismiss,
    onSuccess,
    version,
    databaseId,
    assetId,
}) => {
    const [versionAlias, setVersionAlias] = useState<string>("");
    const [comment, setComment] = useState<string>("");
    const [saving, setSaving] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

    // Pre-populate fields when version changes or modal opens
    useEffect(() => {
        if (visible && version) {
            setVersionAlias(version.versionAlias || "");
            setComment(version.Comment || "");
            setError(null);
        }
    }, [visible, version]);

    const handleSave = async () => {
        setError(null);
        setSaving(true);

        try {
            const body: { comment?: string; versionAlias?: string } = {};

            // Include comment if it changed
            if (comment !== (version.Comment || "")) {
                body.comment = comment;
            }

            // Include alias if it changed
            if (versionAlias !== (version.versionAlias || "")) {
                body.versionAlias = versionAlias;
            }

            // If nothing changed, just close
            if (Object.keys(body).length === 0) {
                onDismiss();
                return;
            }

            const [success, response] = await updateAssetVersion({
                databaseId,
                assetId,
                assetVersionId: version.Version,
                body,
            });

            if (success) {
                onSuccess();
            } else {
                setError(typeof response === "string" ? response : "Failed to update version");
            }
        } catch (err: any) {
            setError(err?.message || "An unexpected error occurred");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Edit Version ${version?.Version}`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onDismiss} disabled={saving}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={handleSave} loading={saving}>
                            Save
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

                <FormField
                    label="Version Alias"
                    description="An optional short name for this version (e.g., RC1, GA, Beta). Max 64 characters."
                    constraintText={`${versionAlias.length}/64 characters`}
                >
                    <Input
                        value={versionAlias}
                        onChange={({ detail }) => {
                            if (detail.value.length <= 64) {
                                setVersionAlias(detail.value);
                            }
                        }}
                        placeholder="e.g., RC1, GA, Beta"
                        disabled={saving}
                    />
                </FormField>

                <FormField
                    label="Comment"
                    description="An optional comment for this version. Max 1024 characters."
                    constraintText={`${comment.length}/1024 characters`}
                >
                    <Textarea
                        value={comment}
                        onChange={({ detail }) => {
                            if (detail.value.length <= 1024) {
                                setComment(detail.value);
                            }
                        }}
                        placeholder="Version comment"
                        rows={4}
                        disabled={saving}
                    />
                </FormField>
            </SpaceBetween>
        </Modal>
    );
};
