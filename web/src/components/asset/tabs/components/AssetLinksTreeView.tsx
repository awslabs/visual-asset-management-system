/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useEffect, useState } from "react";
import {
    Box,
    Button,
    Icon,
    Spinner,
    Toggle,
    TextFilter,
    Container,
    Header,
    SpaceBetween,
    Alert,
} from "@cloudscape-design/components";
import {
    TreeNodeItem,
    AssetLinksContextType,
    NewAssetLinksContextType,
} from "../types/AssetLinksTypes";
import { fetchAsset, fetchtagTypes } from "../../../../services/APIService";
import "./AssetLinksTreeView.css";

// Create a context that will be overridden by the main component
export const AssetLinksContext = React.createContext<AssetLinksContextType | undefined>(undefined);

// Union type for both contexts
type UnifiedContextType = AssetLinksContextType | NewAssetLinksContextType;

interface TreeItemProps {
    item: TreeNodeItem;
}

function TreeItem({ item }: TreeItemProps) {
    const context = useContext(AssetLinksContext);
    if (!context) {
        throw new Error("TreeItem must be used within an AssetLinksContext.Provider");
    }
    const { state, dispatch } = context;
    const [assetDetails, setAssetDetails] = useState<any>(null);
    const [tagTypes, setTagTypes] = useState<any[]>([]);
    const [isLoadingDetails, setIsLoadingDetails] = useState(false);

    const isSelected = state.selectedNode?.id === item.id;
    const hasChildren = item.children.length > 0;
    const showTags = state.showTagsInTree && item.type === "asset" && item.assetData;

    // Fetch asset details and tag types when needed
    useEffect(() => {
        const loadAssetDetails = async () => {
            if (!showTags || !item.assetData || isLoadingDetails) return;

            const { assetId, databaseId } = item.assetData;

            // Check if we already have this asset in cache
            if (state.assetDetailsCache && state.assetDetailsCache[assetId]) {
                setAssetDetails(state.assetDetailsCache[assetId]);
                return;
            }

            setIsLoadingDetails(true);
            try {
                // Fetch asset details
                const details = await fetchAsset({ databaseId, assetId, showArchived: false });
                if (details) {
                    setAssetDetails(details);
                    // Cache the details
                    dispatch({
                        type: "SET_ASSET_DETAILS",
                        payload: { assetId, details },
                    });
                }

                // Fetch tag types if not already loaded
                if (tagTypes.length === 0) {
                    const types = await fetchtagTypes();
                    if (types && Array.isArray(types)) {
                        setTagTypes(types);
                    }
                }
            } catch (error) {
                console.error("Error fetching asset details:", error);
            } finally {
                setIsLoadingDetails(false);
            }
        };

        loadAssetDetails();
    }, [
        showTags,
        item.assetData,
        state.assetDetailsCache,
        dispatch,
        isLoadingDetails,
        tagTypes.length,
    ]);

    // Format tags with tag types
    const formatTags = (tags: any[]) => {
        if (!Array.isArray(tags) || tags.length === 0) {
            return "";
        }

        try {
            const tagsWithType = tags.map((tag) => {
                if (tagTypes && tagTypes.length > 0) {
                    for (const tagType of tagTypes) {
                        var tagTypeName = tagType.tagTypeName;

                        if (tagType && tagType.required === "True") {
                            tagTypeName += " [R]";
                        }

                        if (tagType.tags && tagType.tags.includes(tag)) {
                            return `${tag} [${tagTypeName}]`;
                        }
                    }
                }
                return tag;
            });

            return `(${tagsWithType.join(", ")})`;
        } catch (error) {
            console.error("Error formatting tags:", error);
            return "";
        }
    };

    const handleClick = (e: React.MouseEvent) => {
        // Handle Ctrl+click for navigation to asset page
        if (e.ctrlKey && item.type === "asset" && item.assetData) {
            const { databaseId, assetId } = item.assetData;
            if (databaseId && assetId) {
                // Open asset page in new window
                window.open(
                    `#/databases/${databaseId}/assets/${assetId}`,
                    "_blank",
                    "noopener,noreferrer"
                );
                return;
            }
        }

        // Normal click - select the node
        dispatch({
            type: "SELECT_NODE",
            payload: item,
        });
    };

    const handleToggleExpanded = (e: React.MouseEvent) => {
        e.stopPropagation();
        dispatch({
            type: "TOGGLE_NODE_EXPANDED",
            payload: item.id,
        });
    };

    // Get tags to display
    const tagsDisplay =
        showTags && assetDetails && assetDetails.tags ? formatTags(assetDetails.tags) : "";

    return (
        <div className="asset-links-tree-item">
            <div
                className={`asset-links-tree-item-content ${isSelected ? "selected" : ""}`}
                style={{ paddingLeft: `${item.level * 16}px` }}
                onClick={handleClick}
            >
                {hasChildren && (
                    <span className="asset-links-tree-item-caret" onClick={handleToggleExpanded}>
                        {item.expanded ? (
                            <Icon name="caret-down-filled" />
                        ) : (
                            <Icon name="caret-right-filled" />
                        )}
                    </span>
                )}

                <span className="asset-links-tree-item-icon">
                    {item.type === "root" ? (
                        item.expanded ? (
                            <Icon name="folder-open" />
                        ) : (
                            <Icon name="folder" />
                        )
                    ) : (
                        // For asset nodes, always use the same icon regardless of children
                        <Icon name="settings" />
                    )}
                </span>

                <span className="asset-links-tree-item-name">
                    {item.name}
                    {item.type === "root" && item.children.length > 0 && (
                        <span className="asset-links-count">({item.children.length})</span>
                    )}
                    {showTags && (
                        <span className="asset-links-tags">
                            {isLoadingDetails ? " (Loading tags...)" : tagsDisplay}
                        </span>
                    )}
                </span>
            </div>

            {hasChildren && item.expanded && (
                <div className="asset-links-tree-item-children">
                    {item.children.map((child) => (
                        <TreeItem key={child.id} item={child} />
                    ))}
                </div>
            )}
        </div>
    );
}

