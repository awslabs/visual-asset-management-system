/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { archiveFile } from "../../services/FileOperationsService";
import {
    Alert,
    Box,
    BreadcrumbGroup,
    Button,
    Container,
    Grid,
    Header,
    Link,
    Modal,
    SegmentedControl,
    SpaceBetween,
    Spinner,
} from "@cloudscape-design/components";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { useLocation, useNavigate, useParams } from "react-router";

import FileMetadata from "../metadata/FileMetadata";
import { fetchAsset, fetchFileInfo } from "../../services/APIService";
import { FileVersionsTable } from "../filemanager/components/FileVersionsTable";
// File format constants no longer needed - handled by plugin system
import DynamicViewer from "../../visualizerPlugin/components/DynamicViewer";
import {
    buildSingleViewerEntry,
    OLDER_VERSION_PREVIEW_NOTICE,
    previewIsOfLatestVersionOnly,
} from "./singleViewerEntry";

import Synonyms from "../../synonyms";
import { usePageTitle } from "../../hooks/usePageTitle";
import "./ViewFile.css";

// TypeScript interfaces
interface FileInfo {
    filename: string;
    key: string;
    isDirectory: boolean;
    versionId?: string;
    size?: number;
    dateCreatedCurrentVersion?: string;
    isArchived?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}

interface ViewFileState {
    // Single file mode (existing functionality)
    filename?: string;
    key?: string;
    isDirectory?: boolean;
    versionId?: string;
    assetVersionId?: string;
    size?: number;
    dateCreatedCurrentVersion?: string;
    isArchived?: boolean;
    primaryType?: string | null;
    previewFile?: string;

    // Multi-file mode (new functionality)
    files?: FileInfo[];
}

interface ViewerOption {
    text: string;
    id: string;
}

interface Asset {
    assetId?: string;
    databaseId?: string;
    assetName?: string;
    previewLocation?: {
        Key: string;
    };
    assetLocation?: {
        Key: string;
    };
    generated_artifacts?: {
        preview?: {
            Key: string;
        };
    };
    isDistributable?: boolean;
    status?: string;
}

// File format detection now handled by plugin system - no longer needed

// React Router keeps this route's element mounted when only :databaseId or :assetId change, so a link
// to another asset's file would re-render the previous asset's page with its loaded file still in
// state. Keying on the asset gives each asset a fresh page instance.
export default function ViewFile() {
    const { databaseId, assetId } = useParams<{
        databaseId: string;
        assetId: string;
    }>();
    return <ViewFileForAsset key={`${databaseId}/${assetId}`} />;
}

