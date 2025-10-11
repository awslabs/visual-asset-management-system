/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useReducer, useState, useCallback, useRef } from "react";
import { Cache } from "aws-amplify";
import { fetchAssetLinks } from "../../../services/APIService";
import { AssetLinksTreeView, AssetLinksContext } from "./components/AssetLinksTreeView";
import { AssetLinksDetailsPanel } from "./components/AssetLinksDetailsPanel";
import { CreateAssetLinkModal } from "./components/modals/CreateAssetLinkModal";
import { DeleteAssetLinkModal } from "./components/modals/DeleteAssetLinkModal";
import { assetLinksReducer, initialAssetLinksState } from "./utils/AssetLinksReducer";
import {
    AssetLinksTabProps,
    AssetNode,
    TreeNodeItem,
    NewAssetLinksData,
} from "./types/AssetLinksTypes";
import { featuresEnabled } from "../../../common/constants/featuresEnabled";
import ErrorBoundary from "../../common/ErrorBoundary";
import "./AssetLinksTab.css";

export function AssetLinksTab(props: AssetLinksTabProps) {
    const { mode } = props;

    if (mode === "view") {
        return <ViewModeAssetLinksTab {...props} />;
    } else {
        return <UploadModeAssetLinksTab {...props} />;
    }
}

