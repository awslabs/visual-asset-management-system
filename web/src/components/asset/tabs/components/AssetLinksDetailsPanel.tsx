/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useState, useEffect, useRef } from "react";
import {
    Box,
    Button,
    SpaceBetween,
    Container,
    Header,
    Spinner,
    Link,
} from "@cloudscape-design/components";
import { AssetLinksContext } from "./AssetLinksTreeView";
import { AssetLinkMetadata } from "./AssetLinkMetadata";
import { fetchAsset } from "../../../../services/APIService";
import "./AssetLinksDetailsPanel.css";

interface AssetLinksDetailsPanelProps {
    onCreateLink: (relationshipType: "related" | "parent" | "child") => void;
    onDeleteLink: (
        assetLinkId: string,
        assetName: string,
        relationshipType: "related" | "parent" | "child"
    ) => void;
    onCreateSubChildLink?: (parentAssetData: any) => void;
}

export function AssetLinksDetailsPanel({
    onCreateLink,
    onDeleteLink,
    onCreateSubChildLink,
}: AssetLinksDetailsPanelProps) {
    const context = useContext(AssetLinksContext);
    if (!context) {
        throw new Error("AssetLinksDetailsPanel must be used within an AssetLinksContext.Provider");
    }
    const { state } = context;

    const selectedNode = state.selectedNode;

    // State for linked asset details
    const [linkedAssetDetails, setLinkedAssetDetails] = useState<any>(null);
    const [loadingAssetDetails, setLoadingAssetDetails] = useState(false);
    const [assetDetailsError, setAssetDetailsError] = useState<string | null>(null);

    // Ref to track the last fetched asset to prevent duplicate calls
    const lastFetchedAssetRef = useRef<string | null>(null);
    const fetchControllerRef = useRef<AbortController | null>(null);

    // Fetch linked asset details when an asset is selected
    useEffect(() => {
        const fetchLinkedAssetDetails = async () => {
            // Only fetch for asset nodes, not root nodes
            if (selectedNode?.type !== "asset" || !selectedNode.assetData) {
                setLinkedAssetDetails(null);
                setAssetDetailsError(null);
                lastFetchedAssetRef.current = null;
                return;
            }

            const { databaseId, assetId } = selectedNode.assetData;
            if (!databaseId || !assetId) {
                setLinkedAssetDetails(null);
                setAssetDetailsError("Missing asset information");
                lastFetchedAssetRef.current = null;
                return;
            }

            // Create a unique key for this asset
            const assetKey = `${databaseId}:${assetId}`;

            // Prevent duplicate calls for the same asset
            if (lastFetchedAssetRef.current === assetKey) {
                console.log(
                    "[AssetLinksDetailsPanel] Skipping duplicate fetch for asset:",
                    assetKey
                );
                return;
            }

            // Cancel any ongoing fetch
            if (fetchControllerRef.current) {
                fetchControllerRef.current.abort();
            }

            // Create new abort controller for this fetch
            fetchControllerRef.current = new AbortController();
            const signal = fetchControllerRef.current.signal;

            lastFetchedAssetRef.current = assetKey;
            setLoadingAssetDetails(true);
            setAssetDetailsError(null);

            try {
                console.log("[AssetLinksDetailsPanel] Fetching linked asset details for:", {
                    databaseId,
                    assetId,
                    assetKey,
                });
                const assetDetails = await fetchAsset({
                    databaseId,
                    assetId,
                    showArchived: true,
                });

                // Check if this request was aborted
                if (signal.aborted) {
                    console.log("[AssetLinksDetailsPanel] Fetch aborted for asset:", assetKey);
                    return;
                }

                if (
                    assetDetails !== false &&
                    assetDetails !== undefined &&
                    typeof assetDetails !== "string"
                ) {
                    setLinkedAssetDetails(assetDetails);
                    console.log(
                        "[AssetLinksDetailsPanel] Linked asset details fetched successfully for:",
                        assetKey
                    );
                } else if (typeof assetDetails === "string" && assetDetails.includes("not found")) {
                    setAssetDetailsError("Asset not found or access denied");
                } else {
                    setAssetDetailsError("Failed to load asset details");
                    console.error(
                        "[AssetLinksDetailsPanel] Invalid asset details returned for:",
                        assetKey,
                        assetDetails
                    );
                }
            } catch (error: any) {
                if (error.name === "AbortError") {
                    console.log("[AssetLinksDetailsPanel] Fetch was aborted for asset:", assetKey);
                    return;
                }
                console.error(
                    "[AssetLinksDetailsPanel] Error fetching linked asset details for:",
                    assetKey,
                    error
                );
                setAssetDetailsError("Failed to load asset details");
            } finally {
                if (!signal.aborted) {
                    setLoadingAssetDetails(false);
                }
            }
        };

        // Add a small delay to debounce rapid selections
        const timeoutId = setTimeout(fetchLinkedAssetDetails, 100);

        // Cleanup function to abort ongoing requests and clear timeout
        return () => {
            clearTimeout(timeoutId);
            if (fetchControllerRef.current) {
                fetchControllerRef.current.abort();
            }
        };
    }, [selectedNode?.type, selectedNode?.assetData?.databaseId, selectedNode?.assetData?.assetId]); // Only trigger when the actual asset ID changes, not the entire selectedNode object

    // Format tags with tag types (same logic as AssetDetailsPane)
    const formatLinkedAssetTags = (tags: any[]) => {
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

    const getRelationshipTypeDisplay = () => {
        if (!selectedNode?.relationshipType) return "";

        if (selectedNode.type === "root") {
            return (
                selectedNode.relationshipType.charAt(0).toUpperCase() +
                selectedNode.relationshipType.slice(1)
            );
        } else if (selectedNode.type === "asset") {
            // For asset nodes, determine if it's a children tree node
            if (
                selectedNode.relationshipType === "child" &&
                selectedNode.level &&
                selectedNode.level > 1
            ) {
                return "Children Tree";
            } else {
                return (
                    selectedNode.relationshipType.charAt(0).toUpperCase() +
                    selectedNode.relationshipType.slice(1)
                );
            }
        }
        return "";
    };

    const getUnauthorizedCount = () => {
        if (!selectedNode?.relationshipType || !state.assetLinksData?.unauthorizedCounts) return 0;

        const counts = state.assetLinksData.unauthorizedCounts;
        switch (selectedNode.relationshipType) {
            case "related":
                return counts.related || 0;
            case "parent":
                return counts.parents || 0;
            case "child":
                return counts.children || 0;
            default:
                return 0;
        }
    };

    if (!selectedNode) {
        return (
            <Box textAlign="center" padding="xl">
                <div>Select a relationship or asset to view details</div>
            </Box>
        );
    }

    // Determine action buttons based on node type
    const renderActionButtons = () => {
        if (selectedNode.type === "root" && selectedNode.relationshipType) {
            const relationshipType = selectedNode.relationshipType;
            const buttonText = `Create ${
                relationshipType.charAt(0).toUpperCase() + relationshipType.slice(1)
            } Asset Link`;

            return (
                <Button variant="primary" onClick={() => onCreateLink(relationshipType)}>
                    {buttonText}
                </Button>
            );
        } else if (
            selectedNode.type === "asset" &&
            selectedNode.assetData &&
            selectedNode.relationshipType
        ) {
            const relationshipType = selectedNode.relationshipType;

            // Check if this is a child node (child relationship with level >= 1)
            const isChildNode =
                relationshipType === "child" &&
                selectedNode.level !== undefined &&
                selectedNode.level >= 1;

            const buttons = [];

            // Always show delete button for asset nodes
            // Only use "Delete Sub-Child Asset Link" text for nodes at level >= 2 (2nd level and deeper)
            const isSubChildNode =
                relationshipType === "child" &&
                selectedNode.level !== undefined &&
                selectedNode.level >= 2;
            const deleteButtonText = isSubChildNode
                ? "Delete Sub-Child Asset Link"
                : `Delete ${
                      relationshipType.charAt(0).toUpperCase() + relationshipType.slice(1)
                  } Asset Link`;
            buttons.push(
                <Button
                    key="delete"
                    variant="normal"
                    onClick={() =>
                        onDeleteLink(
                            selectedNode.assetData!.assetLinkId,
                            selectedNode.assetData!.assetName,
                            relationshipType
                        )
                    }
                >
                    {deleteButtonText}
                </Button>
            );

            // Show "Create Sub-Child Asset Link" button for child nodes (only for child relationships)
            if (isChildNode && onCreateSubChildLink) {
                buttons.push(
                    <Button
                        key="create-sub-child"
                        variant="primary"
                        onClick={() => onCreateSubChildLink(selectedNode.assetData)}
                    >
                        Create Sub-Child Asset Link
                    </Button>
                );
            }

            return buttons.length > 0 ? buttons : null;
        }

        return null;
    };

    return (
        <div className="asset-links-details-panel">
            <div className="asset-links-details-header">
                <div
                    style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        width: "100%",
                    }}
                >
                    <div
                        style={{
                            flexShrink: 1,
                            overflow: "hidden",
                            marginRight: "16px",
                            maxWidth: "70%",
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                            flexWrap: "nowrap",
                        }}
                    >
                        <div
                            style={{
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                minWidth: 0,
                                flexShrink: 1,
                            }}
                        >
                            <Header variant="h3">{selectedNode.name}</Header>
                        </div>
                        {selectedNode.type === "asset" &&
                            selectedNode.assetData &&
                            selectedNode.assetData.databaseId &&
                            selectedNode.assetData.assetId && (
                                <div style={{ flexShrink: 0, whiteSpace: "nowrap" }}>
                                    <Link
                                        href={`#/databases/${selectedNode.assetData.databaseId}/assets/${selectedNode.assetData.assetId}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        fontSize="body-s"
                                    >
                                        (View Asset)
                                    </Link>
                                </div>
                            )}
                    </div>
                    <div
                        className="asset-links-actions"
                        style={{
                            flexShrink: 0,
                            display: "flex",
                            flexWrap: "nowrap",
                            minWidth: "fit-content",
                        }}
                    >
                        <SpaceBetween direction="horizontal" size="xs">
                            {renderActionButtons()}
                        </SpaceBetween>
                    </div>
                </div>
            </div>

            <div className="asset-links-details-content">
                <div className="asset-links-info-section">
                    {selectedNode.relationshipType && (
                        <div className="asset-links-info-item">
                            <div className="asset-links-info-label">Relationship Type:</div>
                            <div className="asset-links-info-value">
                                {getRelationshipTypeDisplay()}
                            </div>
                        </div>
                    )}
                    {selectedNode.relationshipType && (
                        <div className="asset-links-info-item">
                            <div className="asset-links-info-label">Unauthorized Sub-Assets:</div>
                            <div className="asset-links-info-value">{getUnauthorizedCount()}</div>
                        </div>
                    )}
                    {/* Linked Asset Tags Section - Only show for asset nodes */}
                    {selectedNode.type === "asset" && (
                        <div className="asset-links-info-item">
                            <div className="asset-links-info-label">Linked Asset Tags:</div>
                            <div className="asset-links-info-value">
                                {loadingAssetDetails ? (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Spinner />
                                        <span>Loading asset details...</span>
                                    </SpaceBetween>
                                ) : assetDetailsError ? (
                                    <span style={{ color: "#d13212", fontStyle: "italic" }}>
                                        {assetDetailsError}
                                    </span>
                                ) : linkedAssetDetails ? (
                                    <span>{formatLinkedAssetTags(linkedAssetDetails.tags)}</span>
                                ) : (
                                    <span style={{ fontStyle: "italic" }}>
                                        No asset details available
                                    </span>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {selectedNode.type === "asset" && selectedNode.assetData && (
                    <AssetLinkMetadata
                        key={selectedNode.assetData.assetLinkId} // Force re-mount when assetLinkId changes
                        assetLinkId={selectedNode.assetData.assetLinkId}
                        selectedNode={selectedNode}
                        mode={state.showChildrenSubTree !== undefined ? "view" : "upload"} // Detect mode based on showChildrenSubTree property
                        initialMetadata={selectedNode.assetData.metadata || []}
                        onMetadataChange={(metadata) => {
                            // Handle metadata changes if needed
                            console.log("Metadata updated:", metadata);

                            // In upload mode, we need to notify the parent about metadata changes
                            if (
                                state.showChildrenSubTree === undefined &&
                                selectedNode.assetData &&
                                selectedNode.relationshipType
                            ) {
                                // This is upload mode - use the metadata change handler from the state
                                if (state.onAssetLinkMetadataChange) {
                                    state.onAssetLinkMetadataChange(
                                        selectedNode.assetData.assetId,
                                        selectedNode.relationshipType,
                                        metadata
                                    );
                                }
                            }
                        }}
                    />
                )}
            </div>
        </div>
    );
}
