/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useReducer, useState, useCallback } from "react";
import {
    Alert,
    Box,
    BreadcrumbGroup,
    Container,
    FormField,
    Header,
    Select,
    SpaceBetween,
    AlertProps,
} from "@cloudscape-design/components";
import { useNavigate, useParams, useLocation } from "react-router";
import { appCache } from "../../services/appCache";
import { apiClient } from "../../services/apiClient";
import { AssetDetailContext, assetDetailReducer } from "../../context/AssetDetailContext";
import { AssetDetail } from "../../pages/AssetUpload/AssetUpload";
import { fetchAsset, fetchAssetLinks, fetchtagTypes } from "../../services/APIService";
import { fetchAllAssetVersions } from "../../services/AssetVersionService";
import { StatusMessageProvider } from "../common/StatusMessage";
import ErrorBoundary from "../common/ErrorBoundary";
import AssetDetailsPane from "./AssetDetailsPane";
import TabbedContainer from "./TabbedContainer";
import AssetDeleteModal from "../modals/AssetDeleteModal";
import { UpdateAsset } from "../createupdate/UpdateAsset";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";
import { MetadataContainer } from "../metadataV2";
import localforage from "localforage";
import Synonyms from "../../synonyms";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

// Fetch tag types and store in localStorage
fetchtagTypes().then((res) => {
    const tagTypesString = JSON.stringify(res);
    localStorage.setItem("tagTypes", tagTypesString);
});

