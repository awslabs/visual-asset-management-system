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
import { WXYZInput } from "./WXYZInput";
import { Matrix4x4Input } from "./Matrix4x4Input";
import { LLAInput } from "./LLAInput";
import { JSONTextInput } from "./JSONTextInput";
import { DateInput } from "./DateInput";
import { BooleanInput } from "./BooleanInput";
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
    editType:
        | "xyz"
        | "wxyz"
        | "string"
        | "number"
        | "matrix4x4"
        | "geopoint"
        | "geojson"
        | "lla"
        | "json"
        | "date"
        | "boolean";
    hasChanges: boolean;
    isNew: boolean;
}

const HARDCODED_FIELDS = [
    { key: "Translation", defaultType: "xyz" as const },
    { key: "Rotation", defaultType: "wxyz" as const },
    { key: "Scale", defaultType: "xyz" as const },
    { key: "Matrix", defaultType: "matrix4x4" as const },
];

const HARDCODED_KEYS = HARDCODED_FIELDS.map((field) => field.key);

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
            HARDCODED_FIELDS.forEach((field) => {
                const existing = existingMetadata.find((m) => m.metadataKey === field.key);
                console.log(`Processing hardcoded key ${field.key}:`, existing);

                const hasValue =
                    existing && existing.metadataValue && existing.metadataValue.trim() !== "";

                const defaultType = existing?.metadataValueType || field.defaultType;

                rows.push({
                    assetLinkId,
                    metadataKey: field.key,
                    metadataValue: existing?.metadataValue || "",
                    metadataValueType: defaultType,
                    isEditing: false,
                    editValue: existing?.metadataValue || "",
                    editType: defaultType,
                    hasChanges: false,
                    isNew: !hasValue, // Only consider it new if there's no value or empty value
                });

                console.log(`Created row for ${field.key}:`, {
                    metadataValue: existing?.metadataValue || "",
                    defaultType,
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

    const handleTypeChange = (
        index: number,
        type:
            | "xyz"
            | "wxyz"
            | "string"
            | "number"
            | "matrix4x4"
            | "geopoint"
            | "geojson"
            | "lla"
            | "json"
            | "date"
            | "boolean"
    ) => {
        setMetadata((prev) =>
            prev.map((row, i) =>
                i === index
                    ? {
                          ...row,
                          editType: type,
                          editValue: "", // Clear value when changing type
                          hasChanges: true,
                      }
                    : row
            )
        );
    };

    const handleSave = async (index: number) => {
        const row = metadata[index];
        if (!row.hasChanges && !row.isNew) return;

        // Validate xyz values
        if (row.editType === "xyz" && row.editValue) {
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
                metadataValueType: "string",
                isEditing: true,
                editValue: "",
                editType: "string",
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
        switch (row.editType) {
            case "xyz":
                return (
                    <XYZInput
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} XYZ coordinates`}
                    />
                );
            case "wxyz":
                return (
                    <WXYZInput
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} WXYZ quaternion`}
                    />
                );
            case "matrix4x4":
                return (
                    <Matrix4x4Input
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} 4x4 matrix`}
                    />
                );
            case "lla":
                return (
                    <LLAInput
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} LLA coordinates`}
                    />
                );
            case "geopoint":
                return (
                    <JSONTextInput
                        type="GEOPOINT"
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} GeoJSON Point`}
                    />
                );
            case "geojson":
                return (
                    <JSONTextInput
                        type="GEOJSON"
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} GeoJSON object`}
                    />
                );
            case "json":
                return (
                    <JSONTextInput
                        type="JSON"
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} JSON object`}
                    />
                );
            case "date":
                return (
                    <DateInput
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} date`}
                    />
                );
            case "boolean":
                return (
                    <BooleanInput
                        value={row.editValue}
                        onChange={(value) => handleValueChange(index, value)}
                        disabled={loading}
                        ariaLabel={`${row.metadataKey} boolean value`}
                    />
                );
            case "number":
                return (
                    <Input
                        value={row.editValue}
                        onChange={({ detail }) => handleValueChange(index, detail.value)}
                        placeholder="Enter number"
                        disabled={loading}
                        type="number"
                        step="any"
                        ariaLabel={`${row.metadataKey} number value`}
                    />
                );
            default:
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
                                    detail.selectedOption.value as
                                        | "xyz"
                                        | "wxyz"
                                        | "string"
                                        | "number"
                                        | "matrix4x4"
                                        | "geopoint"
                                        | "geojson"
                                        | "lla"
                                        | "json"
                                        | "date"
                                        | "boolean"
                                )
                            }
                            options={[
                                { label: "String", value: "string" },
                                { label: "Number", value: "number" },
                                { label: "Date", value: "date" },
                                { label: "Boolean", value: "boolean" },
                                { label: "XYZ", value: "xyz" },
                                { label: "WXYZ", value: "wxyz" },
                                { label: "Matrix 4x4", value: "matrix4x4" },
                                { label: "LLA", value: "lla" },
                                { label: "GeoPoint", value: "geopoint" },
                                { label: "GeoJSON", value: "geojson" },
                                { label: "JSON", value: "json" },
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
                    // Format display based on metadata type
                    if (!item.metadataValue) {
                        return <em>Empty</em>;
                    }

                    try {
                        switch (item.metadataValueType) {
                            case "xyz": {
                                const parsed = JSON.parse(item.metadataValue);
                                return `X: ${parsed.x}, Y: ${parsed.y}, Z: ${parsed.z}`;
                            }
                            case "wxyz": {
                                const parsed = JSON.parse(item.metadataValue);
                                return `W: ${parsed.w}, X: ${parsed.x}, Y: ${parsed.y}, Z: ${parsed.z}`;
                            }
                            case "matrix4x4": {
                                return "4x4 Matrix";
                            }
                            case "lla": {
                                const parsed = JSON.parse(item.metadataValue);
                                return `Lat: ${parsed.lat}, Long: ${parsed.long}, Alt: ${parsed.alt}`;
                            }
                            case "geopoint": {
                                const parsed = JSON.parse(item.metadataValue);
                                if (parsed.type === "Point" && parsed.coordinates) {
                                    return `Point: [${parsed.coordinates[0]}, ${parsed.coordinates[1]}]`;
                                }
                                return "GeoJSON Point";
                            }
                            case "geojson": {
                                const parsed = JSON.parse(item.metadataValue);
                                return `GeoJSON ${parsed.type || "Object"}`;
                            }
                            case "json": {
                                return "JSON Object";
                            }
                            case "date": {
                                const date = new Date(item.metadataValue);
                                return date.toLocaleDateString() + " " + date.toLocaleTimeString();
                            }
                            case "boolean": {
                                return item.metadataValue.toLowerCase() === "true"
                                    ? "True"
                                    : "False";
                            }
                            case "number": {
                                return item.metadataValue;
                            }
                            default:
                                return item.metadataValue;
                        }
                    } catch {
                        // If parsing fails, show raw value
                        return item.metadataValue;
                    }
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
                                <strong>
                                    Translation (XYZ), Rotation (WXYZ), Scale (XYZ), and Matrix
                                    (4x4)
                                </strong>{" "}
                                are always available as fixed fields. You can add custom metadata
                                fields with various data types below.
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
