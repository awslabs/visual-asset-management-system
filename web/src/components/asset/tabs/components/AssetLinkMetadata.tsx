/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import {
    Container,
    Header,
    Table,
    Button,
    Select,
    Input,
    SpaceBetween,
    Box,
    Alert,
    Icon,
    ButtonDropdown,
    Modal,
} from "@cloudscape-design/components";
import "./AssetLinkMetadata.css";
import { AssetLinkMetadata as AssetLinkMetadataType, TreeNodeItem } from "../types/AssetLinksTypes";
import { XYZInput } from "./XYZInput";
import {
    fetchAssetLinkMetadata,
    createAssetLinkMetadata,
    updateAssetLinkMetadata,
    deleteAssetLinkMetadata,
} from "../../../../services/APIService";

interface AssetLinkMetadataProps {
    assetLinkId: string;
    selectedNode: TreeNodeItem | null;
    mode?: "upload" | "view";
    onMetadataChange?: (metadata: AssetLinkMetadataType[]) => void;
    initialMetadata?: AssetLinkMetadataType[];
}

interface MetadataRow extends AssetLinkMetadataType {
    isEditing: boolean;
    editValue: string;
    editType: "XYZ" | "String";
    hasChanges: boolean;
    isNew: boolean;
}

const HARDCODED_KEYS = ["Translation", "Rotation", "Scale"];

