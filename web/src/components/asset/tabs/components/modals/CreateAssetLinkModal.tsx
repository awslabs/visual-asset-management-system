/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Box,
    Button,
    FormField,
    Input,
    Modal,
    SpaceBetween,
    Alert,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { fetchtagTypes } from "../../../../../services/APIService";
import { useStatusMessage } from "../../../../common/StatusMessage";
import { CreateAssetLinkModalProps } from "../../types/AssetLinksTypes";
import { AssetSearchTable, AssetSearchItem } from "../../../../searchSmall/AssetSearchTable";

export function CreateAssetLinkModal({
    visible,
    onDismiss,
    relationshipType,
    currentAssetId,
    currentDatabaseId,
    onSuccess,
    noOpenSearch = false,
    isSubChildMode = false,
    parentAssetData = null,
}: CreateAssetLinkModalProps) {
    // Check if we're in upload mode (temporary IDs indicate upload mode)
    const isUploadMode =
        currentAssetId === "temp-upload-asset" || currentDatabaseId === "temp-upload-db";
    const { showMessage } = useStatusMessage();
    const [formError, setFormError] = useState("");
    const [addDisabled, setAddDisabled] = useState(false);
    const [tagTypes, setTagTypes] = useState<any[]>([]);
    const [aliasId, setAliasId] = useState<string>("");
    const [aliasIdError, setAliasIdError] = useState<string>("");

    // Selected assets from the search table
    const [selectedAssets, setSelectedAssets] = useState<AssetSearchItem[]>([]);

    // Reset state when modal visibility changes
    useEffect(() => {
        if (!visible) {
            // Reset all state when modal is closed
            setSelectedAssets([]);
            setFormError("");
            setAddDisabled(false);
            setAliasId("");
            setAliasIdError("");
        }
    }, [visible]);

    // Validation function for alias ID
    const validateAliasId = (value: string): string => {
        if (!value) return ""; // Optional field
        if (value.length > 128) {
            return "Alias ID must be 128 characters or less";
        }
        // Alphanumeric with hyphens and underscores
        if (!/^[a-zA-Z0-9_-]+$/.test(value)) {
            return "Alias ID can only contain letters, numbers, hyphens, and underscores";
        }
        return "";
    };

    // Fetch tag types when component mounts
    useEffect(() => {
        const loadTagTypes = async () => {
            try {
                const result = await fetchtagTypes();
                if (result && Array.isArray(result)) {
                    setTagTypes(result);
                }
            } catch (error) {
                console.error("Error fetching tag types:", error);
            }
        };

        loadTagTypes();
    }, []);

    // Handle assets selected from search table
    const handleAssetsSelected = (assets: AssetSearchItem[]) => {
        setSelectedAssets(assets);
    };

    // Add link
    const addLink = async () => {
        if (selectedAssets.length === 0) {
            setFormError("Please select at least one asset to link.");
            return;
        }

        if (isUploadMode) {
            // In upload mode, just add to local state (handled by parent component)
            try {
                setAddDisabled(true);

                // Create asset node from the first selected item (upload mode doesn't support multi-select yet)
                const selectedAsset = selectedAssets[0];
                // Generate unique temp ID that includes alias to distinguish multiple links to same asset
                const tempId = aliasId
                    ? `temp-${selectedAsset.assetId}-${relationshipType}-${aliasId}`
                    : `temp-${selectedAsset.assetId}-${relationshipType}-no-alias`;

                const assetNode = {
                    assetId: selectedAsset.assetId,
                    assetName: selectedAsset.assetName,
                    databaseId: selectedAsset.databaseName || selectedAsset.databaseId,
                    assetLinkId: tempId,
                    ...(aliasId && relationshipType !== "related"
                        ? { assetLinkAliasId: aliasId }
                        : {}),
                };

                // Show success message
                showMessage({
                    type: "success",
                    message: `Successfully added ${relationshipType} link`,
                    dismissible: true,
                    autoDismiss: true,
                });

                handleClose();
                // Pass the selected asset data to the parent
                onSuccess(assetNode, relationshipType);
            } catch (err: any) {
                console.error("Error adding asset link:", err);
                setFormError("Unable to add asset link. Please try again.");
            } finally {
                setAddDisabled(false);
            }
            return;
        }

        // View mode - make API calls for each selected asset
        setAddDisabled(true);
        let successCount = 0;
        let errorCount = 0;
        // Track errors with asset names and error messages
        const errors: { assetName: string; message: string }[] = [];

        try {
            // Process each selected asset
            for (const selectedAsset of selectedAssets) {
                const assetLinkBody: any = {
                    fromAssetId: "",
                    fromAssetDatabaseId: "",
                    toAssetId: "",
                    toAssetDatabaseId: "",
                    relationshipType: "",
                };

                // Add alias ID if provided and relationship is parent/child
                if (aliasId && relationshipType !== "related") {
                    assetLinkBody.assetLinkAliasId = aliasId;
                }

                // Special handling for sub-child mode
                if (isSubChildMode && parentAssetData) {
                    // When creating a sub-child link, the parent is the selected node in the tree view
                    // and the child is the selected asset in the modal
                    assetLinkBody.fromAssetId = parentAssetData.assetId;
                    assetLinkBody.fromAssetDatabaseId = parentAssetData.databaseId;
                    assetLinkBody.toAssetId = selectedAsset.assetId;
                    assetLinkBody.toAssetDatabaseId =
                        selectedAsset.databaseName || selectedAsset.databaseId;
                    assetLinkBody.relationshipType = "parentChild";
                } else {
                    // Normal relationship handling
                    switch (relationshipType) {
                        case "parent":
                            // When adding a parent: selected asset is the parent (from), current asset is the child (to)
                            assetLinkBody.fromAssetId = selectedAsset.assetId;
                            assetLinkBody.fromAssetDatabaseId =
                                selectedAsset.databaseName || selectedAsset.databaseId;
                            assetLinkBody.toAssetId = currentAssetId;
                            assetLinkBody.toAssetDatabaseId = currentDatabaseId;
                            assetLinkBody.relationshipType = "parentChild";
                            break;
                        case "child":
                            // When adding a child: current asset is the parent (from), selected asset is the child (to)
                            assetLinkBody.fromAssetId = currentAssetId;
                            assetLinkBody.fromAssetDatabaseId = currentDatabaseId;
                            assetLinkBody.toAssetId = selectedAsset.assetId;
                            assetLinkBody.toAssetDatabaseId =
                                selectedAsset.databaseName || selectedAsset.databaseId;
                            assetLinkBody.relationshipType = "parentChild";
                            break;
                        case "related":
                            assetLinkBody.fromAssetId = currentAssetId;
                            assetLinkBody.fromAssetDatabaseId = currentDatabaseId;
                            assetLinkBody.toAssetId = selectedAsset.assetId;
                            assetLinkBody.toAssetDatabaseId =
                                selectedAsset.databaseName || selectedAsset.databaseId;
                            assetLinkBody.relationshipType = "related";
                            break;
                    }
                }

                try {
                    await API.post("api", "asset-links", {
                        body: assetLinkBody,
                    });
                    successCount++;
                } catch (err: any) {
                    console.error("Error creating asset link:", err);
                    errorCount++;

                    // Extract error message from the response
                    let errorMessage = "Unknown error";
                    try {
                        if (err.response && err.response.data) {
                            errorMessage = err.response.data.message || err.message;
                        } else if (err.message) {
                            // Try to parse JSON error message
                            const jsonMatch = err.message.match(/\{.*\}/);
                            if (jsonMatch) {
                                const jsonError = JSON.parse(jsonMatch[0]);
                                errorMessage = jsonError.message || err.message;
                            } else {
                                errorMessage = err.message;
                            }
                        }
                    } catch (parseError) {
                        errorMessage = err.message || "Unknown error";
                    }

                    // Add to errors array
                    errors.push({
                        assetName: selectedAsset.assetName,
                        message: errorMessage,
                    });
                }
            }

            // Show appropriate message based on results
            if (successCount > 0 && errorCount === 0) {
                showMessage({
                    type: "success",
                    message: `Successfully added ${successCount} ${relationshipType} link${
                        successCount !== 1 ? "s" : ""
                    }`,
                    dismissible: true,
                    autoDismiss: true,
                });
                handleClose();
                onSuccess();
            } else if (successCount > 0 && errorCount > 0) {
                // Format error messages for display
                const errorMessages = errors
                    .map((error) => `• ${error.assetName}: ${error.message}`)
                    .join("\n");

                setFormError(
                    `Added ${successCount} link${
                        successCount !== 1 ? "s" : ""
                    } successfully, but ${errorCount} failed.\n\nErrors:\n${errorMessages}`
                );
            } else if (errorCount > 0) {
                // Format error messages for display
                const errorMessages = errors
                    .map((error) => `• ${error.assetName}: ${error.message}`)
                    .join("\n");

                setFormError(
                    `Failed to add any ${relationshipType} links.\n\nErrors:\n${errorMessages}`
                );
            } else {
                setFormError(`No asset links were processed. Please try again.`);
            }
        } catch (err: any) {
            console.error("Error in batch processing:", err);
            setFormError(`Error processing links: ${err.message || "Unknown error"}`);
        } finally {
            setAddDisabled(false);
        }
    };

    const handleClose = () => {
        setSelectedAssets([]);
        setFormError("");
        setAddDisabled(false);
        setAliasId("");
        setAliasIdError("");
        onDismiss();
    };

    const relationshipDisplayName =
        relationshipType.charAt(0).toUpperCase() + relationshipType.slice(1);

    // Custom header component that includes the alert above the title
    const modalHeader = (
        <div>
            {formError && (
                <div style={{ marginBottom: "10px" }}>
                    <Alert type="error" dismissible onDismiss={() => setFormError("")}>
                        <div style={{ fontWeight: "bold" }}>Error</div>
                        <div style={{ whiteSpace: "pre-line" }}>{formError}</div>
                    </Alert>
                </div>
            )}
            <h2>{`Create ${relationshipDisplayName} Asset Link`}</h2>
        </div>
    );

    return (
        <Modal
            visible={visible}
            onDismiss={handleClose}
            size="large"
            header={modalHeader}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleClose}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            disabled={addDisabled || selectedAssets.length === 0}
                            onClick={addLink}
                        >
                            Create Link
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <div style={{ width: "100%", maxWidth: "none" }}>
                {/* Sub-child mode warning */}
                {isSubChildMode && parentAssetData && (
                    <div style={{ marginBottom: "20px" }}>
                        <Alert type="warning" dismissible={false}>
                            <div>
                                <strong>Sub-Child Asset Link:</strong> You are adding a child asset
                                to "{parentAssetData.assetName}" (not the top-level asset being
                                viewed).
                            </div>
                            <div style={{ marginTop: "8px" }}>
                                This will create a relationship between the selected asset below and
                                "{parentAssetData.assetName}", which is itself a child of the main
                                asset being viewed.
                            </div>
                        </Alert>
                    </div>
                )}

                <SpaceBetween direction="vertical" size="l">
                    {/* Alias ID Field - Only for parent/child relationships */}
                    {(relationshipType === "parent" || relationshipType === "child") && (
                        <FormField
                            label="Alias ID (Optional)"
                            description="Unique identifier for this parent-child relationship. Allows multiple relationships between the same parent and child."
                            constraintText="Maximum 128 characters. Alphanumeric with hyphens and underscores."
                            errorText={aliasIdError}
                        >
                            <Input
                                value={aliasId}
                                onChange={({ detail }) => {
                                    setAliasId(detail.value);
                                    setAliasIdError(validateAliasId(detail.value));
                                }}
                                placeholder="e.g., v1, version-2, config-a"
                                disabled={addDisabled}
                            />
                        </FormField>
                    )}

                    {/* Asset Search Table */}
                    <AssetSearchTable
                        key={visible ? "open" : "closed"} // Force re-render when modal opens/closes
                        selectionMode="multi"
                        currentAssetId={currentAssetId}
                        currentDatabaseId={currentDatabaseId}
                        onAssetsSelect={handleAssetsSelected}
                        showDatabaseColumn={true}
                        showTagsColumn={true}
                        showSelectedAssets={true}
                        tagTypes={tagTypes}
                        noOpenSearch={noOpenSearch}
                    />
                </SpaceBetween>
            </div>
        </Modal>
    );
}