function ViewFileForAsset() {
    const location = useLocation();
    const navigate = useNavigate();
    const { state } = location as { state: ViewFileState };
    const { databaseId, assetId } = useParams<{
        databaseId: string;
        assetId: string;
    }>();

    // This view requires an asset to render. If no asset context is available
    // (e.g. the route was reached without an assetId), redirect to the assets list.
    useEffect(() => {
        if (!assetId) {
            navigate("/assets");
        }
    }, [assetId, navigate]);

    // Extract file path from URL for direct path access
    const getFilePathFromUrl = (): string | null => {
        // First try to match /file/PATH format
        const filePathParts = location.pathname.split("/file/");
        if (filePathParts.length > 1 && filePathParts[1]) {
            return decodeURIComponent(filePathParts[1]);
        }

        // If not found, try direct /databases/:databaseId/assets/:assetId/PATH format
        // Match pattern: /databases/{databaseId}/assets/{assetId}/{filePath}
        const pathMatch = location.pathname.match(/^\/databases\/[^\/]+\/assets\/[^\/]+\/(.+)$/);
        if (pathMatch && pathMatch[1]) {
            // Exclude known routes that shouldn't be treated as file paths
            const excludedPaths = ["download", "uploads", "file"];
            const firstSegment = pathMatch[1].split("/")[0];
            if (!excludedPaths.includes(firstSegment)) {
                return decodeURIComponent(pathMatch[1]);
            }
        }

        return null;
    };

    // Extract version from query parameters
    const getVersionFromQuery = (): string | null => {
        const searchParams = new URLSearchParams(location.search);
        return searchParams.get("version");
    };

    // Extract asset version from query parameters
    const getAssetVersionFromQuery = (): string | null => {
        const searchParams = new URLSearchParams(location.search);
        return searchParams.get("assetVersion");
    };

    const urlFilePath = getFilePathFromUrl();
    const urlVersion = getVersionFromQuery();
    const urlAssetVersion = getAssetVersionFromQuery();
    const isDirectPathAccess = urlFilePath && !state;
    // The URL file path and version that a direct-path load answers.
    const directPathRequest = `${urlFilePath ?? ""}?${urlVersion ?? ""}`;

    // State for direct path loading
    const [isLoadingDirectPath, setIsLoadingDirectPath] = useState(isDirectPathAccess);
    const [directPathError, setDirectPathError] = useState<string | null>(null);
    const [loadedFileInfo, setLoadedFileInfo] = useState<FileInfo | null>(null);
    // The direct-path requests that loadedFileInfo answers: the one it was loaded for and, when that
    // named no version, the same path with the version that was picked, until the URL names that
    // version. Until one equals directPathRequest, the loaded file is the one the URL named before.
    const [loadedFileRequests, setLoadedFileRequests] = useState<string[]>([]);
    // The navigation-state file whose version lookup has started. Each open of such a file asks
    // once: a failed or empty answer is not retried, and the effect running again does not repeat it.
    const versionLookupKey = useRef<string | null>(null);

    // Determine if we're in multi-file mode
    const isMultiFileMode = state?.files && state.files.length > 1;
    const currentFiles = isMultiFileMode ? state.files! : [];

    // For single file mode, use existing logic or loaded file info
    // Priority:
    // 1. state with versionId (from navigation with full data) - use state directly
    // 2. state without versionId but loadedFileInfo has versionId (version was fetched on-demand) - use loadedFileInfo
    // 3. state without versionId and no loadedFileInfo - use state (will trigger version fetch)
    // 4. loadedFileInfo (from direct URL access) - use loadedFileInfo
    // 5. fallback empty object
    const singleFileInfo = isMultiFileMode
        ? null
        : state?.key && state?.versionId
        ? {
              // State has full data including versionId - use it directly
              filename: state?.filename || "",
              key: state?.key || "",
              isDirectory: state?.isDirectory || false,
              versionId: state?.versionId,
              size: state?.size,
              dateCreatedCurrentVersion: state?.dateCreatedCurrentVersion,
              isArchived: state?.isArchived,
              primaryType: state?.primaryType,
              previewFile: state?.previewFile,
          }
        : state?.key &&
          !state?.versionId &&
          loadedFileInfo?.versionId &&
          loadedFileInfo.key === state.key
        ? // State has key but no versionId, and we've fetched version info for that file - use loadedFileInfo
          loadedFileInfo
        : state?.key
        ? {
              // State has key but no versionId yet (version fetch in progress or not started)
              filename: state?.filename || "",
              key: state?.key || "",
              isDirectory: state?.isDirectory || false,
              versionId: undefined, // Will be populated by version fetch
              size: state?.size,
              dateCreatedCurrentVersion: state?.dateCreatedCurrentVersion,
              isArchived: state?.isArchived,
              primaryType: state?.primaryType,
              previewFile: state?.previewFile,
          }
        : loadedFileInfo
        ? loadedFileInfo
        : {
              filename: "",
              key: "",
              isDirectory: false,
              versionId: undefined,
              size: undefined,
              dateCreatedCurrentVersion: undefined,
              isArchived: undefined,
              primaryType: undefined,
              previewFile: undefined,
          };

    // Compute effective asset version ID from URL query param or navigation state
    const effectiveAssetVersionId = urlAssetVersion || state?.assetVersionId || undefined;

    // Check if any files are archived
    const hasArchivedFiles = isMultiFileMode
        ? currentFiles.some((file) => file.isArchived)
        : singleFileInfo?.isArchived === true;

    const [reload, setReload] = useState(true);
    const [viewType, setViewType] = useState<string | null>(null);

    // The file's latest version, so the Preview tab can say when its preview belongs to a different
    // (the latest) version than the one being viewed. The direct-path load already reads the file's
    // versions, so its answer is reused; only a page opened from the file manager's state (no direct
    // load) asks once, and only while the Preview tab is showing.
    const [loadedLatest, setLoadedLatest] = useState<{ key: string; versionId: string } | null>(
        null
    );
    const [lookedUpLatest, setLookedUpLatest] = useState<{
        key: string;
        versionId?: string;
    } | null>(null);
    const previewKeyForLookup = !isMultiFileMode ? singleFileInfo?.key : undefined;
    const knownLatestVersionId =
        loadedLatest && loadedLatest.key === previewKeyForLookup
            ? loadedLatest.versionId
            : lookedUpLatest && lookedUpLatest.key === previewKeyForLookup
            ? lookedUpLatest.versionId
            : undefined;
    const lookupSettled =
        !!knownLatestVersionId || (!!lookedUpLatest && lookedUpLatest.key === previewKeyForLookup);
    const lookupLatestVersion =
        viewType === "preview" &&
        !!previewKeyForLookup &&
        !!singleFileInfo?.previewFile &&
        !!singleFileInfo?.versionId &&
        !effectiveAssetVersionId &&
        !knownLatestVersionId &&
        !lookupSettled &&
        !isLoadingDirectPath;
    useEffect(() => {
        if (!lookupLatestVersion || !databaseId || !assetId || !previewKeyForLookup) return;
        let cancelled = false;
        (async () => {
            try {
                const [success, response] = await fetchFileInfo({
                    databaseId,
                    assetId,
                    fileKey: previewKeyForLookup,
                    includeVersions: true,
                });
                if (cancelled) return;
                if (!success) {
                    setLookedUpLatest({ key: previewKeyForLookup });
                    return;
                }
                const info = typeof response === "string" ? JSON.parse(response) : response;
                const latest = info?.versions?.find((v: any) => v.isLatest);
                setLookedUpLatest({
                    key: previewKeyForLookup,
                    versionId: latest?.versionId || info?.versionId || undefined,
                });
            } catch (error) {
                console.error("Could not read the latest file version:", error);
                if (!cancelled) setLookedUpLatest({ key: previewKeyForLookup });
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [lookupLatestVersion, previewKeyForLookup, databaseId, assetId]);
    const showOlderVersionPreviewNotice =
        !isMultiFileMode &&
        !!singleFileInfo &&
        previewIsOfLatestVersionOnly(singleFileInfo, viewType, effectiveAssetVersionId, {
            latestVersionId: knownLatestVersionId,
            settled: lookupSettled,
            versionInUrl: !!urlVersion,
        });
    const [asset, setAsset] = useState<Asset>({});
    // The file and preview streaming APIs refuse a non-distributable asset, so the visualizer and its
    // view selector are left out entirely and the page shows only versions and metadata. This page is
    // normally reached from the file manager, which hides its own entry points for such an asset; a
    // direct link is still allowed to land here. A fetched record without the flag is not
    // distributable, as the API treats it; the empty record held before the fetch resolves (no
    // assetId) still renders the viewer.
    const isNotDistributable = !!asset?.assetId && asset.isDistributable !== true;

    usePageTitle(
        databaseId,
        asset?.assetName,
        isMultiFileMode ? `${currentFiles.length} Files` : singleFileInfo?.filename
    );

    // Set once the asset's details have been read (the visualizer and its view selector wait for them).
    const [assetDetailsLoaded, setAssetDetailsLoaded] = useState(false);
    // The view choices follow the file the page holds, not the moment the asset details arrived: the
    // file can be read before or after them, and a link to another version of the file carries the
    // preview file only when the page that navigated knew it.
    const defaultViewType = isMultiFileMode ? "files" : "file";
    const viewerOptions = useMemo<ViewerOption[]>(() => {
        if (!assetDetailsLoaded) return [];
        if (isMultiFileMode) return [{ text: "Files", id: "files" }];
        const options: ViewerOption[] = [];
        if (singleFileInfo?.previewFile) options.push({ text: "Preview", id: "preview" });
        options.push({ text: "File", id: "file" });
        return options;
    }, [assetDetailsLoaded, isMultiFileMode, singleFileInfo?.previewFile]);
    // A choice the options no longer offer (the file changed to one without a preview) falls back to
    // the default; a choice the user made and the options still offer is never changed.
    useEffect(() => {
        if (viewerOptions.length > 0 && viewType && !viewerOptions.some((o) => o.id === viewType)) {
            setViewType(defaultViewType);
        }
    }, [viewerOptions, viewType, defaultViewType]);
    const [viewerMode, setViewerMode] = useState("collapse");
    const [showDeletePreviewModal, setShowDeletePreviewModal] = useState(false);
    const [isPreviewDeleting, setIsPreviewDeleting] = useState(false);

    const changeViewerMode = (mode: string) => {
        if (mode === "fullscreen" && viewerMode === "fullscreen") {
            mode = "collapse";
        }
        setViewerMode(mode);
    };

    useEffect(() => {
        if (assetId && !isMultiFileMode && !singleFileInfo?.isDirectory) {
            const fullscreenChangeHandler = (event: Event) => {
                if (!document.fullscreenElement) {
                    if (viewerMode === "fullscreen") {
                        setViewerMode("collapse");
                    }
                }
            };
            const element = document.querySelector(
                "#view-edit-asset-right-column .visualizer-container"
            ) as HTMLElement;

            if (element) {
                element.removeEventListener("fullscreenchange", fullscreenChangeHandler);

                if (
                    document.fullscreenElement ||
                    (document as any).webkitFullscreenElement ||
                    (document as any).mozFullScreenElement ||
                    (document as any).msFullscreenElement
                ) {
                    if (document.exitFullscreen) {
                        document.exitFullscreen();
                    } else if ((document as any).mozCancelFullScreen) {
                        (document as any).mozCancelFullScreen();
                    } else if ((document as any).webkitExitFullscreen) {
                        (document as any).webkitExitFullscreen();
                    } else if ((document as any).msExitFullscreen) {
                        (document as any).msExitFullscreen();
                    }
                } else if (viewerMode === "fullscreen") {
                    if (element.requestFullscreen) {
                        element.requestFullscreen();
                    } else if ((element as any).mozRequestFullScreen) {
                        (element as any).mozRequestFullScreen();
                    } else if ((element as any).webkitRequestFullscreen) {
                        (element as any).webkitRequestFullscreen(
                            (Element as any).ALLOW_KEYBOARD_INPUT
                        );
                    } else if ((element as any).msRequestFullscreen) {
                        (element as any).msRequestFullscreen();
                    }
                }
                element.addEventListener("fullscreenchange", fullscreenChangeHandler);
                return () => {
                    element.removeEventListener("fullscreenchange", fullscreenChangeHandler);
                };
            }
        }
    }, [assetId, isMultiFileMode, singleFileInfo?.isDirectory, viewerMode]);

    const changeViewType = (event: any) => {
        setViewType(event.detail.selectedId);
    };

    // Handle direct path access - fetch file info from URL
    useEffect(() => {
        // A response for a URL the page has since left is dropped, so it cannot replace the file
        // the URL names now.
        let cancelled = false;
        const loadFileFromDirectPath = async () => {
            if (!isDirectPathAccess || !databaseId || !assetId || !urlFilePath) {
                setIsLoadingDirectPath(false);
                return;
            }

            // The file in hand already answers this URL: the URL gained the version picked for it,
            // or the effect re-ran after the load wrote that file. Looking it up again would show the
            // spinner a second time.
            if (loadedFileInfo && loadedFileRequests.includes(directPathRequest)) {
                // Once the URL names the picked version, the request that named no version no longer
                // stands for the file in hand: a link that names no version asks for the latest one.
                if (loadedFileRequests.length > 1 && loadedFileRequests[0] !== directPathRequest) {
                    setLoadedFileRequests([directPathRequest]);
                }
                setDirectPathError(null);
                setIsLoadingDirectPath(false);
                return;
            }

            setIsLoadingDirectPath(true);
            setDirectPathError(null);

            try {
                console.log("Loading file from direct path:", urlFilePath);
                console.log("Requested version:", urlVersion || "latest");

                // Fetch file info
                const [success, fileInfoResponse] = await fetchFileInfo({
                    databaseId,
                    assetId,
                    fileKey: urlFilePath,
                    includeVersions: true,
                });
                if (cancelled) {
                    return;
                }

                if (!success) {
                    setDirectPathError(
                        typeof fileInfoResponse === "string"
                            ? fileInfoResponse
                            : "The file you're looking for doesn't exist or has been moved."
                    );
                    setIsLoadingDirectPath(false);
                    return;
                }

                // Parse file info response
                let fileInfo: any;
                if (typeof fileInfoResponse === "string") {
                    try {
                        fileInfo = JSON.parse(fileInfoResponse);
                    } catch {
                        fileInfo = fileInfoResponse;
                    }
                } else {
                    fileInfo = fileInfoResponse;
                }

                // Check if file exists
                if (!fileInfo || !fileInfo.key) {
                    setDirectPathError(
                        "The file you're looking for doesn't exist or has been moved."
                    );
                    setIsLoadingDirectPath(false);
                    return;
                }

                // Check if file is archived
                if (fileInfo.isArchived) {
                    setDirectPathError(
                        `This file is part of an archived ${Synonyms.asset} and cannot be viewed.`
                    );
                    setIsLoadingDirectPath(false);
                    return;
                }

                // Get the filename - use fileName field from API or extract from key
                const filename = fileInfo.fileName || fileInfo.key.split("/").pop() || "";

                // Get the latest version from versions array or use requested version
                let versionToUse: string | undefined = urlVersion || undefined;
                if (!versionToUse && fileInfo.versions && fileInfo.versions.length > 0) {
                    // Find the latest version
                    const latestVersion = fileInfo.versions.find((v: any) => v.isLatest);
                    versionToUse =
                        latestVersion?.versionId || fileInfo.versions[0]?.versionId || undefined;
                }

                // Get lastModified date
                const lastModified =
                    fileInfo.lastModified ||
                    (fileInfo.versions && fileInfo.versions.length > 0
                        ? fileInfo.versions[0].lastModified
                        : undefined);

                // Create file info object with correct field mappings
                const loadedFile: FileInfo = {
                    filename: filename,
                    key: fileInfo.key,
                    isDirectory: fileInfo.isFolder || false,
                    versionId: versionToUse,
                    size: fileInfo.size,
                    dateCreatedCurrentVersion: lastModified,
                    isArchived: fileInfo.isArchived || false,
                    primaryType: fileInfo.primaryType || null,
                    previewFile: fileInfo.previewFile,
                };

                const latestEntry = Array.isArray(fileInfo.versions)
                    ? fileInfo.versions.find((v: any) => v.isLatest)
                    : undefined;
                setLoadedLatest(
                    latestEntry?.versionId
                        ? { key: fileInfo.key, versionId: latestEntry.versionId }
                        : null
                );

                console.log("Loaded file info:", loadedFile);
                setLoadedFileInfo(loadedFile);
                setLoadedFileRequests(
                    Array.from(new Set([directPathRequest, `${urlFilePath}?${versionToUse ?? ""}`]))
                );
                setIsLoadingDirectPath(false);
                setReload(true); // Trigger asset data fetch
            } catch (error: any) {
                if (cancelled) {
                    return;
                }
                console.error("Error loading file from direct path:", error);

                // Check for specific error types. The apiClient throws an ApiError
                // carrying the HTTP status in `.status` and the backend message in `.message`.
                if (error?.status === 403) {
                    setDirectPathError("You don't have permission to access this file.");
                } else if (error?.status === 404) {
                    setDirectPathError(
                        "The file you're looking for doesn't exist or has been moved."
                    );
                } else {
                    setDirectPathError(
                        error?.message || "An error occurred while loading the file."
                    );
                }
                setIsLoadingDirectPath(false);
            }
        };

        loadFileFromDirectPath();
        return () => {
            cancelled = true;
        };
    }, [
        isDirectPathAccess,
        databaseId,
        assetId,
        urlFilePath,
        urlVersion,
        directPathRequest,
        loadedFileInfo,
        loadedFileRequests,
    ]);

    // Fetch version info when state is passed but versionId is missing
    useEffect(() => {
        // The file to look up: one the navigation state names (navigated from the file manager)
        // without a versionId (the file manager hadn't loaded full data yet), in single-file mode.
        const lookupKey =
            state?.key && !state.versionId && !effectiveAssetVersionId && !isMultiFileMode
                ? state.key
                : null;
        if (!lookupKey) {
            // The page no longer holds such a file, so the next one opened is looked up afresh.
            versionLookupKey.current = null;
            return;
        }
        if (
            isLoadingDirectPath ||
            !databaseId ||
            !assetId ||
            versionLookupKey.current === lookupKey
        ) {
            return;
        }
        versionLookupKey.current = lookupKey;
        // An answer that arrives after the page has moved to another file is dropped, so it cannot
        // replace the file the page shows now.
        let cancelled = false;
        let settled = false;

        const loadVersionInfo = async () => {
            console.log("State passed without versionId, fetching version info for:", lookupKey);

            try {
                const [success, fileInfoResponse] = await fetchFileInfo({
                    databaseId,
                    assetId,
                    fileKey: lookupKey,
                    includeVersions: true,
                });
                if (cancelled) {
                    return;
                }

                if (success && fileInfoResponse) {
                    let fileInfo: any;
                    if (typeof fileInfoResponse === "string") {
                        try {
                            fileInfo = JSON.parse(fileInfoResponse);
                        } catch {
                            fileInfo = fileInfoResponse;
                        }
                    } else {
                        fileInfo = fileInfoResponse;
                    }

                    // Get the latest version from versions array
                    let latestVersionId: string | undefined;
                    if (fileInfo.versions && fileInfo.versions.length > 0) {
                        const latestVersion = fileInfo.versions.find((v: any) => v.isLatest);
                        latestVersionId =
                            latestVersion?.versionId || fileInfo.versions[0]?.versionId;
                    } else if (fileInfo.versionId) {
                        latestVersionId = fileInfo.versionId;
                    }

                    if (latestVersionId) {
                        console.log("Found latest version:", latestVersionId);
                        const latestEntry = Array.isArray(fileInfo.versions)
                            ? fileInfo.versions.find((v: any) => v.isLatest)
                            : undefined;
                        setLoadedLatest(
                            latestEntry?.versionId
                                ? { key: lookupKey, versionId: latestEntry.versionId }
                                : null
                        );
                        // Update loadedFileInfo with the version info
                        // This will be used by singleFileInfo since state doesn't have versionId
                        setLoadedFileInfo({
                            filename: state.filename || fileInfo.fileName || "",
                            key: lookupKey,
                            isDirectory: state.isDirectory || false,
                            versionId: latestVersionId,
                            size: state.size || fileInfo.size,
                            dateCreatedCurrentVersion:
                                state.dateCreatedCurrentVersion || fileInfo.lastModified,
                            isArchived: state.isArchived || fileInfo.isArchived,
                            primaryType: state.primaryType || fileInfo.primaryType,
                            previewFile: state.previewFile || fileInfo.previewFile,
                        });
                    }
                }
            } catch (error) {
                if (!cancelled) {
                    console.error("Error fetching version info:", error);
                }
            } finally {
                settled = true;
            }
        };

        loadVersionInfo();
        return () => {
            cancelled = true;
            // A lookup cut off before it answered runs again if the page still holds the same file
            // (React runs each effect twice in development).
            if (!settled && versionLookupKey.current === lookupKey) {
                versionLookupKey.current = null;
            }
        };
        // `state` is read only for the file the lookup is keyed on; its other fields fill in the answer.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        state?.key,
        state?.versionId,
        effectiveAssetVersionId,
        isMultiFileMode,
        isLoadingDirectPath,
        databaseId,
        assetId,
    ]);

    useEffect(() => {
        const getData = async () => {
            if (databaseId && assetId) {
                console.log("Fetching asset details for:", assetId);
                const item = await fetchAsset({
                    databaseId: databaseId,
                    assetId: assetId,
                    showArchived: true,
                });
                if (item !== false) {
                    console.log("Asset details fetched:", item);
                    setAsset(item);

                    setAssetDetailsLoaded(true);
                    setViewType(isMultiFileMode ? "files" : "file");
                    setReload(false);
                }
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, assetId, databaseId]);

    // Update URL when version changes to keep URL copy/paste accurate
    useEffect(() => {
        // Skip URL sync when using assetVersionId — the URL already has ?assetVersion=X
        if (effectiveAssetVersionId) {
            return;
        }

        // Only update URL for single file mode with a valid file key and version
        if (isMultiFileMode || !singleFileInfo?.key || !singleFileInfo?.versionId) {
            return;
        }

        // Don't update during initial loading
        if (isLoadingDirectPath) {
            return;
        }

        // The file in hand is the one the URL named before until the direct-path load answers the
        // current URL; writing it into the URL would send the URL, and the next load, back to it.
        if (isDirectPathAccess && !loadedFileRequests.includes(directPathRequest)) {
            return;
        }

        // Get the relative path from the file key
        // The key format is typically: assetId/relativePath
        // We need to extract just the relativePath part
        const keyParts = singleFileInfo.key.split("/");
        // Remove the first part (assetId) if it matches
        let relativePath = singleFileInfo.key;
        if (keyParts.length > 1 && keyParts[0] === assetId) {
            relativePath = keyParts.slice(1).join("/");
        }

        // Encode the path for URL
        const encodedPath = encodeURIComponent(relativePath);

        // Build the new URL with version query parameter
        const newUrl = `/databases/${databaseId}/assets/${encodeURIComponent(
            String(assetId)
        )}/file/${encodedPath}?version=${encodeURIComponent(singleFileInfo.versionId)}`;

        // Get current URL path and query
        const currentPath = location.pathname;

        // Only update if the URL has changed (to avoid infinite loops)
        // Compare the expected URL structure
        const expectedPathBase = `/databases/${databaseId}/assets/${encodeURIComponent(
            String(assetId)
        )}/file/`;
        if (
            currentPath.startsWith(expectedPathBase) ||
            currentPath ===
                `/databases/${databaseId}/assets/${encodeURIComponent(String(assetId))}/file`
        ) {
            const currentVersion = getVersionFromQuery();
            if (currentVersion !== singleFileInfo.versionId) {
                // Update URL without adding to history (replace). The navigation state is kept, so
                // the file the page already holds is not looked up again by its direct path.
                navigate(newUrl, { replace: true, state: location.state });
            }
        }
    }, [
        effectiveAssetVersionId,
        singleFileInfo?.versionId,
        singleFileInfo?.key,
        databaseId,
        assetId,
        isMultiFileMode,
        isLoadingDirectPath,
        isDirectPathAccess,
        loadedFileRequests,
        directPathRequest,
        navigate,
        location.pathname,
        location.search,
        location.state,
    ]);

    // Generate breadcrumb text
    const getBreadcrumbText = (): string => {
        if (isMultiFileMode) {
            return `view ${currentFiles.length} files`;
        }
        return `view ${singleFileInfo?.filename || ""}`;
    };

    // Generate header text
    const getHeaderText = (): string => {
        if (isMultiFileMode) {
            return `${asset?.assetName || Synonyms.Asset} - Multiple Files`;
        }

        const filename = singleFileInfo?.filename || asset?.assetName || "";
        const primaryType = singleFileInfo?.primaryType;

        if (primaryType && primaryType.trim() !== "") {
            return `${filename} (${primaryType})`;
        }

        return filename;
    };

    // Handle version revert - refresh the page to show updated file
    const handleVersionRevert = () => {
        // Refresh the page to show the reverted file
        window.location.reload();
    };

    return (
        <div>
            {assetId && (
                <>
                    {/* Show loading state for direct path access */}
                    {isLoadingDirectPath && (
                        <Box padding={{ top: "s", horizontal: "l" }}>
                            <Container>
                                <Box padding="xxl" textAlign="center">
                                    <SpaceBetween direction="vertical" size="m">
                                        <Spinner size="large" />
                                        <div
                                            style={{
                                                color: "var(--vams-text-secondary)",
                                                fontSize: "16px",
                                            }}
                                        >
                                            Loading file...
                                        </div>
                                    </SpaceBetween>
                                </Box>
                            </Container>
                        </Box>
                    )}

                    {/* Show error state for direct path access */}
                    {!isLoadingDirectPath && directPathError && (
                        <Box padding={{ top: "s", horizontal: "l" }}>
                            <Container>
                                <Box padding="m">
                                    <Alert type="error" header="File Not Found">
                                        {directPathError}
                                        <br />
                                        <br />
                                        <Link
                                            href={`#/databases/${databaseId}/assets/${encodeURIComponent(
                                                assetId
                                            )}`}
                                        >
                                            {`Return to ${Synonyms.asset}`}
                                        </Link>
                                    </Alert>
                                </Box>
                            </Container>
                        </Box>
                    )}

                    {/* Show normal content when not loading and no error */}
                    {!isLoadingDirectPath && !directPathError && (
                        <Box padding={{ top: "xs", horizontal: "l" }}>
                            <SpaceBetween direction="vertical" size="xxs">
                                <BreadcrumbGroup
                                    items={[
                                        { text: Synonyms.Databases, href: "#/databases/" },
                                        {
                                            text: "Search",
                                            href: "#/assets/",
                                        },
                                        {
                                            text: databaseId || "",
                                            href: "#/databases/" + databaseId + "/assets/",
                                        },
                                        {
                                            text: asset?.assetName || "",
                                            href:
                                                "#/databases/" +
                                                databaseId +
                                                "/assets/" +
                                                encodeURIComponent(assetId),
                                        },
                                        { text: getBreadcrumbText(), href: "#" },
                                    ]}
                                    ariaLabel="Breadcrumbs"
                                />
                                <div>
                                    {/* Main title and File/Preview buttons on same row */}
                                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                        <Box>
                                            <h1 style={{ margin: "9px 0 0 0" }}>
                                                {getHeaderText()}{" "}
                                                {asset?.status === "archived" && (
                                                    <span
                                                        style={{
                                                            color: "var(--vams-text-secondary)",
                                                        }}
                                                    >
                                                        (Archived)
                                                    </span>
                                                )}
                                                {!isMultiFileMode && singleFileInfo?.versionId && (
                                                    <span
                                                        style={{
                                                            fontSize: "14px",
                                                            fontWeight: "normal",
                                                            color: "var(--vams-text-secondary)",
                                                            marginLeft: "8px",
                                                        }}
                                                    >
                                                        (Version: {singleFileInfo.versionId})
                                                    </span>
                                                )}
                                            </h1>
                                        </Box>
                                        <div
                                            style={{
                                                display: "flex",
                                                alignItems: "center",
                                                justifyContent: "flex-end",
                                            }}
                                        >
                                            {viewerOptions.length > 0 && !isNotDistributable && (
                                                <SegmentedControl
                                                    label="Visualizer Control"
                                                    options={viewerOptions}
                                                    selectedId={viewType}
                                                    onChange={changeViewType}
                                                    className="visualizer-segment-control"
                                                />
                                            )}
                                        </div>
                                    </Grid>

                                    {effectiveAssetVersionId && (
                                        <Box margin={{ top: "xxs" }}>
                                            <StatusIndicator type="info">
                                                {`${Synonyms.Asset} Version: v${effectiveAssetVersionId}`}
                                            </StatusIndicator>
                                        </Box>
                                    )}
                                </div>

                                {/* Main content area — vertical stack, no splitter */}
                                {(!isMultiFileMode ? !singleFileInfo?.isDirectory : true) &&
                                !hasArchivedFiles ? (
                                    <SpaceBetween direction="vertical" size="xs">
                                        {/* Viewer — omitted for a non-distributable asset */}
                                        {isNotDistributable ? (
                                            <Container>
                                                <Box
                                                    padding="m"
                                                    textAlign="center"
                                                    color="text-status-inactive"
                                                >
                                                    {`Visualizer is not available because this ${Synonyms.asset} is not marked as distributable.`}
                                                </Box>
                                            </Container>
                                        ) : (
                                            <>
                                                {showOlderVersionPreviewNotice && (
                                                    <Alert
                                                        type="info"
                                                        statusIconAriaLabel="Info"
                                                        data-testid="older-version-preview-notice"
                                                    >
                                                        {OLDER_VERSION_PREVIEW_NOTICE}
                                                    </Alert>
                                                )}
                                                <div
                                                    style={{
                                                        height: "65vh",
                                                        maxHeight: "700px",
                                                        minHeight: "100px",
                                                        position: "relative",
                                                        overflow: "hidden",
                                                    }}
                                                >
                                                    <div
                                                        id="view-edit-asset-right-column"
                                                        className={viewerMode}
                                                        style={{
                                                            height: "100%",
                                                            width: "100%",
                                                            position: "absolute",
                                                            top: 0,
                                                            left: 0,
                                                            overflow: "hidden",
                                                        }}
                                                    >
                                                        <DynamicViewer
                                                            key={`${viewType}-${assetId}-${
                                                                singleFileInfo?.key || "no-file"
                                                            }-${
                                                                effectiveAssetVersionId ||
                                                                singleFileInfo?.versionId ||
                                                                "no-version"
                                                            }`}
                                                            files={
                                                                isMultiFileMode ||
                                                                viewType === "files"
                                                                    ? currentFiles
                                                                    : singleFileInfo
                                                                    ? [
                                                                          buildSingleViewerEntry(
                                                                              singleFileInfo,
                                                                              viewType,
                                                                              effectiveAssetVersionId
                                                                          ),
                                                                      ]
                                                                    : []
                                                            }
                                                            assetId={assetId!}
                                                            databaseId={databaseId!}
                                                            assetVersionId={effectiveAssetVersionId}
                                                            viewerMode={viewerMode}
                                                            onViewerModeChange={changeViewerMode}
                                                            showViewerSelector={true}
                                                            isPreviewMode={viewType === "preview"}
                                                            onDeletePreview={undefined}
                                                            sizingMode="container"
                                                        />
                                                    </div>
                                                </div>
                                            </>
                                        )}

                                        {/* File list (multi-file mode only) */}
                                        {isMultiFileMode && (
                                            <Container
                                                header={
                                                    <Header variant="h3">Selected Files</Header>
                                                }
                                            >
                                                <SpaceBetween direction="vertical" size="xs">
                                                    {currentFiles.map(
                                                        (file: any, index: number) => (
                                                            <Box
                                                                key={index}
                                                                padding={{
                                                                    vertical: "xs",
                                                                    horizontal: "s",
                                                                }}
                                                            >
                                                                <span
                                                                    style={{
                                                                        fontFamily: "monospace",
                                                                    }}
                                                                >
                                                                    {file.filename}
                                                                    {file.primaryType &&
                                                                        file.primaryType.trim() !==
                                                                            "" && (
                                                                            <span
                                                                                style={{
                                                                                    color: "var(--vams-text-secondary)",
                                                                                    marginLeft:
                                                                                        "4px",
                                                                                }}
                                                                            >
                                                                                ({file.primaryType})
                                                                            </span>
                                                                        )}
                                                                    {file.versionId && (
                                                                        <span
                                                                            style={{
                                                                                color: "var(--vams-text-secondary)",
                                                                                marginLeft: "8px",
                                                                            }}
                                                                        >
                                                                            (Version:{" "}
                                                                            {file.versionId})
                                                                        </span>
                                                                    )}
                                                                </span>
                                                            </Box>
                                                        )
                                                    )}
                                                </SpaceBetween>
                                            </Container>
                                        )}

                                        {/* Metadata (single-file, non-archived only) */}
                                        {!isMultiFileMode && singleFileInfo?.key && (
                                            <FileMetadata
                                                databaseId={databaseId!}
                                                assetId={assetId!}
                                                prefix={singleFileInfo.key}
                                                showHeader={false}
                                                className="viewfile-metadata"
                                                assetVersionId={effectiveAssetVersionId}
                                            />
                                        )}

                                        {/* File Versions (single-file, non-directory, has version) */}
                                        {!isMultiFileMode &&
                                            singleFileInfo &&
                                            singleFileInfo.key &&
                                            !singleFileInfo.isDirectory &&
                                            (singleFileInfo.versionId ||
                                                effectiveAssetVersionId) && (
                                                <FileVersionsTable
                                                    databaseId={databaseId!}
                                                    assetId={assetId!}
                                                    filePath={singleFileInfo.key}
                                                    fileName={singleFileInfo.filename}
                                                    currentVersionId={singleFileInfo.versionId}
                                                    onVersionRevert={handleVersionRevert}
                                                    displayMode="container"
                                                    visible={true}
                                                    assetVersionId={effectiveAssetVersionId}
                                                    previewFile={singleFileInfo.previewFile}
                                                />
                                            )}
                                    </SpaceBetween>
                                ) : (
                                    /* Archived/directory case */
                                    <Container>
                                        <Box padding="m" textAlign="center">
                                            <div
                                                style={{
                                                    color: "var(--vams-text-secondary)",
                                                    fontSize: "16px",
                                                }}
                                            >
                                                {hasArchivedFiles
                                                    ? "Visualizer is not available for archived files."
                                                    : "Visualizer is not available for directories."}
                                            </div>
                                        </Box>
                                    </Container>
                                )}

                                {/* Delete Preview Modal */}
                                <Modal
                                    visible={showDeletePreviewModal}
                                    onDismiss={() => setShowDeletePreviewModal(false)}
                                    header="Delete Preview File"
                                    footer={
                                        <Box float="right">
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Button
                                                    variant="link"
                                                    onClick={() => setShowDeletePreviewModal(false)}
                                                    disabled={isPreviewDeleting}
                                                >
                                                    Cancel
                                                </Button>
                                                <Button
                                                    variant="primary"
                                                    onClick={async () => {
                                                        setIsPreviewDeleting(true);
                                                        try {
                                                            // File preview deletion
                                                            await archiveFile(
                                                                databaseId!,
                                                                assetId!,
                                                                {
                                                                    filePath:
                                                                        singleFileInfo!
                                                                            .previewFile!,
                                                                }
                                                            );
                                                            // Refresh the page to show updated file
                                                            window.location.reload();
                                                            setShowDeletePreviewModal(false);
                                                        } catch (error) {
                                                            console.error(
                                                                "Error deleting preview:",
                                                                error
                                                            );
                                                        } finally {
                                                            setIsPreviewDeleting(false);
                                                        }
                                                    }}
                                                    loading={isPreviewDeleting}
                                                >
                                                    Delete
                                                </Button>
                                            </SpaceBetween>
                                        </Box>
                                    }
                                >
                                    <p>
                                        Are you sure you want to delete this preview file? This
                                        action cannot be undone.
                                    </p>
                                </Modal>
                            </SpaceBetween>
                        </Box>
                    )}
                </>
            )}
            <div style={{ paddingBottom: "20px" }} />
        </div>
    );
}
