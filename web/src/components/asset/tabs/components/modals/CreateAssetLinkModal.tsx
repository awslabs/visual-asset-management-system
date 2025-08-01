/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
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
import { fetchAllAssets, fetchtagTypes } from "../../../../../services/APIService";
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
    const [selectedAssets, setSelectedAssets] = useState<any[]>([]);
    const [nameError, setNameError] = useState("");
    const [formError, setFormError] = useState("");
    const [addDisabled, setAddDisabled] = useState(false);
    const [tagTypes, setTagTypes] = useState<any[]>([]);

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

    // Format tags with tag types (same logic as SearchPageListView)
    const formatAssetTags = (tags: any[]) => {
        console.log("formatAssetTags called with:", tags);

        if (!Array.isArray(tags) || tags.length === 0) {
            console.log("No tags to format or tags is not an array");
            return "No tags assigned";
        }

        try {
            console.log("Tag types available:", tagTypes);

            const tagsWithType = tags.map((tag) => {
                console.log("Processing tag:", tag);

                if (tagTypes && tagTypes.length > 0) {
                    for (const tagType of tagTypes) {
                        var tagTypeName = tagType.tagTypeName;

                        // If tagType has required field add [R] to tag type name
                        if (tagType && tagType.required === "True") {
                            tagTypeName += " [R]";
                        }

                        if (
                            tagType.tags &&
                            Array.isArray(tagType.tags) &&
                            tagType.tags.includes(tag)
                        ) {
                            console.log(`Found tag type for ${tag}: ${tagTypeName}`);
                            return `${tag} [${tagTypeName}]`;
                        }
                    }
                }
                return tag;
            });

            const result = tagsWithType.join(", ");
            console.log("Formatted tags result:", result);
            return result;
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
                // Debug logging
                console.log("Search results:", result);
                if (result.length > 0) {
                    console.log("First result:", result[0]);
                    if (!noOpenSearch) {
                        console.log("Tags in first result:", result[0]._source?.tags);
                    } else {
                        console.log("Tags in first result:", result[0].tags);
                    }
                }

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

    // Add selected items to the selected assets list
    const addSelectedToAssets = () => {
        if (selectedItems.length === 0) {
            setNameError("Please select at least one asset to add.");
            return;
        }

        // Add selected items to the selected assets list, avoiding duplicates
        const newSelectedAssets = [...selectedAssets];
        selectedItems.forEach((item) => {
            const isDuplicate = newSelectedAssets.some((asset) => asset.assetId === item.assetId);
            if (!isDuplicate) {
                newSelectedAssets.push(item);
            }
        });

        setSelectedAssets(newSelectedAssets);
        setSelectedItems([]);
    };

    // Remove an asset from the selected assets list
    const removeSelectedAsset = (assetId: string) => {
        setSelectedAssets(selectedAssets.filter((asset) => asset.assetId !== assetId));
    };

    // Add link
    const addLink = async () => {
        // Use selectedAssets instead of selectedItems for the final selection
        const assetsToLink = selectedAssets.length > 0 ? selectedAssets : selectedItems;

        if (assetsToLink.length === 0) {
            setFormError("Please select at least one asset to link.");
            return;
        }

        if (isUploadMode) {
            // In upload mode, just add to local state (handled by parent component)
            try {
                setAddDisabled(true);

                // Create asset node from the first selected item (upload mode doesn't support multi-select yet)
                const selectedAsset = assetsToLink[0];
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

        // View mode - make API calls for each selected asset
        setAddDisabled(true);
        let successCount = 0;
        let errorCount = 0;
        // Track errors with asset names and error messages
        const errors: { assetName: string; message: string }[] = [];

        try {
            // Process each selected asset
            for (const selectedAsset of assetsToLink) {
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
        setSearchedEntity("");
        setShowTable(false);
        setSearchResult([]);
        setSelectedItems([]);
        setSelectedAssets([]);
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
            ? searchResult.map((result: any) => {
                  // Debug logging for OpenSearch results
                  console.log("Processing OpenSearch result:", result);
                  console.log(
                      "_source fields:",
                      result._source ? Object.keys(result._source) : "No _source"
                  );

                  // Check for tags in different possible locations
                  // OpenSearch typically stores tags in list_tags field
                  let tags = [];

                  if (result._source?.list_tags) {
                      // If list_tags is a string (comma-separated), split it
                      if (typeof result._source.list_tags === "string") {
                          tags = result._source.list_tags
                              .split(",")
                              .map((tag: string) => tag.trim());
                      }
                      // If list_tags is already an array
                      else if (Array.isArray(result._source.list_tags)) {
                          tags = result._source.list_tags;
                      }
                  }
                  // Fallback to other possible tag fields
                  else if (result._source?.tags) {
                      tags = Array.isArray(result._source.tags)
                          ? result._source.tags
                          : [result._source.tags];
                  }

                  console.log("Found tags:", tags);

                  // Search API results
                  return {
                      assetName: result._source?.str_assetname || "",
                      databaseName: result._source?.str_databaseid || "",
                      description: result._source?.str_description || "",
                      assetId: result._source?.str_assetid || "",
                      tags: tags,
                  };
              })
            : // FetchAllAssets API Results (No OpenSearch)
              searchResult.map((result: any) => {
                  // Debug logging for direct API results
                  console.log("Processing direct API result:", result);
                  console.log("Available fields:", Object.keys(result));
                  console.log("Tags field:", result.tags);

                  return {
                      assetName: result.assetName || "",
                      databaseName: result.databaseId || "",
                      description: result.description || "",
                      assetId: result.assetId || "",
                      tags: result.tags || [],
                  };
              })
        : []; // No result

    // Debug the formatted items
    console.log("Formatted asset items:", assetItems);
    if (assetItems.length > 0) {
        console.log("First formatted item:", assetItems[0]);
        console.log("Tags in first formatted item:", assetItems[0].tags);
    }

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
                            disabled={
                                addDisabled ||
                                (selectedItems.length === 0 && selectedAssets.length === 0)
                            }
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
                    {/* Selected Assets Table */}
                    {selectedAssets.length > 0 && (
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
                                    Selected Assets ({selectedAssets.length})
                                </label>
                            </div>
                            <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                                <CustomTable
                                    columns={[
                                        ...assetCols,
                                        {
                                            id: "actions",
                                            header: "Actions",
                                            cell: (item: any) => (
                                                <Button
                                                    variant="link"
                                                    onClick={() =>
                                                        removeSelectedAsset(item.assetId)
                                                    }
                                                >
                                                    Remove
                                                </Button>
                                            ),
                                        },
                                    ]}
                                    items={selectedAssets}
                                    selectedItems={[]}
                                    setSelectedItems={() => {}}
                                    trackBy={"assetId"}
                                    enablePagination={true}
                                    pageSize={5}
                                />
                            </div>
                        </div>
                    )}

                    {/* Search Input */}
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
                                Search Assets
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

                    {/* Search Results */}
                    {showTable && (
                        <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                            <div
                                style={{
                                    display: "flex",
                                    justifyContent: "space-between",
                                    alignItems: "center",
                                    marginBottom: "8px",
                                }}
                            >
                                <label
                                    style={{
                                        display: "block",
                                        fontWeight: "600",
                                        fontSize: "14px",
                                        color: "var(--color-text-label)",
                                    }}
                                >
                                    Search Results
                                </label>
                                <Button
                                    onClick={addSelectedToAssets}
                                    disabled={selectedItems.length === 0}
                                >
                                    Add Selected ({selectedItems.length})
                                </Button>
                            </div>
                            <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                                <CustomTable
                                    columns={assetCols}
                                    items={assetItems}
                                    selectedItems={selectedItems}
                                    setSelectedItems={setSelectedItems}
                                    trackBy={"assetId"}
                                    enablePagination={true}
                                    pageSize={10}
                                    selectionType="multi"
                                />
                            </div>
                        </div>
                    )}
                </SpaceBetween>
            </div>
        </Modal>
    );
}