// Search Results Component
function SearchResults() {
    const context = useContext(AssetLinksContext);
    if (!context) {
        throw new Error("SearchResults must be used within an AssetLinksContext.Provider");
    }
    const { state, dispatch } = context;

    if (state.searchResults.length === 0) {
        return (
            <Box textAlign="center" padding="m">
                <div>No assets match your search</div>
            </Box>
        );
    }

    return (
        <div className="asset-links-search-results">
            {state.searchResults.map((item) => (
                <div
                    key={item.id}
                    className={`asset-links-search-result-item ${
                        state.selectedNode?.id === item.id ? "selected" : ""
                    }`}
                    onClick={(e: React.MouseEvent) => {
                        // Handle Ctrl+click for navigation to asset page
                        if (e.ctrlKey && item.type === "asset" && item.assetData) {
                            const { databaseId, assetId } = item.assetData;
                            if (databaseId && assetId) {
                                // Open asset page in new window
                                window.open(
                                    `#/databases/${databaseId}/assets/${assetId}`,
                                    "_blank",
                                    "noopener,noreferrer"
                                );
                                return;
                            }
                        }

                        // Normal click - select the node
                        dispatch({
                            type: "SELECT_NODE",
                            payload: item,
                        });
                    }}
                >
                    <span className="asset-links-search-result-icon">
                        <Icon name="settings" />
                    </span>
                    <span className="asset-links-search-result-name">{item.name}</span>
                    <span className="asset-links-search-result-type">
                        {item.relationshipType
                            ? item.relationshipType.charAt(0).toUpperCase() +
                              item.relationshipType.slice(1)
                            : "Unknown"}
                    </span>
                </div>
            ))}
        </div>
    );
}

