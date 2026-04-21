/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import { AssetVersion } from "../AssetVersionManager";
import { archiveAssetVersion } from "../../../../services/AssetVersionService";

interface ArchiveVersionModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: () => void;
    version: AssetVersion;
    databaseId: string;
    assetId: string;
}

export const ArchiveVersionModal: React.FC<ArchiveVersionModalProps> = ({
    visible,
    onDismiss,
    onSuccess,
    version,
    databaseId,
    assetId,
}) => {
    const [archiving, setArchiving] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

    const handleArchive = async () => {
        setError(null);
        setArchiving(true);

        try {
            const [success, response] = await archiveAssetVersion({
                databaseId,
                assetId,
                assetVersionId: version.Version,
            });

            if (success) {
                onSuccess();
            } else {
                setError(typeof response === "string" ? response : "Failed to archive version");
            }
        } catch (err: any) {
            setError(err?.message || "An unexpected error occurred");
        } finally {
            setArchiving(false);
        }
    };

    const versionLabel = version?.versionAlias
        ? `Version ${version.Version} (${version.versionAlias})`
        : `Version ${version?.Version}`;

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Archive ${versionLabel}`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onDismiss} disabled={archiving}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={handleArchive} loading={archiving}>
                            Archive
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

                <Box>
                    Are you sure you want to archive {versionLabel}? This version will be hidden
                    from the active versions list.
                </Box>

                <Alert type="info">
                    Archived versions can be restored later using the Unarchive action.
                </Alert>
            </SpaceBetween>
        </Modal>
    );
};