export const AssetLinkMetadata: React.FC<AssetLinkMetadataProps> = ({
    assetLinkId,
    selectedNode,
    mode = "view",
    onMetadataChange,
    initialMetadata = [],
}) => {
    const [metadata, setMetadata] = useState<MetadataRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Confirmation dialog state
    const [confirmationDialog, setConfirmationDialog] = useState<{
        visible: boolean;
        type: "clear" | "delete";
        index: number;
        metadataKey: string;
    }>({
        visible: false,
        type: "clear",
        index: -1,
        metadataKey: "",
    });

    // Initialize hardcoded rows
    const initializeHardcodedRows = useCallback(
        (existingMetadata: AssetLinkMetadataType[]): MetadataRow[] => {
            console.log("initializeHardcodedRows called with:", existingMetadata);
            const rows: MetadataRow[] = [];

            // Add hardcoded rows first
            HARDCODED_KEYS.forEach((key) => {
                const existing = existingMetadata.find((m) => m.metadataKey === key);
                console.log(`Processing hardcoded key ${key}:`, existing);

                const hasValue =
                    existing && existing.metadataValue && existing.metadataValue.trim() !== "";

                rows.push({
                    assetLinkId,
                    metadataKey: key,
                    metadataValue: existing?.metadataValue || "",
                    metadataValueType: "XYZ",
                    isEditing: false,
                    editValue: existing?.metadataValue || "",
                    editType: "XYZ",
                    hasChanges: false,
                    isNew: !hasValue, // Only consider it new if there's no value or empty value
                });

                console.log(`Created row for ${key}:`, {
                    metadataValue: existing?.metadataValue || "",
                    isNew: !hasValue,
                    hasValue,
                });
            });

            // Add other metadata rows
            existingMetadata.forEach((meta) => {
                if (!HARDCODED_KEYS.includes(meta.metadataKey)) {
                    console.log("Adding custom metadata:", meta);
                    rows.push({
                        ...meta,
                        isEditing: false,
                        editValue: meta.metadataValue,
                        editType: meta.metadataValueType,
                        hasChanges: false,
                        isNew: false,
                    });
                }
            });

            console.log("Final initialized rows:", rows);
            return rows;
        },
        [assetLinkId]
    );

    // Load metadata - separate effect for each trigger
    useEffect(() => {
        console.log("[AssetLinkMetadata] useEffect triggered:", {
            assetLinkId,
            mode,
            initialMetadata,
        });

        if (mode === "upload") {
            // In upload mode, use initialMetadata instead of API call
            console.log(
                "[AssetLinkMetadata] Upload mode: Initializing metadata from initialMetadata"
            );

            // Only initialize if we don't have metadata yet, or if assetLinkId changed
            // This prevents overwriting local changes when parent re-renders
            setMetadata((prev) => {
                // If we have existing metadata for the same assetLinkId, keep it
                if (prev.length > 0 && prev[0].assetLinkId === assetLinkId) {
                    console.log(
                        "[AssetLinkMetadata] Keeping existing metadata to preserve local changes"
                    );
                    return prev;
                }

                // Otherwise, initialize with new data
                const rows = initializeHardcodedRows(initialMetadata);
                return rows;
            });
            return;
        }

        if (!assetLinkId) {
            // Clear metadata if no assetLinkId
            console.log("[AssetLinkMetadata] No assetLinkId, clearing metadata");
            setMetadata([]);
            return;
        }

        // Reset metadata state immediately when assetLinkId changes to prevent stale data
        setMetadata([]);
        setError(null);

        const loadMetadata = async () => {
            setLoading(true);

            try {
                console.log(
                    "[AssetLinkMetadata] View mode: Loading metadata from API for assetLinkId:",
                    assetLinkId
                );
                const response = await fetchAssetLinkMetadata({ assetLinkId });
                console.log("[AssetLinkMetadata] Raw API response:", response);
                console.log("[AssetLinkMetadata] Response type:", typeof response);
                console.log(
                    "[AssetLinkMetadata] Response keys:",
                    response ? Object.keys(response) : "null"
                );

                let metadataList: AssetLinkMetadataType[] = [];

                // Handle different response formats
                if (response && typeof response === "object") {
                    if (Array.isArray(response.metadata)) {
                        metadataList = response.metadata;
                        console.log(
                            "[AssetLinkMetadata] Found metadata array in response.metadata"
                        );
                    } else if (Array.isArray(response)) {
                        metadataList = response;
                        console.log("[AssetLinkMetadata] Response is directly an array");
                    } else if (
                        response.message &&
                        typeof response.message === "object" &&
                        Array.isArray(response.message.metadata)
                    ) {
                        metadataList = response.message.metadata;
                        console.log(
                            "[AssetLinkMetadata] Found metadata in response.message.metadata"
                        );
                    } else if (typeof response === "string" && response === "Success") {
                        // Handle case where API returns just "Success" string - means no metadata
                        metadataList = [];
                        console.log(
                            "[AssetLinkMetadata] API returned Success string - no metadata available"
                        );
                    } else {
                        console.log(
                            "[AssetLinkMetadata] Unexpected response format, checking for metadata property"
                        );
                        // Try to find metadata in any property
                        for (const key in response) {
                            if (Array.isArray(response[key])) {
                                metadataList = response[key];
                                console.log(
                                    `[AssetLinkMetadata] Found metadata array in response.${key}`
                                );
                                break;
                            }
                        }
                    }
                } else if (typeof response === "string" && response === "Success") {
                    // Handle direct string response
                    metadataList = [];
                    console.log(
                        "[AssetLinkMetadata] API returned Success string directly - no metadata available"
                    );
                }

                console.log(
                    "[AssetLinkMetadata] View mode: Received metadata from API:",
                    metadataList
                );
                const rows = initializeHardcodedRows(metadataList);
                console.log("[AssetLinkMetadata] View mode: Initialized rows:", rows);

                // Fix Issue 1: Remove race condition by directly setting metadata
                setMetadata(rows);

                if (onMetadataChange) {
                    onMetadataChange(metadataList);
                }
            } catch (err) {
                console.error("[AssetLinkMetadata] Error loading metadata:", err);
                setError("Failed to load metadata");
                // Still initialize hardcoded rows even on error
                const rows = initializeHardcodedRows([]);
                setMetadata(rows);
            } finally {
                setLoading(false);
            }
        };

        // Add a small delay to debounce rapid selections
        const timeoutId = setTimeout(loadMetadata, 150);

        // Cleanup function to clear timeout
        return () => {
            clearTimeout(timeoutId);
        };
    }, [assetLinkId, mode, initialMetadata, initializeHardcodedRows, onMetadataChange]);

    const handleEdit = (index: number) => {
        setMetadata((prev) =>
            prev.map((row, i) =>
                i === index
                    ? {
                          ...row,
                          isEditing: true,
                          editValue: row.metadataValue,
                          editType: row.metadataValueType,
                      }
                    : row
            )
        );
    };

    const handleCancel = (index: number) => {
        const row = metadata[index];

        // Fix Issue 3: For static fields (hardcoded keys), never remove them - just exit edit mode
        if (HARDCODED_KEYS.includes(row.metadataKey)) {
            // For static fields, always just exit edit mode
            setMetadata((prev) =>
                prev.map((r, i) =>
                    i === index
                        ? {
                              ...r,
                              isEditing: false,
                              editValue: r.metadataValue,
                              editType: r.metadataValueType,
                              hasChanges: false,
                          }
                        : r
                )
            );
        } else if (row.isNew && !row.metadataValue) {
            // For non-static fields, if canceling a new item that hasn't been saved, remove it completely
            setMetadata((prev) => prev.filter((_, i) => i !== index));
        } else {
            // Otherwise, just exit edit mode
            setMetadata((prev) =>
                prev.map((r, i) =>
                    i === index
                        ? {
                              ...r,
                              isEditing: false,
                              editValue: r.metadataValue,
                              editType: r.metadataValueType,
                              hasChanges: false,
                          }
                        : r
                )
            );
        }
    };

    const handleValueChange = (index: number, value: string) => {
        setMetadata((prev) =>
            prev.map((row, i) =>
                i === index
                    ? { ...row, editValue: value, hasChanges: value !== row.metadataValue }
                    : row
            )
        );
    };

    const handleTypeChange = (index: number, type: "XYZ" | "String") => {
        setMetadata((prev) =>
            prev.map((row, i) =>
                i === index
                    ? {
                          ...row,
                          editType: type,
                          editValue: "", // Fix Issue 2: Use empty string for both XYZ and String types
                          hasChanges: true,
                      }
                    : row
            )
        );
    };

    const handleSave = async (index: number) => {
        const row = metadata[index];
        if (!row.hasChanges && !row.isNew) return;

        // Validate XYZ values
        if (row.editType === "XYZ" && row.editValue) {
            try {
                JSON.parse(row.editValue);
            } catch {
                setError("Invalid XYZ format. Please enter valid coordinates.");
                return;
            }
        }

        if (mode === "upload") {
            // In upload mode, just update local state - NO API CALLS
            console.log("Upload mode: Saving metadata locally only");
            console.log("Current row before save:", row);
            console.log("Edit value to save:", row.editValue);

            // Update the metadata state with proper state management
            setMetadata((prev) => {
                console.log("Previous metadata state:", prev);

                const updatedMetadata = prev.map((r, i) => {
                    if (i === index) {
                        const updatedRow = {
                            ...r,
                            metadataValue: r.editValue,
                            metadataValueType: r.editType,
                            isEditing: false,
                            hasChanges: false,
                            isNew: false,
                        };
                        console.log("Updated row:", updatedRow);
                        return updatedRow;
                    }
                    return r;
                });

                console.log("New metadata state:", updatedMetadata);

                // Notify parent of changes using the updated metadata
                if (onMetadataChange) {
                    const metadataForParent = updatedMetadata
                        .filter((m) => m.metadataValue) // Only include non-empty values
                        .map((r) => ({
                            assetLinkId: r.assetLinkId,
                            metadataKey: r.metadataKey,
                            metadataValue: r.metadataValue,
                            metadataValueType: r.metadataValueType,
                        }));
                    console.log("Notifying parent with metadata:", metadataForParent);
                    onMetadataChange(metadataForParent);
                }

                return updatedMetadata;
            });

            return; // IMPORTANT: Return here to prevent any API calls
        }

        // View mode - make API calls
        console.log("View mode: Saving metadata via API");
        setLoading(true);
        setError(null);

        try {
            let success = false;
            let message = "";

            if (row.isNew || !row.metadataValue) {
                // Create new metadata
                const [isSuccess, responseMessage] = await createAssetLinkMetadata({
                    assetLinkId,
                    metadataKey: row.metadataKey,
                    metadataValue: row.editValue,
                    metadataValueType: row.editType,
                });
                success = isSuccess;
                message = responseMessage;
            } else {
                // Update existing metadata
                const [isSuccess, responseMessage] = await updateAssetLinkMetadata({
                    assetLinkId,
                    metadataKey: row.metadataKey,
                    metadataValue: row.editValue,
                    metadataValueType: row.editType,
                });
                success = isSuccess;
                message = responseMessage;
            }

            if (success) {
                // Update local state
                setMetadata((prev) =>
                    prev.map((r, i) =>
                        i === index
                            ? {
                                  ...r,
                                  metadataValue: r.editValue,
                                  metadataValueType: r.editType,
                                  isEditing: false,
                                  hasChanges: false,
                                  isNew: false,
                              }
                            : r
                    )
                );

                // Notify parent of changes
                if (onMetadataChange) {
                    const updatedMetadata = metadata
                        .map((r, i) =>
                            i === index
                                ? {
                                      assetLinkId: r.assetLinkId,
                                      metadataKey: r.metadataKey,
                                      metadataValue: r.editValue,
                                      metadataValueType: r.editType,
                                  }
                                : {
                                      assetLinkId: r.assetLinkId,
                                      metadataKey: r.metadataKey,
                                      metadataValue: r.metadataValue,
                                      metadataValueType: r.metadataValueType,
                                  }
                        )
                        .filter((m) => m.metadataValue); // Only include non-empty values
                    onMetadataChange(updatedMetadata);
                }
            } else {
                setError(message || "Failed to save metadata");
            }
        } catch (err) {
            console.error("Error saving metadata:", err);
            setError("Failed to save metadata");
        } finally {
            setLoading(false);
        }
    };

    const handleDelete = async (index: number) => {
        const row = metadata[index];

        if (mode === "upload") {
            // In upload mode, just update local state
            if (HARDCODED_KEYS.includes(row.metadataKey)) {
                // For hardcoded rows, just clear the value
                setMetadata((prev) =>
                    prev.map((r, i) =>
                        i === index
                            ? {
                                  ...r,
                                  metadataValue: "",
                                  editValue: "",
                                  isNew: true,
                                  hasChanges: false,
                              }
                            : r
                    )
                );
            } else {
                // For custom rows, remove completely
                setMetadata((prev) => prev.filter((_, i) => i !== index));
            }

            // Notify parent of changes
            if (onMetadataChange) {
                const updatedMetadata = metadata
                    .filter((_, i) => i !== index || HARDCODED_KEYS.includes(row.metadataKey))
                    .map((r) => ({
                        assetLinkId: r.assetLinkId,
                        metadataKey: r.metadataKey,
                        metadataValue:
                            HARDCODED_KEYS.includes(r.metadataKey) && r === row
                                ? ""
                                : r.metadataValue,
                        metadataValueType: r.metadataValueType,
                    }))
                    .filter((m) => m.metadataValue); // Only include non-empty values
                onMetadataChange(updatedMetadata);
            }
            return;
        }

        // For hardcoded rows, just clear the value
        if (HARDCODED_KEYS.includes(row.metadataKey)) {
            if (!row.metadataValue) return; // Already empty

            setLoading(true);
            setError(null);

            try {
                const [success, message] = await deleteAssetLinkMetadata({
                    assetLinkId,
                    metadataKey: row.metadataKey,
                });

                if (success) {
                    setMetadata((prev) =>
                        prev.map((r, i) =>
                            i === index
                                ? {
                                      ...r,
                                      metadataValue: "",
                                      editValue: "",
                                      isNew: true,
                                      hasChanges: false,
                                  }
                                : r
                        )
                    );
                } else {
                    setError(message || "Failed to delete metadata");
                }
            } catch (err) {
                console.error("Error deleting metadata:", err);
                setError("Failed to delete metadata");
            } finally {
                setLoading(false);
            }
        } else {
            // For custom rows, remove completely
            setLoading(true);
            setError(null);

            try {
                const [success, message] = await deleteAssetLinkMetadata({
                    assetLinkId,
                    metadataKey: row.metadataKey,
                });

                if (success) {
                    setMetadata((prev) => prev.filter((_, i) => i !== index));
                } else {
                    setError(message || "Failed to delete metadata");
                }
            } catch (err) {
                console.error("Error deleting metadata:", err);
                setError("Failed to delete metadata");
            } finally {
                setLoading(false);
            }
        }
    };

    const handleAddNew = () => {
        setMetadata((prev) => [
            ...prev,
            {
                assetLinkId,
                metadataKey: "",
                metadataValue: "",
                metadataValueType: "String",
                isEditing: true,
                editValue: "",
                editType: "String",
                hasChanges: true,
                isNew: true,
            },
        ]);
    };

    const handleKeyChange = (index: number, key: string) => {
        setMetadata((prev) =>
            prev.map((row, i) =>
                i === index ? { ...row, metadataKey: key, hasChanges: true } : row
            )
        );
    };

    const renderValueInput = (row: MetadataRow, index: number) => {
        if (row.editType === "XYZ") {
            return (
                <XYZInput
                    value={row.editValue}
                    onChange={(value) => handleValueChange(index, value)}
                    disabled={loading}
                    ariaLabel={`${row.metadataKey} XYZ coordinates`}
                />
            );
        } else {
            return (
                <Input
                    value={row.editValue}
                    onChange={({ detail }) => handleValueChange(index, detail.value)}
                    placeholder="Enter value"
                    disabled={loading}
                    ariaLabel={`${row.metadataKey} value`}
                />
            );
        }
    };

    const getItemIndex = (item: MetadataRow) => {
        return metadata.findIndex(
            (m) =>
                m.assetLinkId === item.assetLinkId &&
                m.metadataKey === item.metadataKey &&
                m.isEditing === item.isEditing
        );
    };

    const columnDefinitions = [
        {
            id: "key",
            header: "Metadata Key",
            cell: (item: MetadataRow) => {
                const index = getItemIndex(item);
                if (HARDCODED_KEYS.includes(item.metadataKey)) {
                    return <strong>{item.metadataKey}</strong>;
                } else if (item.isEditing && item.isNew) {
                    return (
                        <Input
                            value={item.metadataKey}
                            onChange={({ detail }) => handleKeyChange(index, detail.value)}
                            placeholder="Enter key name"
                            disabled={loading}
                            ariaLabel="Metadata key"
                        />
                    );
                } else {
                    return item.metadataKey;
                }
            },
        },
        {
            id: "type",
            header: "Metadata Type",
            cell: (item: MetadataRow) => {
                const index = getItemIndex(item);
                if (item.isEditing && !HARDCODED_KEYS.includes(item.metadataKey)) {
                    return (
                        <Select
                            selectedOption={{ label: item.editType, value: item.editType }}
                            onChange={({ detail }) =>
                                handleTypeChange(
                                    index,
                                    detail.selectedOption.value as "XYZ" | "String"
                                )
                            }
                            options={[
                                { label: "String", value: "String" },
                                { label: "XYZ", value: "XYZ" },
                            ]}
                            disabled={loading}
                            ariaLabel="Metadata type"
                            expandToViewport={true}
                        />
                    );
                } else {
                    return item.metadataValueType;
                }
            },
        },
        {
            id: "value",
            header: "Metadata Value",
            cell: (item: MetadataRow) => {
                const index = getItemIndex(item);
                if (item.isEditing) {
                    return renderValueInput(item, index);
                } else {
                    if (item.metadataValueType === "XYZ" && item.metadataValue) {
                        try {
                            const parsed = JSON.parse(item.metadataValue);
                            return `X: ${parsed.x}, Y: ${parsed.y}, Z: ${parsed.z}`;
                        } catch {
                            return item.metadataValue;
                        }
                    }
                    return item.metadataValue || <em>Empty</em>;
                }
            },
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: MetadataRow) => {
                const index = getItemIndex(item);
                if (item.isEditing) {
                    return (
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="primary"
                                onClick={() => handleSave(index)}
                                disabled={
                                    loading || !item.hasChanges || (item.isNew && !item.metadataKey)
                                }
                                ariaLabel="Save metadata"
                            >
                                <Icon name="check" />
                            </Button>
                            <Button
                                variant="normal"
                                onClick={() => handleCancel(index)}
                                disabled={loading}
                                ariaLabel="Cancel edit"
                            >
                                <Icon name="close" />
                            </Button>
                        </SpaceBetween>
                    );
                } else {
                    return (
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="normal"
                                onClick={() => handleEdit(index)}
                                disabled={loading}
                                ariaLabel="Edit metadata"
                            >
                                Edit
                            </Button>
                            <Button
                                variant="normal"
                                onClick={() => {
                                    const actionType = HARDCODED_KEYS.includes(item.metadataKey)
                                        ? "clear"
                                        : "delete";
                                    setConfirmationDialog({
                                        visible: true,
                                        type: actionType,
                                        index,
                                        metadataKey: item.metadataKey,
                                    });
                                }}
                                disabled={
                                    loading ||
                                    (HARDCODED_KEYS.includes(item.metadataKey) &&
                                        !item.metadataValue)
                                }
                                ariaLabel={
                                    HARDCODED_KEYS.includes(item.metadataKey)
                                        ? "Clear metadata"
                                        : "Delete metadata"
                                }
                            >
                                {HARDCODED_KEYS.includes(item.metadataKey) ? "Clear" : "Delete"}
                            </Button>
                        </SpaceBetween>
                    );
                }
            },
        },
    ];

    return (
        <Container
            header={
                <Header
                    variant="h3"
                    actions={
                        <Button
                            variant="primary"
                            onClick={handleAddNew}
                            disabled={loading}
                            ariaLabel="Add new metadata"
                        >
                            Add Metadata
                        </Button>
                    }
                >
                    Link Metadata
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                <div className="asset-link-metadata-table">
                    <Table
                        columnDefinitions={columnDefinitions}
                        items={metadata}
                        loading={loading}
                        loadingText="Loading metadata..."
                        empty={
                            <div className="asset-link-metadata-empty-state">
                                <Box>
                                    <b>No metadata</b>
                                </Box>
                                <Box variant="p" color="inherit">
                                    Add metadata to provide additional information about this asset
                                    link.
                                </Box>
                                <Box>
                                    <Button
                                        variant="primary"
                                        onClick={handleAddNew}
                                        disabled={loading}
                                        ariaLabel="Add new metadata"
                                    >
                                        Add Metadata
                                    </Button>
                                </Box>
                            </div>
                        }
                        header={
                            <Box
                                textAlign="center"
                                variant="small"
                                color="text-body-secondary"
                                padding="s"
                            >
                                <strong>Translation, Rotation, and Scale</strong> are always
                                available for XYZ coordinate values. You can add custom metadata
                                fields below.
                            </Box>
                        }
                    />
                </div>
            </SpaceBetween>

            {/* Confirmation Modal */}
            <Modal
                visible={confirmationDialog.visible}
                onDismiss={() =>
                    setConfirmationDialog({
                        visible: false,
                        type: "clear",
                        index: -1,
                        metadataKey: "",
                    })
                }
                header={confirmationDialog.type === "clear" ? "Clear Metadata" : "Delete Metadata"}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() =>
                                    setConfirmationDialog({
                                        visible: false,
                                        type: "clear",
                                        index: -1,
                                        metadataKey: "",
                                    })
                                }
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    handleDelete(confirmationDialog.index);
                                    setConfirmationDialog({
                                        visible: false,
                                        type: "clear",
                                        index: -1,
                                        metadataKey: "",
                                    });
                                }}
                            >
                                {confirmationDialog.type === "clear" ? "Clear" : "Delete"}
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="m">
                    <Box variant="span">
                        {confirmationDialog.type === "clear"
                            ? `Are you sure you want to clear the metadata for "${confirmationDialog.metadataKey}"? This will remove the current value but keep the field available for future use.`
                            : `Are you sure you want to delete the metadata field "${confirmationDialog.metadataKey}"? This action cannot be undone.`}
                    </Box>
                </SpaceBetween>
            </Modal>
        </Container>
    );
};

export default AssetLinkMetadata;
