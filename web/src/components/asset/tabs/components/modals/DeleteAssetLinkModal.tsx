/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Box, Button, Modal, SpaceBetween } from "@cloudscape-design/components";
import { deleteAssetLink } from "../../../../../services/APIService";
import { useStatusMessage } from "../../../../common/StatusMessage";
import { DeleteAssetLinkModalProps } from "../../types/AssetLinksTypes";
import Synonyms from "../../../../../synonyms";

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

            await deleteAssetLink({ relationId: assetLinkId });

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
                        error.response?.data?.message ||
                        `Not authorized to delete this ${Synonyms.asset} link`,
                    dismissible: true,
                });
            } else {
                showMessage({
                    type: "error",
                    message: `Failed to delete ${Synonyms.asset} link. Please try again.`,
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
            header={`Delete ${Synonyms.Asset} Link`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Cancel
                        </Button>
                        <Button variant="primary" disabled={deleteDisabled} onClick={handleDelete}>
                            {isSubChild && parentAssetName
                                ? `Delete Sub-Child ${Synonyms.Asset} Link`
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
                            <strong>Important:</strong>{" "}
                            {`Removing this ${Synonyms.asset} link will only break the connection between the current ${Synonyms.asset} and the selected ${Synonyms.asset}.`}
                        </div>
                        <div>
                            It will <strong>not</strong> recursively delete any sub-relationships
                            {`that may exist below the selected ${Synonyms.asset} in the hierarchy.`}
                        </div>
                        {isSubChild && parentAssetName && (
                            <div>
                                <strong>Sub-Child Deletion:</strong>{" "}
                                {`You are deleting a sub-child ${Synonyms.asset} link. This affects the relationship between "${parentAssetName}" and "${assetName}".`}
                            </div>
                        )}
                    </SpaceBetween>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