export default function ViewAsset() {
    const { databaseId, assetId } = useParams();
    const navigate = useNavigate();
    const location = useLocation();

    // Extract file path from navigation state if provided
    const filePathToNavigate = (location.state as any)?.filePathToNavigate;

    // State
    const [state, dispatch] = useReducer(assetDetailReducer, {
        isMultiFile: false,
        isDistributable: true,
        Asset: [],
    } as AssetDetail);
    const [asset, setAsset] = useState<any>({});
    const [assetLinks, setAssetLinks] = useState<any>({});
    const [openUpdateAsset, setOpenUpdateAsset] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [apiError, setApiError] = useState<string | null>(null);
    const [showApiError, setShowApiError] = useState(false);
    const [apiErrorType, setApiErrorType] = useState<AlertProps.Type>("error");

    // Version selection state - initialize from URL query parameter for deep linking
    const [selectedVersionId, setSelectedVersionId] = useState<string | null>(() => {
        const params = new URLSearchParams(location.search);
        return params.get("assetVersionId") || null;
    });
    const [versions, setVersions] = useState<any[]>([]);
    const [versionsLoading, setVersionsLoading] = useState(false);

    // Config
    const config = appCache.getItem("config");
    const [useNoOpenSearch] = useState(
        config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );

    // Fetch asset data
    useEffect(() => {
        const fetchAssetData = async (): Promise<void> => {
            if (!databaseId || !assetId) return;

            try {
                const item = await fetchAsset({
                    databaseId,
                    assetId,
                    showArchived: true,
                });

                if (item !== false && item !== undefined && typeof item !== "string") {
                    setAsset(item);
                } else if (typeof item === "string" && item.includes("not found")) {
                    setApiError(
                        "Asset not found. The requested asset may have been deleted or you may not have permission to access it."
                    );
                    setShowApiError(true);
                    setApiErrorType("error");
                } else {
                    // Handle other API failure cases
                    setApiError(
                        "Failed to load asset data. The server returned an invalid response."
                    );
                    setShowApiError(true);
                    setApiErrorType("error");
                    console.error("Invalid asset data returned:", item);
                }
            } catch (error) {
                console.error("Error fetching asset:", error);
                setApiError("Failed to load asset data. Please try again later.");
                setShowApiError(true);
                setApiErrorType("error");
            }

            // Only attempt to load from localforage or set asset details if we don't have an API error
            if (!showApiError) {
                // Load from localforage if available
                try {
                    const value: any = await localforage.getItem(assetId);
                    if (value && value.Asset) {
                        dispatch({
                            type: "SET_ASSET_DETAIL",
                            payload: {
                                isMultiFile: value.isMultiFile,
                                assetId: assetId,
                                assetName: value.assetName,
                                databaseId: databaseId,
                                description: value.description,
                                key: value.key || value.assetLocation["Key"],
                                assetLocation: {
                                    Key: value.key || value.assetLocation["Key"],
                                },
                                assetType: value.assetType,
                                isDistributable: value.isDistributable,
                                Asset: value.Asset,
                            },
                        });
                    } else if (asset && asset.assetId) {
                        dispatch({
                            type: "SET_ASSET_DETAIL",
                            payload: {
                                isMultiFile: asset.isMultiFile,
                                assetId: assetId,
                                assetName: asset.assetName,
                                databaseId: databaseId,
                                description: asset.description,
                                key:
                                    asset.key ||
                                    (asset.assetLocation && asset.assetLocation["Key"]),
                                assetLocation: {
                                    Key:
                                        asset.key ||
                                        (asset.assetLocation && asset.assetLocation["Key"]),
                                },
                                assetType: asset.assetType,
                                isDistributable: asset.isDistributable,
                                Asset: [],
                            },
                        });
                    }
                } catch (error) {
                    console.error("Error loading from localforage:", error);
                }
            }
        };

        fetchAssetData();
    }, [databaseId, assetId, asset?.assetId]);

    // Fetch asset versions for the version dropdown
    useEffect(() => {
        const loadVersions = async () => {
            if (!databaseId || !assetId) return;
            setVersionsLoading(true);
            try {
                const [success, result] = await fetchAllAssetVersions({
                    databaseId,
                    assetId,
                });
                if (success && result?.versions) {
                    // Sort versions by dateModified (newest first)
                    const sorted = [...result.versions].sort((a: any, b: any) => {
                        const dateA = new Date(a.dateModified || 0).getTime();
                        const dateB = new Date(b.dateModified || 0).getTime();
                        return dateB - dateA;
                    });
                    setVersions(sorted);
                }
            } catch (error) {
                console.log("Failed to load asset versions:", error);
            } finally {
                setVersionsLoading(false);
            }
        };
        loadVersions();
    }, [databaseId, assetId]);

    // Handle version selection change and sync to URL query parameter
    const handleVersionChange = useCallback(
        (newVersionId: string | null) => {
            setSelectedVersionId(newVersionId);
            // Update URL query parameter for deep linking support
            const params = new URLSearchParams(location.search);
            if (newVersionId) {
                params.set("assetVersionId", newVersionId);
            } else {
                params.delete("assetVersionId");
            }
            const search = params.toString();
            navigate(
                { search: search ? `?${search}` : "" },
                { replace: true, state: location.state }
            );
        },
        [location.search, location.state, navigate]
    );

    // Handle opening the update asset modal
    const handleOpenUpdateAsset = () => {
        setOpenUpdateAsset(true);
    };

    // Handle opening the delete modal
    const handleOpenDeleteModal = () => {
        setShowDeleteModal(true);
    };

    // Handle opening the workflow selector modal
    const handleExecuteWorkflow = () => {
        setWorkflowOpen(true);
    };

    // State to trigger workflow tab refresh - this will be managed by TabbedContainer
    const [workflowRefreshTrigger, setWorkflowRefreshTrigger] = useState(0);

    // Function to refresh the workflow tab - this will be called by WorkflowSelectorWithModal
    // and will trigger TabbedContainer's callback
    const refreshWorkflowTab = useCallback(() => {
        console.log("ViewAsset: refreshWorkflowTab called, incrementing trigger");
        setWorkflowRefreshTrigger((prev) => prev + 1);
    }, []);

    return (
        <AssetDetailContext.Provider value={{ state, dispatch }}>
            <StatusMessageProvider>
                <Box padding={{ top: "s", horizontal: "l" }}>
                    <SpaceBetween direction="vertical" size="l">
                        {/* Breadcrumbs */}
                        <BreadcrumbGroup
                            items={[
                                { text: Synonyms.Databases, href: "#/databases/" },
                                {
                                    text: databaseId,
                                    href: "#/databases/" + databaseId + "/assets/",
                                },
                                { text: asset?.assetName, href: "" },
                            ]}
                            ariaLabel="Breadcrumbs"
                        />

                        {/* API Error Alert */}
                        {showApiError && (
                            <Alert
                                type={apiErrorType}
                                statusIconAriaLabel="Error"
                                header="API Error"
                                dismissible
                                onDismiss={() => setShowApiError(false)}
                            >
                                {apiError}
                            </Alert>
                        )}

                        {/* Asset Header */}
                        <Header variant="h1">
                            {showApiError
                                ? "Asset Information Unavailable"
                                : `${asset?.assetName || ""}${
                                      asset?.status === "archived" ? " (Archived)" : ""
                                  }`}
                        </Header>

                        {/* Version selector dropdown */}
                        {!showApiError && versions.length > 0 && (
                            <div style={{ maxWidth: "400px" }}>
                                <FormField label="Version Selection">
                                    <Select
                                        selectedOption={
                                            selectedVersionId
                                                ? {
                                                      label: `v${selectedVersionId}${
                                                          versions.find(
                                                              (v: any) =>
                                                                  v.Version === selectedVersionId
                                                          )?.versionAlias
                                                              ? ` (${
                                                                    versions.find(
                                                                        (v: any) =>
                                                                            v.Version ===
                                                                            selectedVersionId
                                                                    )?.versionAlias
                                                                })`
                                                              : ""
                                                      }`,
                                                      value: selectedVersionId,
                                                  }
                                                : {
                                                      label: "LATEST (Non-Versioned)",
                                                      value: "__LATEST__",
                                                  }
                                        }
                                        onChange={({ detail }) => {
                                            const val = detail.selectedOption.value;
                                            handleVersionChange(
                                                val === "__LATEST__" ? null : val || null
                                            );
                                        }}
                                        options={[
                                            {
                                                label: "LATEST (Non-Versioned)",
                                                value: "__LATEST__",
                                            },
                                            ...versions.map((v: any) => ({
                                                label: `v${v.Version}${
                                                    v.versionAlias ? ` (${v.versionAlias})` : ""
                                                } - ${v.Comment || "No comment"} (${new Date(
                                                    v.DateModified
                                                ).toLocaleDateString()})`,
                                                value: v.Version,
                                            })),
                                        ]}
                                        placeholder="Select version"
                                        loadingText="Loading versions..."
                                        statusType={versionsLoading ? "loading" : "finished"}
                                    />
                                </FormField>
                            </div>
                        )}

                        {/* Only render asset details and related components if there's no API error */}
                        {!showApiError && (
                            <>
                                {/* Asset Details Pane */}
                                <AssetDetailsPane
                                    asset={asset}
                                    databaseId={databaseId || ""}
                                    onOpenUpdateAsset={handleOpenUpdateAsset}
                                    onOpenDeleteModal={handleOpenDeleteModal}
                                />

                                {/* Tabbed Container */}
                                <TabbedContainer
                                    assetName={asset?.assetName || ""}
                                    assetId={assetId || ""}
                                    databaseId={databaseId || ""}
                                    onExecuteWorkflow={handleExecuteWorkflow}
                                    onWorkflowExecuted={refreshWorkflowTab}
                                    workflowExecutedTrigger={workflowRefreshTrigger}
                                    filePathToNavigate={filePathToNavigate}
                                    assetVersionId={selectedVersionId || undefined}
                                />

                                {/* Metadata - New MetadataV2 Component */}
                                <ErrorBoundary componentName="Metadata">
                                    {databaseId && assetId && (
                                        <MetadataContainer
                                            entityType="asset"
                                            entityId={assetId}
                                            databaseId={databaseId}
                                            mode="online"
                                            assetVersionId={selectedVersionId || undefined}
                                            readOnly={!!selectedVersionId}
                                        />
                                    )}
                                </ErrorBoundary>
                            </>
                        )}
                    </SpaceBetween>
                </Box>

                {/* Modals */}
                {asset && (
                    <UpdateAsset
                        asset={asset}
                        isOpen={openUpdateAsset}
                        onClose={() => setOpenUpdateAsset(false)}
                        onComplete={() => {
                            setOpenUpdateAsset(false);
                            window.location.reload();
                        }}
                    />
                )}

                <WorkflowSelectorWithModal
                    assetId={assetId || ""}
                    databaseId={databaseId || ""}
                    open={workflowOpen}
                    setOpen={setWorkflowOpen}
                    onWorkflowExecuted={refreshWorkflowTab}
                />

                <AssetDeleteModal
                    visible={showDeleteModal}
                    onDismiss={() => setShowDeleteModal(false)}
                    mode="asset"
                    selectedAssets={asset ? [asset] : []}
                    databaseId={databaseId}
                    onSuccess={(operation) => {
                        setShowDeleteModal(false);
                        // Navigate back to search page after successful deletion / archival
                        navigate(databaseId ? `/search/${databaseId}/assets` : "/search");
                    }}
                />
            </StatusMessageProvider>
        </AssetDetailContext.Provider>
    );
}