export function AssetLinksTreeView() {
    const context = useContext(AssetLinksContext);
    if (!context) {
        throw new Error("AssetLinksTreeView must be used within an AssetLinksContext.Provider");
    }
    const { state, dispatch } = context;

    // Check if this is upload mode (NewAssetLinksState) or view mode (AssetLinksState)
    const isUploadMode = !("showChildrenSubTree" in state);
    const isViewMode = "showChildrenSubTree" in state;

    if (state.loading) {
        return (
            <Container>
                <Box textAlign="center" padding="m">
                    <Spinner size="normal" />
                    <div>Loading asset relationships...</div>
                </Box>
            </Container>
        );
    }

    if (state.error) {
        return (
            <Container>
                <SpaceBetween direction="vertical" size="m">
                    <Alert
                        type="error"
                        dismissible
                        onDismiss={() => dispatch({ type: "SET_ERROR", payload: null })}
                    >
                        {state.error}
                    </Alert>
                    {isViewMode && (
                        <Box textAlign="center">
                            <Button
                                variant="primary"
                                onClick={() => dispatch({ type: "REFRESH_DATA", payload: null })}
                            >
                                Retry
                            </Button>
                        </Box>
                    )}
                </SpaceBetween>
            </Container>
        );
    }

    // Upload mode rendering (simpler, no search)
    if (isUploadMode) {
        const treeData = (state as any).treeData || [];
        return (
            <Container
                header={
                    <Header variant="h2" description="Manage asset relationships for the new asset">
                        Asset Relationships
                    </Header>
                }
            >
                <SpaceBetween direction="vertical" size="m">
                    <div className="asset-links-tree">
                        {treeData.map((rootNode: TreeNodeItem) => (
                            <TreeItem key={rootNode.id} item={rootNode} />
                        ))}
                    </div>

                    {treeData.every((node: TreeNodeItem) => node.children.length === 0) && (
                        <div className="empty-state">
                            <p>No asset relationships defined yet.</p>
                            <p>
                                Select a relationship type above and click "Create Link" to add
                                relationships.
                            </p>
                        </div>
                    )}
                </SpaceBetween>
            </Container>
        );
    }

    // View mode rendering (full featured with search)
    return (
        <div className="asset-links-tree-container">
            <div className="asset-links-search-box">
                <div className="asset-links-search-container">
                    <TextFilter
                        filteringText={state.searchTerm || ""}
                        filteringPlaceholder="Search assets"
                        filteringAriaLabel="Search assets"
                        onChange={({ detail }) =>
                            dispatch({
                                type: "SET_SEARCH_TERM",
                                payload: { searchTerm: detail.filteringText },
                            })
                        }
                        countText={
                            state.isSearching
                                ? `${state.searchResults?.length || 0} matches`
                                : undefined
                        }
                    />
                    <Button
                        iconName="refresh"
                        variant="icon"
                        ariaLabel="Refresh asset relationships"
                        onClick={() => dispatch({ type: "REFRESH_DATA", payload: null })}
                        disabled={state.loading}
                    />
                </div>
            </div>

            {state.isSearching ? (
                <SearchResults />
            ) : (
                <div className="asset-links-tree">
                    {state.treeData.map((rootNode) => (
                        <TreeItem key={rootNode.id} item={rootNode} />
                    ))}
                </div>
            )}

            <div className="asset-links-tree-footer">
                {isViewMode && (
                    <div className="asset-links-toggle-controls">
                        <div className="asset-links-toggle-row">
                            <Toggle
                                onChange={() =>
                                    dispatch({
                                        type: "TOGGLE_TAGS_IN_TREE",
                                        payload: null,
                                    })
                                }
                                checked={state.showTagsInTree || false}
                            >
                                Show Tags
                            </Toggle>
                            <Toggle
                                onChange={() =>
                                    dispatch({
                                        type: "TOGGLE_CHILDREN_SUBTREE",
                                        payload: null,
                                    })
                                }
                                checked={state.showChildrenSubTree || false}
                            >
                                Show Children Sub-tree
                            </Toggle>
                        </div>
                    </div>
                )}
                <div className="selection-note">
                    Select a asset link relationship to view details
                    <br />
                    Ctrl+click on an asset to open it in a new window
                </div>
            </div>
        </div>
    );
}
