/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    AssetLinksState,
    AssetLinksAction,
    TreeNodeItem,
    AssetLinksData,
    AssetNode,
    AssetTreeNode,
} from "../types/AssetLinksTypes";

export const initialAssetLinksState: AssetLinksState = {
    treeData: [],
    selectedNode: null,
    showChildrenSubTree: true, // Set to true by default
    showTagsInTree: false, // Default to false for performance reasons
    loading: true,
    error: null,
    assetLinksData: null,
    metadata: {
        metadata: [],
        loading: false,
        error: null,
    },
    searchTerm: "",
    searchResults: [],
    isSearching: false,
    assetDetailsCache: {}, // Cache for asset details
};

function buildTreeFromAssetLinks(
    assetLinksData: AssetLinksData,
    showChildrenSubTree: boolean
): TreeNodeItem[] {
    const rootNodes: TreeNodeItem[] = [
        {
            id: "related-root",
            name: "Related",
            type: "root",
            relationshipType: "related",
            children: [],
            expanded: true,
            level: 0,
        },
        {
            id: "parents-root",
            name: "Parents",
            type: "root",
            relationshipType: "parent",
            children: [],
            expanded: true,
            level: 0,
        },
        {
            id: "children-root",
            name: "Children",
            type: "root",
            relationshipType: "child",
            children: [],
            expanded: true,
            level: 0,
        },
    ];

    // Add related assets
    if (assetLinksData.related) {
        rootNodes[0].children = assetLinksData.related.map((asset: AssetNode) => ({
            id: `related-${asset.assetLinkId}`,
            name: asset.assetName,
            type: "asset" as const,
            relationshipType: "related" as const,
            assetData: asset,
            children: [],
            expanded: false,
            level: 1,
        }));
    }

    // Add parent assets
    if (assetLinksData.parents) {
        rootNodes[1].children = assetLinksData.parents.map((asset: AssetNode) => ({
            id: `parent-${asset.assetLinkId}`,
            name: asset.assetName,
            type: "asset" as const,
            relationshipType: "parent" as const,
            assetData: asset,
            children: [],
            expanded: false,
            level: 1,
        }));
    }

    // Add child assets (with potential tree structure)
    if (assetLinksData.children) {
        if (
            showChildrenSubTree &&
            Array.isArray(assetLinksData.children) &&
            assetLinksData.children.length > 0
        ) {
            // Check if the first item has children property (tree structure)
            const firstChild = assetLinksData.children[0] as AssetTreeNode;
            if (firstChild && "children" in firstChild) {
                // Handle tree structure
                rootNodes[2].children = buildChildTreeNodes(
                    assetLinksData.children as AssetTreeNode[],
                    1
                );
            } else {
                // Handle flat structure
                rootNodes[2].children = (assetLinksData.children as AssetNode[]).map(
                    (asset: AssetNode) => ({
                        id: `child-${asset.assetLinkId}`,
                        name: asset.assetName,
                        type: "asset" as const,
                        relationshipType: "child" as const,
                        assetData: asset,
                        children: [],
                        expanded: false,
                        level: 1,
                    })
                );
            }
        } else {
            // Handle flat structure
            rootNodes[2].children = (assetLinksData.children as AssetNode[]).map(
                (asset: AssetNode) => ({
                    id: `child-${asset.assetLinkId}`,
                    name: asset.assetName,
                    type: "asset" as const,
                    relationshipType: "child" as const,
                    assetData: asset,
                    children: [],
                    expanded: false,
                    level: 1,
                })
            );
        }
    }

    return rootNodes;
}

function buildChildTreeNodes(treeNodes: AssetTreeNode[], level: number): TreeNodeItem[] {
    return treeNodes.map((node: AssetTreeNode) => ({
        id: `child-${node.assetLinkId}`,
        name: node.assetName,
        type: "asset" as const,
        relationshipType: "child" as const,
        assetData: node,
        children: node.children ? buildChildTreeNodes(node.children, level + 1) : [],
        expanded: false,
        level,
    }));
}

function toggleNodeExpanded(nodes: TreeNodeItem[], nodeId: string): TreeNodeItem[] {
    return nodes.map((node) => {
        if (node.id === nodeId) {
            return { ...node, expanded: !node.expanded };
        }
        if (node.children.length > 0) {
            return { ...node, children: toggleNodeExpanded(node.children, nodeId) };
        }
        return node;
    });
}

