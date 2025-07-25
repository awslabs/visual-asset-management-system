/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    Box,
    Button,
    Form,
    FormField,
    Input,
    Link,
    Modal,
    SpaceBetween,
    Alert,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { fetchAllAssets } from "../../../../../services/APIService";
import CustomTable from "../../../../table/CustomTable";
import { useStatusMessage } from "../../../../common/StatusMessage";
import { CreateAssetLinkModalProps } from "../../types/AssetLinksTypes";

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
    const [searchedEntity, setSearchedEntity] = useState("");
    const [showTable, setShowTable] = useState(false);
    const [searchResult, setSearchResult] = useState<any[]>([]);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);
    const [nameError, setNameError] = useState("");
    const [formError, setFormError] = useState("");
    const [addDisabled, setAddDisabled] = useState(false);

    // Format tags with tag types (same logic as AssetLinksDetailsPanel)
    const formatAssetTags = (tags: any[]) => {
        if (!Array.isArray(tags) || tags.length === 0) {
            return "No tags assigned";
        }

        try {
            const tagTypes = JSON.parse(localStorage.getItem("tagTypes") || "[]");

            return tags
                .map((tag: any) => {
                    const tagType = tagTypes.find(
                        (type: any) => Array.isArray(type.tags) && type.tags.includes(tag)
                    );

                    if (tagType) {
                        // If tagType has required field add [R] to tag type name
                        const tagTypeName =
                            tagType.required === "True"
                                ? `${tagType.tagTypeName} [R]`
                                : tagType.tagTypeName;
                        return `${tag} (${tagTypeName})`;
                    }
                    return tag;
                })
                .join(", ");
        } catch (error) {
            console.error("Error formatting tags:", error);
            return tags.join(", ");
        }
    };

    // Handle entity search
    const handleEntitySearch = async () => {
        try {
            // Clear any previous errors
            setNameError("");
            setFormError("");

            if (!searchedEntity || searchedEntity.trim() === "") {
                setNameError("Please enter an asset name to search for");
                return;
            }

            // Show loading state
            setAddDisabled(true);

            let result;
            if (!noOpenSearch) {
                // Use OpenSearch API
                const body = {
                    tokens: [],
                    operation: "AND",
                    from: 0,
                    size: 100,
                    query: searchedEntity,
                    filters: [
                        {
                            query_string: {
                                query: '(_rectype:("asset"))',
                            },
                        },
                    ],
                };
                result = await API.post("api", "search", {
                    "Content-type": "application/json",
                    body: body,
                });
                result = result?.hits?.hits;
            } else {
                // Use assets API
                result = await fetchAllAssets();
                result = result?.filter((item: any) => item.databaseId.indexOf("#deleted") === -1);
                result = result?.filter((item: any) =>
                    item.assetName.toLowerCase().includes(searchedEntity.toLowerCase())
                );
            }

            if (result && Object.keys(result).length > 0) {
                setSearchResult(result);
                setShowTable(true);
            } else {
                setSearchResult([]);
                setShowTable(true);
                setNameError(`No assets found matching "${searchedEntity}"`);
            }
        } catch (error: any) {
            console.error("Error fetching data:", error);
            setFormError(`Failed to search for assets: ${error.message || "Unknown error"}`);
        } finally {
            setAddDisabled(false);
        }
    };

    // Add link
    const addLink = async () => {
        if (!selectedItems[0]?.assetId) {
            setFormError("Please select an asset to link.");
            return;
        }

        if (isUploadMode) {
            // In upload mode, just add to local state (handled by parent component)
            try {
                setAddDisabled(true);

                // Create asset node from selected item
                const selectedAsset = selectedItems[0];
                const assetNode = {
                    assetId: selectedAsset.assetId,
                    assetName: selectedAsset.assetName,
                    databaseId: selectedAsset.databaseName || selectedAsset.databaseId,
                    assetLinkId: `temp-${selectedAsset.assetId}-${relationshipType}`, // Temporary ID for upload mode
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

        // View mode - make actual API call
        const assetLinkBody = {
            fromAssetId: "",
            fromAssetDatabaseId: "",
            toAssetId: "",
            toAssetDatabaseId: "",
            relationshipType: "",
        };

        // Special handling for sub-child mode
        if (isSubChildMode && parentAssetData) {
            // When creating a sub-child link, the parent is the selected node in the tree view
            // and the child is the selected asset in the modal
            assetLinkBody.fromAssetId = parentAssetData.assetId;
            assetLinkBody.fromAssetDatabaseId = parentAssetData.databaseId;
            assetLinkBody.toAssetId = selectedItems[0].assetId;
            assetLinkBody.toAssetDatabaseId =
                selectedItems[0].databaseName || selectedItems[0].databaseId;
            assetLinkBody.relationshipType = "parentChild";

            // Debug logging
            console.log("Creating sub-child link with:", {
                isSubChildMode,
                parentAssetData,
                fromAssetId: assetLinkBody.fromAssetId,
                fromAssetDatabaseId: assetLinkBody.fromAssetDatabaseId,
                toAssetId: assetLinkBody.toAssetId,
                toAssetDatabaseId: assetLinkBody.toAssetDatabaseId,
                relationshipType: assetLinkBody.relationshipType,
            });
        } else {
            // Normal relationship handling
            switch (relationshipType) {
                case "parent":
                    // When adding a parent: selected asset is the parent (from), current asset is the child (to)
                    assetLinkBody.fromAssetId = selectedItems[0].assetId;
                    assetLinkBody.fromAssetDatabaseId =
                        selectedItems[0].databaseName || selectedItems[0].databaseId;
                    assetLinkBody.toAssetId = currentAssetId;
                    assetLinkBody.toAssetDatabaseId = currentDatabaseId;
                    assetLinkBody.relationshipType = "parentChild";
                    break;
                case "child":
                    // When adding a child: current asset is the parent (from), selected asset is the child (to)
                    assetLinkBody.fromAssetId = currentAssetId;
                    assetLinkBody.fromAssetDatabaseId = currentDatabaseId;
                    assetLinkBody.toAssetId = selectedItems[0].assetId;
                    assetLinkBody.toAssetDatabaseId =
                        selectedItems[0].databaseName || selectedItems[0].databaseId;
                    assetLinkBody.relationshipType = "parentChild";
                    break;
                case "related":
                    assetLinkBody.fromAssetId = currentAssetId;
                    assetLinkBody.fromAssetDatabaseId = currentDatabaseId;
                    assetLinkBody.toAssetId = selectedItems[0].assetId;
                    assetLinkBody.toAssetDatabaseId =
                        selectedItems[0].databaseName || selectedItems[0].databaseId;
                    assetLinkBody.relationshipType = "related";
                    break;
            }
        }

        try {
            setAddDisabled(true);
            await API.post("api", "asset-links", {
                body: assetLinkBody,
            });

            showMessage({
                type: "success",
                message: `Successfully added ${relationshipType} link`,
                dismissible: true,
                autoDismiss: true,
            });

            handleClose();
            onSuccess();
        } catch (err: any) {
            console.error("Error creating asset link:", err);
            if (err.response?.status === 400) {
                setNameError(err.response.data.message || "Invalid request");
            } else {
                let msg = `Unable to add ${relationshipType} link. Error: Request failed with status code ${
                    err.response?.status || "unknown"
                }`;
                setFormError(msg);
            }
        } finally {
            setAddDisabled(false);
        }
    };

    const handleClose = () => {
        setSearchedEntity("");
        setShowTable(false);
        setSearchResult([]);
        setSelectedItems([]);
        setFormError("");
        setNameError("");
        setAddDisabled(false);
        onDismiss();
    };

    // Table columns for asset search results
    const assetCols = [
        {
            id: "assetId",
            header: "Asset Name",
            cell: (item: any) => (
                <Link
                    href={`#/databases/${item.databaseName || item.databaseId}/assets/${
                        item.assetId
                    }`}
                >
                    {item.assetName}
                </Link>
            ),
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "databaseId",
            header: "Database Name",
            cell: (item: any) => item.databaseName || item.databaseId,
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "description",
            header: "Description",
            cell: (item: any) => item.description,
            sortingField: "alt",
        },
        {
            id: "tags",
            header: "Tags",
            cell: (item: any) => formatAssetTags(item.tags || []),
            sortingField: "tags",
        },
    ];

    // Format search results
    const assetItems = Array.isArray(searchResult)
        ? !noOpenSearch
            ? searchResult.map((result: any) => ({
                  // Search API results
                  assetName: result._source.str_assetname || "",
                  databaseName: result._source.str_databaseid || "",
                  description: result._source.str_description || "",
                  assetId: result._source.str_assetid || "",
                  tags: result._source.tags || [],
              }))
            : // FetchAllAssets API Results (No OpenSearch)
              searchResult.map((result: any) => ({
                  assetName: result.assetName || "",
                  databaseName: result.databaseId || "",
                  description: result.description || "",
                  assetId: result.assetId || "",
                  tags: result.tags || [],
              }))
        : []; // No result

    const relationshipDisplayName =
        relationshipType.charAt(0).toUpperCase() + relationshipType.slice(1);

    // Custom header component that includes the alert above the title
    const modalHeader = (
        <div>
            {formError && (
                <div style={{ marginBottom: "10px" }}>
                    <Alert type="error" dismissible onDismiss={() => setFormError("")}>
                        <div style={{ fontWeight: "bold" }}>Error</div>
                        <div>{formError}</div>
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
                            disabled={addDisabled || !selectedItems[0]?.assetId}
                            onClick={addLink}
                        >
                            Create Link
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <div style={{ width: "100%", maxWidth: "none" }}>
                {/* Name error alert at the top of the modal content */}
                {nameError && (
                    <div style={{ marginBottom: "20px" }}>
                        <Alert type="error" dismissible onDismiss={() => setNameError("")}>
                            {nameError}
                        </Alert>
                    </div>
                )}

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
                    <div style={{ width: "100%", maxWidth: "none" }}>
                        <div style={{ marginBottom: "8px" }}>
                            <label
                                style={{
                                    display: "block",
                                    fontWeight: "600",
                                    fontSize: "14px",
                                    marginBottom: "4px",
                                    color: "var(--color-text-label)",
                                }}
                            >
                                Asset Name
                            </label>
                            <div
                                style={{
                                    fontSize: "12px",
                                    color: "var(--color-text-body-secondary)",
                                    marginBottom: "8px",
                                }}
                            >
                                Input asset name. Press Enter to search.
                            </div>
                        </div>
                        <div style={{ width: "100%", maxWidth: "none" }}>
                            <Input
                                placeholder="Search for assets"
                                type="search"
                                value={searchedEntity || ""}
                                onChange={({ detail }) => {
                                    setSearchedEntity(detail.value);
                                    setShowTable(false);
                                    setSelectedItems([]);
                                    setNameError("");
                                }}
                                onKeyDown={({ detail }) => {
                                    if (detail.key === "Enter") {
                                        handleEntitySearch();
                                    }
                                }}
                            />
                        </div>
                    </div>
                    {showTable && (
                        <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                            <div style={{ marginBottom: "8px" }}>
                                <label
                                    style={{
                                        display: "block",
                                        fontWeight: "600",
                                        fontSize: "14px",
                                        color: "var(--color-text-label)",
                                    }}
                                >
                                    Select Asset
                                </label>
                            </div>
                            <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                                <CustomTable
                                    columns={assetCols}
                                    items={assetItems}
                                    selectedItems={selectedItems}
                                    setSelectedItems={setSelectedItems}
                                    trackBy={"assetId"}
                                    enablePagination={true}
                                    pageSize={15}
                                />
                            </div>
                        </div>
                    )}
                </SpaceBetween>
            </div>
        </Modal>
    );
}