// View mode component (existing functionality)
function ViewModeAssetLinksTab(props: AssetLinksTabProps) {
    const assetId = props.assetId!;
    const databaseId = props.databaseId!;
    const isActive = props.isActive!;

    const [state, dispatch] = useReducer(assetLinksReducer, initialAssetLinksState);

    // Modal states
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [createModalRelationshipType, setCreateModalRelationshipType] = useState<
        "related" | "parent" | "child"
    >("related");
    const [deleteModalData, setDeleteModalData] = useState<{
        assetLinkId: string;
        assetName: string;
        relationshipType: "related" | "parent" | "child";
        isSubChild?: boolean;
        parentAssetName?: string;
    } | null>(null);

    // Sub-child creation states
    const [isSubChildMode, setIsSubChildMode] = useState(false);
    const [parentAssetData, setParentAssetData] = useState<any>(null);

    // Config
    const config = Cache.getItem("config");
    const useNoOpenSearch = config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);

    // Fetch asset links data
    const fetchAssetLinksData = async () => {
        try {
            dispatch({ type: "SET_LOADING", payload: true });

            console.log("Fetching asset links with params:", {
                assetId,
                databaseId,
                childTreeView: state.showChildrenSubTree,
            });

            const response = await fetchAssetLinks({
                assetId,
                databaseId,
                childTreeView: state.showChildrenSubTree,
            });

            console.log("Raw API response:", JSON.stringify(response));

            if (response && typeof response === "object") {
                // Check if the response has the expected structure
                console.log("Response keys:", Object.keys(response));
                console.log("Response related type:", typeof response.related);
                console.log("Response parents type:", typeof response.parents);
                console.log("Response children type:", typeof response.children);

                // Accept the response even if it's wrapped in a message property
                if (response.message && typeof response.message === "object") {
                    console.log("Response is wrapped in message property, unwrapping");
                    const unwrappedResponse = response.message;

                    if (
                        unwrappedResponse.related !== undefined &&
                        unwrappedResponse.parents !== undefined &&
                        unwrappedResponse.children !== undefined
                    ) {
                        console.log("Unwrapped response has valid structure");
                        dispatch({ type: "SET_ASSET_LINKS_DATA", payload: unwrappedResponse });
                    } else {
                        console.error(
                            "Unwrapped response has invalid structure:",
                            unwrappedResponse
                        );
                        dispatch({
                            type: "SET_ERROR",
                            payload: "Invalid response format (unwrapped)",
                        });
                    }
                }
                // Check direct response structure
                else if (
                    response.related !== undefined &&
                    response.parents !== undefined &&
                    response.children !== undefined
                ) {
                    console.log("Response has valid structure");
                    dispatch({ type: "SET_ASSET_LINKS_DATA", payload: response });
                } else {
                    console.error("Unexpected API response format:", response);
                    dispatch({
                        type: "SET_ERROR",
                        payload: "Invalid response format (missing fields)",
                    });
                }
            } else {
                console.error("Invalid API response:", response);
                dispatch({ type: "SET_ERROR", payload: "Invalid response format (not an object)" });
            }
        } catch (error: any) {
            console.error("Error fetching asset links:", error);
            dispatch({
                type: "SET_ERROR",
                payload: error.message || "Failed to load asset relationships",
            });
        }
    };

    // Initial load and refresh when active
    useEffect(() => {
        if (isActive && assetId && databaseId) {
            fetchAssetLinksData();
        }
    }, [isActive, assetId, databaseId]);

    // Handle refresh trigger
    useEffect(() => {
        if (state.loading && assetId && databaseId) {
            fetchAssetLinksData();
        }
    }, [state.loading]);

    // Refetch when children sub-tree toggle changes
    useEffect(() => {
        if (assetId && databaseId) {
            fetchAssetLinksData();
        }
    }, [state.showChildrenSubTree]);

    // Handle create link
    const handleCreateLink = (relationshipType: "related" | "parent" | "child") => {
        setCreateModalRelationshipType(relationshipType);
        setShowCreateModal(true);
    };

    // Handle delete link
    const handleDeleteLink = (
        assetLinkId: string,
        assetName: string,
        relationshipType: "related" | "parent" | "child"
    ) => {
        // Check if this is a sub-child deletion by examining the selected node
        const selectedNode = state.selectedNode;
        const isSubChild =
            selectedNode?.type === "asset" &&
            selectedNode.relationshipType === "child" &&
            selectedNode.level !== undefined &&
            selectedNode.level >= 2;

        // For level 1 nodes (direct children), the parent is the top-level asset being viewed
        // We need to find the parent node's name
        let parentAssetName;
        if (isSubChild) {
            // For level 1 nodes, the parent is the top-level asset
            // We can get this from the current asset ID/database ID
            parentAssetName = "Top-level Asset"; // Default fallback

            // Try to find the actual asset name from the tree data
            const rootNodes = state.treeData;
            if (rootNodes && rootNodes.length > 0) {
                // The parent asset name would be in the breadcrumb or page title
                // For now, we'll use a generic name that's different from the child
                parentAssetName = "Parent Asset";
            }
        }

        setDeleteModalData({
            assetLinkId,
            assetName,
            relationshipType,
            isSubChild,
            parentAssetName: isSubChild ? parentAssetName : undefined,
        });
        setShowDeleteModal(true);
    };

    // Handle sub-child creation
    const handleCreateSubChildLink = (parentAssetData: any) => {
        console.log("handleCreateSubChildLink called with parentAssetData:", parentAssetData);
        setParentAssetData(parentAssetData);
        setIsSubChildMode(true);
        setCreateModalRelationshipType("child");
        setShowCreateModal(true);

        // Debug logging after state updates
        setTimeout(() => {
            console.log("Sub-child mode state:", {
                isSubChildMode,
                parentAssetData,
                createModalRelationshipType,
                showCreateModal,
            });
        }, 0);
    };

    // Handle modal success (refresh data)
    const handleModalSuccess = () => {
        // Clear the selected node to prevent showing stale data
        dispatch({ type: "SELECT_NODE", payload: null });
        // Refresh the data
        dispatch({ type: "REFRESH_DATA", payload: null });
    };

    // Handle modal dismiss
    const handleCreateModalDismiss = () => {
        setShowCreateModal(false);
        setCreateModalRelationshipType("related");
        setIsSubChildMode(false);
        setParentAssetData(null);
    };

    const handleDeleteModalDismiss = () => {
        setShowDeleteModal(false);
        setDeleteModalData(null);
    };

    return (
        <ErrorBoundary componentName="Asset Links Tab">
            <AssetLinksContext.Provider value={{ state, dispatch }}>
                <div className="asset-links-tab">
                    <div className="asset-links-tree-panel">
                        <AssetLinksTreeView />
                    </div>
                    <div className="asset-links-details-panel">
                        <AssetLinksDetailsPanel
                            onCreateLink={handleCreateLink}
                            onDeleteLink={handleDeleteLink}
                            onCreateSubChildLink={handleCreateSubChildLink}
                        />
                    </div>
                </div>

                {/* Create Asset Link Modal */}
                <CreateAssetLinkModal
                    visible={showCreateModal}
                    onDismiss={handleCreateModalDismiss}
                    relationshipType={createModalRelationshipType}
                    currentAssetId={assetId}
                    currentDatabaseId={databaseId}
                    onSuccess={handleModalSuccess}
                    noOpenSearch={useNoOpenSearch}
                    isSubChildMode={isSubChildMode}
                    parentAssetData={parentAssetData}
                />

                {/* Delete Asset Link Modal */}
                {deleteModalData && (
                    <DeleteAssetLinkModal
                        visible={showDeleteModal}
                        onDismiss={handleDeleteModalDismiss}
                        assetLinkId={deleteModalData.assetLinkId}
                        assetName={deleteModalData.assetName}
                        relationshipType={deleteModalData.relationshipType}
                        onSuccess={handleModalSuccess}
                        isSubChild={deleteModalData.isSubChild}
                        parentAssetName={deleteModalData.parentAssetName}
                    />
                )}
            </AssetLinksContext.Provider>
        </ErrorBoundary>
    );
}