// Helper function to search through tree nodes
function searchTreeNodes(nodes: TreeNodeItem[], searchTerm: string): TreeNodeItem[] {
    const results: TreeNodeItem[] = [];

    // Function to recursively search through nodes
    function searchRecursive(nodeList: TreeNodeItem[]) {
        for (const node of nodeList) {
            // Only include asset nodes in search results, not root categories
            if (
                node.type === "asset" &&
                node.name.toLowerCase().includes(searchTerm.toLowerCase())
            ) {
                results.push(node);
            }

            // Search through children
            if (node.children && node.children.length > 0) {
                searchRecursive(node.children);
            }
        }
    }

    searchRecursive(nodes);
    return results;
}

export function assetLinksReducer(
    state: AssetLinksState,
    action: AssetLinksAction
): AssetLinksState {
    switch (action.type) {
        case "SET_LOADING":
            return {
                ...state,
                loading: action.payload,
            };

        case "SET_ERROR":
            return {
                ...state,
                error: action.payload,
                loading: false,
            };

        case "SET_ASSET_LINKS_DATA":
            return {
                ...state,
                assetLinksData: action.payload,
                treeData: buildTreeFromAssetLinks(
                    action.payload,
                    state.showChildrenSubTree || false
                ),
                loading: false,
                error: null,
            };

        case "SELECT_NODE":
            return {
                ...state,
                selectedNode: action.payload,
            };

        case "TOGGLE_NODE_EXPANDED":
            return {
                ...state,
                treeData: toggleNodeExpanded(state.treeData, action.payload),
            };

        case "TOGGLE_CHILDREN_SUBTREE":
            const newShowChildrenSubTree = !state.showChildrenSubTree;
            return {
                ...state,
                showChildrenSubTree: newShowChildrenSubTree,
                treeData: state.assetLinksData
                    ? buildTreeFromAssetLinks(state.assetLinksData, newShowChildrenSubTree)
                    : state.treeData,
            };
            
        case "TOGGLE_TAGS_IN_TREE":
            return {
                ...state,
                showTagsInTree: !state.showTagsInTree,
            };
            
        case "SET_ASSET_DETAILS":
            return {
                ...state,
                assetDetailsCache: {
                    ...state.assetDetailsCache,
                    [action.payload.assetId]: action.payload.details,
                },
            };

        case "REFRESH_DATA":
            return {
                ...state,
                loading: true,
                error: null,
            };

        case "SET_METADATA_LOADING":
            return {
                ...state,
                metadata: {
                    ...state.metadata,
                    loading: action.payload,
                },
            };

        case "SET_METADATA_ERROR":
            return {
                ...state,
                metadata: {
                    ...state.metadata,
                    error: action.payload,
                    loading: false,
                },
            };

        case "SET_METADATA":
            return {
                ...state,
                metadata: {
                    metadata: action.payload,
                    loading: false,
                    error: null,
                },
            };

        case "ADD_METADATA":
            return {
                ...state,
                metadata: {
                    ...state.metadata,
                    metadata: [...state.metadata.metadata, action.payload],
                },
            };

        case "UPDATE_METADATA":
            return {
                ...state,
                metadata: {
                    ...state.metadata,
                    metadata: state.metadata.metadata.map((meta) =>
                        meta.assetLinkId === action.payload.assetLinkId &&
                        meta.metadataKey === action.payload.metadataKey
                            ? action.payload
                            : meta
                    ),
                },
            };

        case "DELETE_METADATA":
            return {
                ...state,
                metadata: {
                    ...state.metadata,
                    metadata: state.metadata.metadata.filter(
                        (meta) =>
                            !(
                                meta.assetLinkId === action.payload.assetLinkId &&
                                meta.metadataKey === action.payload.metadataKey
                            )
                    ),
                },
            };

        case "SET_SEARCH_TERM":
            const searchTerm = action.payload.searchTerm;
            const isSearching = searchTerm.length > 0;

            // If search term is empty, clear search results
            if (!isSearching) {
                return {
                    ...state,
                    searchTerm,
                    isSearching,
                    searchResults: [],
                };
            }

            // Otherwise, perform search
            const searchResults = searchTreeNodes(state.treeData, searchTerm);

            return {
                ...state,
                searchTerm,
                isSearching,
                searchResults,
            };

        case "SET_SEARCH_RESULTS":
            return {
                ...state,
                searchResults: action.payload.searchResults,
            };

        default:
            return state;
    }
}
