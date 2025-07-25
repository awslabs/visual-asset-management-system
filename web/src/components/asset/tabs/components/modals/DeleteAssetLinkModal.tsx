/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Box, Button, Modal, SpaceBetween } from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useStatusMessage } from "../../../../common/StatusMessage";
import { DeleteAssetLinkModalProps } from "../../types/AssetLinksTypes";

export function DeleteAssetLinkModal({
    visible,
    onDismiss,
    assetLinkId,
    assetName,
    relationshipType,
    onSuccess,
    isSubChild = false,
    parentAssetName,
}: DeleteAssetLinkModalProps) {
    const { showMessage } = useStatusMessage();
    const [deleteDisabled, setDeleteDisabled] = useState(false);

    const handleDelete = async () => {
        try {
            setDeleteDisabled(true);

            await API.del("api", `asset-links/${assetLinkId}`, {});

            showMessage({
                type: "success",
                message: `Successfully deleted ${relationshipType} link to ${assetName}`,
                dismissible: true,
                autoDismiss: true,
            });

            onDismiss();
            onSuccess();
        } catch (error: any) {
            console.error("Error deleting asset link:", error);

            if (error.response?.status === 403) {
                showMessage({
                    type: "error",
                    message:
                        error.response?.data?.message || "Not authorized to delete this asset link",
                    dismissible: true,
                });
            } else {
                showMessage({
                    type: "error",
                    message: "Failed to delete asset link. Please try again.",
                    dismissible: true,
                });
            }
        } finally {
            setDeleteDisabled(false);
        }
    };

    const relationshipDisplayName =
        relationshipType.charAt(0).toUpperCase() + relationshipType.slice(1);

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            size="medium"
            header="Delete Asset Link"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Cancel
                        </Button>
                        <Button variant="primary" disabled={deleteDisabled} onClick={handleDelete}>
                            {isSubChild && parentAssetName
                                ? "Delete Sub-Child Asset Link"
                                : "Delete Link"}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                <div>
                    <p>
                        Do you want to delete the <strong>{relationshipDisplayName}</strong> link to{" "}
                        <strong>'{assetName}'</strong>?
                    </p>
                </div>

                <Box variant="div" padding="s" color="text-status-warning">
                    <SpaceBetween direction="vertical" size="xs">
                        <div>
                            <strong>Important:</strong> Removing this asset link will only break the
                            connection between the current asset and the selected asset.
                        </div>
                        <div>
                            It will <strong>not</strong> recursively delete any sub-relationships
                            that may exist below the selected asset in the hierarchy.
                        </div>
                        {isSubChild && parentAssetName && (
                            <div>
                                <strong>Sub-Child Deletion:</strong> You are deleting a sub-child
                                asset link. This affects the relationship between "{parentAssetName}
                                " and "{assetName}".
                            </div>
                        )}
                    </SpaceBetween>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
