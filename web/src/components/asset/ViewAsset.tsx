/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useReducer, useRef, useState, useCallback } from "react";
import {
    Alert,
    Box,
    BreadcrumbGroup,
    Container,
    Header,
    SpaceBetween,
    StatusIndicator,
    AlertProps,
} from "@cloudscape-design/components";
import { useNavigate, useParams, useLocation } from "react-router";
import { AssetDetailContext, assetDetailReducer } from "../../context/AssetDetailContext";
import { ArchivedAssetContext } from "../../context/ArchivedAssetContext";
import { AssetDetail } from "../../pages/AssetUpload/AssetUpload";
import { fetchAssetLinks, fetchAssetWithStatus, fetchtagTypes } from "../../services/APIService";
import { fetchAllAssetVersions } from "../../services/AssetVersionService";
import { StatusMessageProvider } from "../common/StatusMessage";
import ErrorBoundary from "../common/ErrorBoundary";
import AssetDetailsPane from "./AssetDetailsPane";
import TabbedContainer from "./TabbedContainer";
import AssetDeleteModal from "../modals/AssetDeleteModal";
import AssetUnarchiveModal from "../modals/AssetUnarchiveModal";
import { UpdateAsset } from "../createupdate/UpdateAsset";
import { MetadataContainer } from "../metadataV2";
import Synonyms from "../../synonyms";
import { usePageTitle } from "../../hooks/usePageTitle";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { routeDisabledReason } from "../../features/orchestration/permissions/routeDisabledReason";
import { ASSET_TAB_QUERY_PARAM } from "../zipDownloads/zipDownloads";

const ASSET_METADATA_ROUTE = "/database/{databaseId}/assets/{assetId}/metadata";
const ASSET_UNARCHIVE_ROUTE = "/database/{databaseId}/assets/{assetId}/unarchiveAsset";
const ASSET_PERMANENT_DELETE_ROUTE = "/database/{databaseId}/assets/{assetId}/deleteAsset";

// Fetch tag types and store in localStorage
fetchtagTypes().then((res) => {
    const tagTypesString = JSON.stringify(res);
    localStorage.setItem("tagTypes", tagTypesString);
});