// Upload mode component (simplified without loading states)
function UploadModeAssetLinksTab(props: AssetLinksTabProps) {
    const setValid = props.setValid!;
    const showErrors = props.showErrors!;
    const onAssetLinksChange = props.onAssetLinksChange!;
    const initialData = props.initialData;
    const databaseId = props.databaseId || "temp-upload-db"; // Use real database ID if provided, fallback to temp

    // Local asset links data for upload mode
    const [localAssetLinks, setLocalAssetLinks] = useState<NewAssetLinksData>({
        assetLinksFe: {
            related: [] as AssetNode[],
            parents: [] as AssetNode[],
            child: [] as AssetNode[],
        },
        assetLinks: {
            related: [] as string[],
            parents: [] as string[],
            child: [] as string[],
        },
        assetLinksMetadata: {
            related: {},
            parents: {},
            child: {},
        },
    });

    // Tree data state for upload mode (no loading, no API calls)
    const [treeData, setTreeData] = useState<TreeNodeItem[]>([
        {
            id: "related",
            name: "Related Assets",
            type: "root" as const,
            level: 0,
            expanded: true,
            children: [],
            relationshipType: "related" as const,
        },
        {
            id: "parents",
            name: "Parent Assets",
            type: "root" as const,
            level: 0,
            expanded: true,
            children: [],
            relationshipType: "parent" as const,
        },
        {
            id: "child",
            name: "Child Assets",
            type: "root" as const,
            level: 0,
            expanded: true,
            children: [],
            relationshipType: "child" as const,
        },
    ]);

    const [selectedNode, setSelectedNode] = useState<TreeNodeItem | null>(null);

    // Modal states
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [createModalRelationshipType, setCreateModalRelationshipType] = useState<
        "related" | "parent" | "child"
    >("related");

    // Config (get once, not in a loop)
    const [config] = useState(() => Cache.getItem("config"));
    const useNoOpenSearch = config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);

    // Use ref to track previous data and prevent infinite loops
    const prevAssetLinksRef = useRef<NewAssetLinksData>();

    // Initialize with existing data if provided
    useEffect(() => {
        if (initialData) {
            setLocalAssetLinks(initialData);
        }
    }, [initialData]);

    // Update parent component when local data changes (prevent infinite loops)
    useEffect(() => {
        // Only call parent callback if data actually changed
        if (JSON.stringify(prevAssetLinksRef.current) !== JSON.stringify(localAssetLinks)) {
            onAssetLinksChange(localAssetLinks);
            setValid(true); // Asset links are optional
            prevAssetLinksRef.current = localAssetLinks;
        }
    }, [localAssetLinks, onAssetLinksChange, setValid]);

    // Update tree data when local asset links change
    useEffect(() => {
        setTreeData((prevTreeData) => {
            return prevTreeData.map((rootNode) => {
                const relationshipType = rootNode.relationshipType!;
                // Handle the mapping between 'parent' and 'parents'
                const relationshipKey =
                    relationshipType === "parent" ? "parents" : relationshipType;
                const assets =
                    localAssetLinks.assetLinksFe[
                        relationshipKey as keyof typeof localAssetLinks.assetLinksFe
                    ] || [];

                return {
                    ...rootNode,
                    children: assets.map((asset: AssetNode, index: number) => ({
                        id: `${relationshipType}-${asset.assetId}-${
                            asset.assetLinkAliasId || "no-alias"
                        }-${index}`,
                        name: asset.assetName,
                        type: "asset" as const,
                        level: 1,
                        expanded: false,
                        children: [],
                        relationshipType,
                        assetData: asset,
                    })),
                };
            });
        });
    }, [localAssetLinks]);

    // Handle create link
    const handleCreateLink = (relationshipType: "related" | "parent" | "child") => {
        setCreateModalRelationshipType(relationshipType);
        setShowCreateModal(true);
    };

    // Handle delete link (for upload mode, this removes from local state)
    const handleDeleteLink = (
        assetLinkId: string,
        assetName: string,
        relationshipType: "related" | "parent" | "child"
    ) => {
        setLocalAssetLinks((prev: NewAssetLinksData) => {
            // Handle the mapping between 'parent' and 'parents'
            const relationshipKey = relationshipType === "parent" ? "parents" : relationshipType;

            // Filter by assetLinkId to remove only the specific link (not all links to the same asset)
            const updatedAssetLinksFe = prev.assetLinksFe[
                relationshipKey as keyof typeof prev.assetLinksFe
            ].filter((asset: AssetNode) => asset.assetLinkId !== assetLinkId);

            // Also update the assetLinks array (remove the assetId only if no other links to it exist)
            const remainingAssetIds = updatedAssetLinksFe.map((asset: AssetNode) => asset.assetId);
            const updatedAssetLinks = prev.assetLinks[
                relationshipKey as keyof typeof prev.assetLinks
            ].filter((id: string) => remainingAssetIds.includes(id));

            return {
                ...prev,
                assetLinksFe: {
                    ...prev.assetLinksFe,
                    [relationshipKey]: updatedAssetLinksFe,
                },
                assetLinks: {
                    ...prev.assetLinks,
                    [relationshipKey]: updatedAssetLinks,
                },
            };
        });
    };

    // Handle modal success (add new asset link)
    const handleModalSuccess = (
        assetNode?: AssetNode,
        relationshipType?: "related" | "parent" | "child"
    ) => {
        setShowCreateModal(false);

        // If asset data is provided (upload mode), add it to local state
        if (assetNode && relationshipType) {
            setLocalAssetLinks((prev: NewAssetLinksData) => {
                // Handle the mapping between 'parent' and 'parents'
                const relationshipKey =
                    relationshipType === "parent" ? "parents" : relationshipType;

                // Check for duplicate: same asset with same alias (or both no alias)
                const existingLinks =
                    prev.assetLinksFe[relationshipKey as keyof typeof prev.assetLinksFe];
                const isDuplicate = existingLinks.some((existing: AssetNode) => {
                    if (existing.assetId !== assetNode.assetId) return false;
                    // Both have no alias
                    if (!existing.assetLinkAliasId && !assetNode.assetLinkAliasId) return true;
                    // Both have the same alias
                    if (
                        existing.assetLinkAliasId &&
                        assetNode.assetLinkAliasId &&
                        existing.assetLinkAliasId === assetNode.assetLinkAliasId
                    )
                        return true;
                    return false;
                });

                if (isDuplicate) {
                    console.warn("Duplicate asset link detected in upload mode");
                    return prev; // Don't add duplicate
                }

                return {
                    ...prev,
                    assetLinksFe: {
                        ...prev.assetLinksFe,
                        [relationshipKey]: [
                            ...prev.assetLinksFe[relationshipKey as keyof typeof prev.assetLinksFe],
                            assetNode,
                        ],
                    },
                    assetLinks: {
                        ...prev.assetLinks,
                        [relationshipKey]: [
                            ...prev.assetLinks[relationshipKey as keyof typeof prev.assetLinks],
                            assetNode.assetId,
                        ],
                    },
                };
            });
        }
    };

    // Handle metadata changes for asset links in upload mode
    const handleAssetLinkMetadataChange = useCallback(
        (assetId: string, relationshipType: "related" | "parent" | "child", metadata: any[]) => {
            setLocalAssetLinks((prev) => {
                const relationshipKey =
                    relationshipType === "parent" ? "parents" : relationshipType;

                // Update the metadata in the assetLinksMetadata structure
                const updatedMetadata = {
                    ...prev.assetLinksMetadata,
                    [relationshipKey]: {
                        ...prev.assetLinksMetadata?.[relationshipKey],
                        [assetId]: metadata,
                    },
                };

                // Also update the asset node itself to include the metadata
                const updatedAssetLinksFe = {
                    ...prev.assetLinksFe,
                    [relationshipKey]: prev.assetLinksFe[
                        relationshipKey as keyof typeof prev.assetLinksFe
                    ].map((asset: AssetNode) =>
                        asset.assetId === assetId ? { ...asset, metadata: metadata } : asset
                    ),
                };

                return {
                    ...prev,
                    assetLinksFe: updatedAssetLinksFe,
                    assetLinksMetadata: updatedMetadata,
                };
            });
        },
        []
    );

    // Handle modal dismiss
    const handleCreateModalDismiss = () => {
        setShowCreateModal(false);
        setCreateModalRelationshipType("related");
    };

    // Create a simplified state for upload mode (no loading, no API calls)
    // NOTE: Do NOT include showChildrenSubTree property - its absence indicates upload mode
    const uploadModeState: any = {
        treeData,
        selectedNode,
        loading: false, // Never loading in upload mode
        error: null,
        assetLinksData: {
            related: localAssetLinks.assetLinksFe.related,
            parents: localAssetLinks.assetLinksFe.parents,
            children: localAssetLinks.assetLinksFe.child,
        },
        metadata: { metadata: [], loading: false, error: null },
        searchTerm: "",
        searchResults: [],
        isSearching: false,
        // Add metadata change handler to the state so it can be accessed by child components
        onAssetLinkMetadataChange: handleAssetLinkMetadataChange,
    };

    // Simple dispatch for upload mode (no complex state management)
    const uploadModeDispatch = (action: any) => {
        switch (action.type) {
            case "SELECT_NODE":
                setSelectedNode(action.payload);
                break;
            case "TOGGLE_NODE_EXPANDED":
                setTreeData((prev) =>
                    prev.map((node) =>
                        node.id === action.payload ? { ...node, expanded: !node.expanded } : node
                    )
                );
                break;
            // Ignore other actions that don't apply to upload mode
            default:
                break;
        }
    };

    return (
        <ErrorBoundary componentName="Asset Links Tab (Upload Mode)">
            <AssetLinksContext.Provider
                value={{ state: uploadModeState, dispatch: uploadModeDispatch }}
            >
                <div className="asset-links-tab">
                    <div className="asset-links-tree-panel">
                        <AssetLinksTreeView />
                    </div>
                    <div className="asset-links-details-panel">
                        <AssetLinksDetailsPanel
                            onCreateLink={handleCreateLink}
                            onDeleteLink={handleDeleteLink}
                        />
                    </div>
                </div>

                {/* Create Asset Link Modal for upload mode */}
                <CreateAssetLinkModal
                    visible={showCreateModal}
                    onDismiss={handleCreateModalDismiss}
                    relationshipType={createModalRelationshipType}
                    currentAssetId="temp-upload-asset" // Temporary ID for upload mode
                    currentDatabaseId={databaseId} // Use real database ID from props
                    onSuccess={handleModalSuccess}
                    noOpenSearch={useNoOpenSearch}
                />
            </AssetLinksContext.Provider>
        </ErrorBoundary>
    );
}

export default AssetLinksTab;