export default function ViewAsset() {
    const { databaseId, assetId } = useParams();
    const navigate = useNavigate();
    const location = useLocation();

    // Extract file path from URL query string OR navigation state. The
    // query-param form (`?filePath=...`) lets users right-click → "Open in
    // new tab" (or copy/share the URL) and still land on the right file
    // inside the file manager — router state is lost on a fresh page load.
    // Router state remains supported so existing in-app navigations (which
    // pass `state: { filePathToNavigate }`) keep working without changes,
    // and so callers can avoid polluting the address bar when they don't
    // need a shareable link.
    //
    // Precedent: matches ViewFile's behavior, where the URL is the source
    // of truth for file path / version. If both are provided, the query
    // param wins so a deep link is always authoritative.
    const filePathToNavigate = React.useMemo(() => {
        const params = new URLSearchParams(location.search);
        const fromQuery = params.get("filePath");
        if (fromQuery) return fromQuery;
        return (location.state as any)?.filePathToNavigate ?? undefined;
    }, [location.search, location.state]);

    // `?tab=` selects a tab (the Download Queue link of a new zip job uses it). The tab follows the
    // query while the page stays mounted, and a tab the user picks is written back to it.
    const requestedTabId = React.useMemo(
        () => new URLSearchParams(location.search).get(ASSET_TAB_QUERY_PARAM),
        [location.search]
    );
    const handleTabChange = useCallback(
        (tabId: string) => {
            const params = new URLSearchParams(location.search);
            if (tabId === "file-manager") params.delete(ASSET_TAB_QUERY_PARAM);
            else params.set(ASSET_TAB_QUERY_PARAM, tabId);
            const search = params.toString();
            if (`?${search}` === location.search || (!search && !location.search)) return;
            navigate(
                { search: search ? `?${search}` : "" },
                { replace: true, state: location.state }
            );
        },
        [location.search, location.state, navigate]
    );

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
    const [showUnarchiveModal, setShowUnarchiveModal] = useState(false);
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
    // Counts the changes made on the Versions tab, each of which reloads the asset and its versions.
    const [versionsRevision, setVersionsRevision] = useState(0);
    const versionsAssetKey = useRef("");

    usePageTitle(databaseId, asset?.assetName);
    const assetLoaded = !!assetId && asset?.assetId === assetId;
    // An archived asset is shown read-only: every panel reads with showArchived and offers no
    // writes, downloads or viewers; the page offers Unarchive and Delete permanently instead.
    const isArchived = assetLoaded && asset?.status === "archived";

    // The role's API route permissions decide which asset actions are offered; the API still
    // authorizes each request.
    const { loading: permissionsLoading, failed: permissionsFailed, can } = useAllowedRoutes();
    const deleteDisabledReason = routeDisabledReason(
        permissionsLoading,
        permissionsFailed,
        can("DELETE", "/database/{databaseId}/assets/{assetId}/archiveAsset"),
        `Your role does not allow archiving or deleting this ${Synonyms.asset}.`
    );
    // The metadata editor adds with POST and updates with PUT; either grant keeps it editable.
    const canWriteMetadata = can("POST", ASSET_METADATA_ROUTE) || can("PUT", ASSET_METADATA_ROUTE);
    const unarchiveDisabledReason = routeDisabledReason(
        permissionsLoading,
        permissionsFailed,
        can("PUT", ASSET_UNARCHIVE_ROUTE),
        `Your role does not allow unarchiving this ${Synonyms.asset}.`
    );
    const permanentDeleteDisabledReason = routeDisabledReason(
        permissionsLoading,
        permissionsFailed,
        can("DELETE", ASSET_PERMANENT_DELETE_ROUTE),
        `Your role does not allow permanently deleting this ${Synonyms.asset}.`
    );

    // Fetch asset data. In-app navigation to another asset keeps this page mounted, so the previous
    // asset's details and error are cleared first and an answer for an asset already left is
    // dropped.
    useEffect(() => {
        let cancelled = false;
        setAsset({});
        setShowApiError(false);
        setApiError(null);
        setSelectedVersionId(new URLSearchParams(location.search).get("assetVersionId") || null);

        const fetchAssetData = async (): Promise<void> => {
            if (!databaseId || !assetId) return;

            try {
                const [, item, status] = await fetchAssetWithStatus({
                    databaseId,
                    assetId,
                    showArchived: true,
                });
                if (cancelled) return;

                if (item && typeof item === "object") {
                    setAsset(item);
                    dispatch({
                        type: "SET_ASSET_DETAIL",
                        payload: {
                            isMultiFile: item.isMultiFile,
                            assetId: assetId,
                            assetName: item.assetName,
                            databaseId: databaseId,
                            description: item.description,
                            key: item.key || (item.assetLocation && item.assetLocation["Key"]),
                            assetLocation: {
                                Key: item.key || (item.assetLocation && item.assetLocation["Key"]),
                            },
                            assetType: item.assetType,
                            isDistributable: item.isDistributable,
                            Asset: [],
                        },
                    });
                } else if (status === 403) {
                    setApiError(
                        `Access denied. Your role does not allow you to view this ${Synonyms.asset}.`
                    );
                    setShowApiError(true);
                    setApiErrorType("error");
                } else if (typeof item === "string" && item.includes("not found")) {
                    setApiError(
                        `${Synonyms.Asset} not found. The requested ${Synonyms.asset} may have been deleted or you may not have permission to access it.`
                    );
                    setShowApiError(true);
                    setApiErrorType("error");
                } else if (status === undefined && typeof item === "string" && item) {
                    // A request rejected without a status keeps its own message: a block by the
                    // AWS WAF web ACL names the request ID an administrator looks up, and a
                    // request whose response never arrived says the service could not be reached.
                    setApiError(`Failed to load ${Synonyms.asset} data. ${item}`);
                    setShowApiError(true);
                    setApiErrorType("error");
                } else {
                    // Handle other API failure cases
                    setApiError(
                        `Failed to load ${Synonyms.asset} data. The server returned an invalid response.`
                    );
                    setShowApiError(true);
                    setApiErrorType("error");
                    console.error("Invalid asset data returned:", item);
                }
            } catch (error) {
                if (cancelled) return;
                console.error("Error fetching asset:", error);
                setApiError(`Failed to load ${Synonyms.asset} data. Please try again later.`);
                setShowApiError(true);
                setApiErrorType("error");
            }
        };

        fetchAssetData();
        return () => {
            cancelled = true;
        };
        // location.search seeds the version selection only when the asset changes; a later query
        // change (version or file selection) must not refetch the asset.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [databaseId, assetId]);

    // Reload the asset after a change on the Versions tab, so the header's current version follows
    // it. The details stay on screen while the request runs, and a failed reload leaves them as
    // they are.
    useEffect(() => {
        if (versionsRevision === 0 || !databaseId || !assetId) return;
        let cancelled = false;
        const assetKey = `${databaseId}/${assetId}`;
        fetchAssetWithStatus({ databaseId, assetId, showArchived: true })
            .then(([, item]) => {
                // Drop an answer for an asset the user has already left: versionsAssetKey names
                // the asset now shown (the versions effect below updates it on navigation).
                if (cancelled || versionsAssetKey.current !== assetKey) return;
                if (item && typeof item === "object") setAsset(item);
            })
            .catch((error) => console.log("Failed to reload asset:", error));
        return () => {
            cancelled = true;
        };
        // Runs once per change on the Versions tab; the asset effect above handles a new asset.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [versionsRevision]);

    // Fetch asset versions for the version dropdown, once the asset is known so an archived
    // asset's versions are asked for with showArchived. The list is cleared for another asset and
    // kept while it reloads for the same one.
    useEffect(() => {
        let cancelled = false;
        const assetKey = `${databaseId}/${assetId}`;
        if (versionsAssetKey.current !== assetKey) {
            versionsAssetKey.current = assetKey;
            setVersions([]);
        }
        const loadVersions = async () => {
            if (!databaseId || !assetId || !assetLoaded) return;
            setVersionsLoading(true);
            try {
                const [success, result] = await fetchAllAssetVersions({
                    databaseId,
                    assetId,
                    showArchived: isArchived,
                });
                if (cancelled) return;
                if (success && result?.versions) {
                    // Sort versions by DateModified (newest first)
                    const sorted = [...result.versions].sort((a: any, b: any) => {
                        const dateA = new Date(a.DateModified || a.dateModified || 0).getTime();
                        const dateB = new Date(b.DateModified || b.dateModified || 0).getTime();
                        return dateB - dateA;
                    });
                    setVersions(sorted);
                }
            } catch (error) {
                console.log("Failed to load asset versions:", error);
            } finally {
                if (!cancelled) setVersionsLoading(false);
            }
        };
        loadVersions();
        return () => {
            cancelled = true;
        };
    }, [databaseId, assetId, assetLoaded, isArchived, versionsRevision]);

    const handleVersionsChanged = useCallback(() => setVersionsRevision((n) => n + 1), []);

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

    // Keep the URL `?filePath=` query param in lockstep with the file
    // manager's selection so a refresh / share / new-tab open always
    // reproduces the user's current view. Includes the asset root ("/"),
    // since selecting the root folder is a meaningful navigation state
    // for the file manager and should also survive a refresh.
    //
    // We use replace:true to avoid spamming browser history with one
    // entry per click, and we no-op if the path is unchanged so React's
    // strict-mode double-effects don't trigger redundant navigations.
    const handleSelectedPathChange = useCallback(
        (path: string | null) => {
            const params = new URLSearchParams(location.search);
            const current = params.get("filePath");
            if (path === null) {
                if (current === null) return;
                params.delete("filePath");
            } else {
                if (current === path) return;
                params.set("filePath", path);
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

    return (
        <AssetDetailContext.Provider value={{ state, dispatch }}>
            <ArchivedAssetContext.Provider value={isArchived}>
                <StatusMessageProvider>
                    <Box padding={{ top: "xs", horizontal: "l" }}>
                        <SpaceBetween direction="vertical" size="xs">
                            {/* Breadcrumbs */}
                            <BreadcrumbGroup
                                items={[
                                    { text: Synonyms.Databases, href: "#/databases/" },
                                    {
                                        text: "Search",
                                        href: "#/assets/",
                                    },
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
                                    dismissAriaLabel="Dismiss error"
                                    onDismiss={() => setShowApiError(false)}
                                >
                                    {apiError}
                                </Alert>
                            )}

                            {/* Asset Header - only shown on error, otherwise asset name is in the details
                                container. It stays after the alert is dismissed so the page is not left empty. */}
                            {apiError && (
                                <Header variant="h1">{`${Synonyms.Asset} Information Unavailable`}</Header>
                            )}
                        </SpaceBetween>

                        {/* Content with more spacing between containers */}
                        <div style={{ marginTop: "12px" }} />
                        <SpaceBetween direction="vertical" size="xs">
                            {/* The asset's details, files, metadata and map mount only once the asset
                                has loaded, so a refused or missing asset never starts them and each
                                panel knows whether to read it as archived */}
                            {!apiError && !assetLoaded && (
                                <StatusIndicator type="loading">
                                    {`Loading ${Synonyms.asset}...`}
                                </StatusIndicator>
                            )}
                            {!showApiError && assetLoaded && (
                                <>
                                    {/* Asset Details Pane */}
                                    <AssetDetailsPane
                                        asset={asset}
                                        databaseId={databaseId || ""}
                                        onOpenUpdateAsset={handleOpenUpdateAsset}
                                        onOpenDeleteModal={handleOpenDeleteModal}
                                        deleteDisabledReason={
                                            isArchived
                                                ? permanentDeleteDisabledReason
                                                : deleteDisabledReason
                                        }
                                        isArchived={isArchived}
                                        onOpenUnarchiveModal={() => setShowUnarchiveModal(true)}
                                        unarchiveDisabledReason={unarchiveDisabledReason}
                                        versions={versions}
                                        versionsLoading={versionsLoading}
                                        selectedVersionId={selectedVersionId}
                                        onVersionChange={handleVersionChange}
                                    />

                                    {/* Tabbed Container */}
                                    <TabbedContainer
                                        assetName={asset?.assetName || ""}
                                        assetId={assetId || ""}
                                        databaseId={databaseId || ""}
                                        filePathToNavigate={filePathToNavigate}
                                        assetVersionId={selectedVersionId || undefined}
                                        onSelectedPathChange={handleSelectedPathChange}
                                        requestedTabId={requestedTabId}
                                        onTabChange={handleTabChange}
                                        onVersionsChanged={handleVersionsChanged}
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
                                                readOnly={
                                                    !!selectedVersionId ||
                                                    !canWriteMetadata ||
                                                    isArchived
                                                }
                                            />
                                        )}
                                    </ErrorBoundary>
                                </>
                            )}
                        </SpaceBetween>
                        <div style={{ paddingBottom: "20px" }} />
                    </Box>

                    {/* Modals */}
                    {asset && !isArchived && (
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

                    <AssetDeleteModal
                        visible={showDeleteModal}
                        onDismiss={() => setShowDeleteModal(false)}
                        mode="asset"
                        selectedAssets={asset ? [asset] : []}
                        databaseId={databaseId}
                        forceDeleteMode={isArchived}
                        onSuccess={(operation) => {
                            setShowDeleteModal(false);
                            // Navigate back to search page after successful deletion / archival
                            navigate(databaseId ? `/databases/${databaseId}/assets` : "/assets");
                        }}
                    />
                    {isArchived && (
                        <AssetUnarchiveModal
                            visible={showUnarchiveModal}
                            onDismiss={() => setShowUnarchiveModal(false)}
                            selectedAsset={asset}
                            databaseId={databaseId}
                            onSuccess={() => {
                                setShowUnarchiveModal(false);
                                window.location.reload();
                            }}
                        />
                    )}
                </StatusMessageProvider>
            </ArchivedAssetContext.Provider>
        </AssetDetailContext.Provider>
    );
}
