/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
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
import { useLocation, useNavigate, useParams } from "react-router";

import FileMetadata from "../metadata/FileMetadata";
import { fetchAsset, fetchFileInfo } from "../../services/APIService";
import { FileVersionsTable } from "../filemanager/components/FileVersionsTable";
// File format constants no longer needed - handled by plugin system
import DynamicViewer from "../../visualizerPlugin/components/DynamicViewer";
import AssetSelectorWithModal from "../selectors/AssetSelectorWithModal";

import Synonyms from "../../synonyms";
import { HorizontalResizableSplitter } from "../filemanager/components/HorizontalResizableSplitter";
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

export default function ViewFile() {
    const location = useLocation();
    const navigate = useNavigate();
    const { state } = location as { state: ViewFileState };
    const { databaseId, assetId, pathViewType } = useParams<{
        databaseId: string;
        assetId: string;
        pathViewType?: string;
    }>();

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

    const urlFilePath = getFilePathFromUrl();
    const urlVersion = getVersionFromQuery();
    const isDirectPathAccess = urlFilePath && !state;

    // State for direct path loading
    const [isLoadingDirectPath, setIsLoadingDirectPath] = useState(isDirectPathAccess);
    const [directPathError, setDirectPathError] = useState<string | null>(null);
    const [loadedFileInfo, setLoadedFileInfo] = useState<FileInfo | null>(null);
    // State for on-demand version loading when state is passed without versionId
    const [isLoadingVersionInfo, setIsLoadingVersionInfo] = useState(false);

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
        : state?.key && !state?.versionId && loadedFileInfo?.versionId
        ? // State has key but no versionId, and we've fetched version info - use loadedFileInfo
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

    // Check if any files are archived
    const hasArchivedFiles = isMultiFileMode
        ? currentFiles.some((file) => file.isArchived)
        : singleFileInfo?.isArchived === true;

    const [reload, setReload] = useState(true);
    const [viewType, setViewType] = useState<string | null>(null);
    const [asset, setAsset] = useState<Asset>({});

    const [viewerOptions, setViewerOptions] = useState<ViewerOption[]>([]);
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
        const loadFileFromDirectPath = async () => {
            if (!isDirectPathAccess || !databaseId || !assetId || !urlFilePath) {
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
                        "This file is part of an archived asset and cannot be viewed."
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

                console.log("Loaded file info:", loadedFile);
                setLoadedFileInfo(loadedFile);
                setIsLoadingDirectPath(false);
                setReload(true); // Trigger asset data fetch
            } catch (error: any) {
                console.error("Error loading file from direct path:", error);

                // Check for specific error types
                if (error?.response?.status === 403) {
                    setDirectPathError("You don't have permission to access this file.");
                } else if (error?.response?.status === 404) {
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
    }, [isDirectPathAccess, databaseId, assetId, urlFilePath, urlVersion]);

    // Fetch version info when state is passed but versionId is missing
    useEffect(() => {
        const loadVersionInfo = async () => {
            // Only run if:
            // 1. We have state with a key (navigated from file manager)
            // 2. But no versionId (file manager hadn't loaded full data yet)
            // 3. Not in multi-file mode
            // 4. Not already loading
            if (
                !state?.key ||
                state?.versionId ||
                isMultiFileMode ||
                isLoadingVersionInfo ||
                isLoadingDirectPath
            ) {
                return;
            }

            console.log("State passed without versionId, fetching version info for:", state.key);
            setIsLoadingVersionInfo(true);

            try {
                const [success, fileInfoResponse] = await fetchFileInfo({
                    databaseId: databaseId!,
                    assetId: assetId!,
                    fileKey: state.key,
                    includeVersions: true,
                });

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
                        // Update loadedFileInfo with the version info
                        // This will be used by singleFileInfo since state doesn't have versionId
                        setLoadedFileInfo({
                            filename: state.filename || fileInfo.fileName || "",
                            key: state.key,
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
                console.error("Error fetching version info:", error);
            } finally {
                setIsLoadingVersionInfo(false);
            }
        };

        loadVersionInfo();
    }, [
        state?.key,
        state?.versionId,
        isMultiFileMode,
        isLoadingVersionInfo,
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

                    let defaultViewType: string;
                    const newViewerOptions: ViewerOption[] = [];

                    if (isMultiFileMode) {
                        // Multi-file mode: simplified to just show Files tab
                        defaultViewType = "files";
                        newViewerOptions.push({ text: "Files", id: "files" });
                    } else {
                        // Single file mode: show Preview and File tabs
                        defaultViewType = "file"; // Always default to file view

                        // Add Preview tab if the file has a preview file
                        if (singleFileInfo?.previewFile) {
                            console.log("Using preview file:", singleFileInfo.previewFile);
                            newViewerOptions.push({ text: "Preview", id: "preview" });
                        }

                        // Always add File tab for the actual file
                        newViewerOptions.push({ text: "File", id: "file" });
                    }

                    setViewerOptions(newViewerOptions);
                    setViewType(defaultViewType);
                    setReload(false);
                }
            }
        };
        if (reload && !pathViewType) {
            getData();
        }
    }, [reload, assetId, databaseId, pathViewType]);

    // Update URL when version changes to keep URL copy/paste accurate
    useEffect(() => {
        // Only update URL for single file mode with a valid file key and version
        if (isMultiFileMode || !singleFileInfo?.key || !singleFileInfo?.versionId) {
            return;
        }

        // Don't update during initial loading
        if (isLoadingDirectPath) {
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
        const newUrl = `/databases/${databaseId}/assets/${assetId}/file/${encodedPath}?version=${encodeURIComponent(
            singleFileInfo.versionId
        )}`;

        // Get current URL path and query
        const currentPath = location.pathname;
        const currentSearch = location.search;
        const currentFullPath = currentPath + currentSearch;

        // Only update if the URL has changed (to avoid infinite loops)
        // Compare the expected URL structure
        const expectedPathBase = `/databases/${databaseId}/assets/${assetId}/file/`;
        if (
            currentPath.startsWith(expectedPathBase) ||
            currentPath === `/databases/${databaseId}/assets/${assetId}/file`
        ) {
            const currentVersion = getVersionFromQuery();
            if (currentVersion !== singleFileInfo.versionId) {
                // Update URL without adding to history (replace)
                navigate(newUrl, { replace: true });
            }
        }
    }, [
        singleFileInfo?.versionId,
        singleFileInfo?.key,
        databaseId,
        assetId,
        isMultiFileMode,
        isLoadingDirectPath,
        navigate,
        location.pathname,
        location.search,
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
            return `${asset?.assetName || "Asset"} - Multiple Files`;
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
        <div style={{ height: "100vh", overflow: "auto" }}>
            {assetId && (
                <>
                    {/* Show loading state for direct path access */}
                    {isLoadingDirectPath && (
                        <Box padding={{ top: "s", horizontal: "l" }}>
                            <Container>
                                <Box padding="xxl" textAlign="center">
                                    <SpaceBetween direction="vertical" size="m">
                                        <Spinner size="large" />
                                        <div style={{ color: "#666", fontSize: "16px" }}>
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
                                        <Link href={`#/databases/${databaseId}/assets/${assetId}`}>
                                            Return to asset
                                        </Link>
                                    </Alert>
                                </Box>
                            </Container>
                        </Box>
                    )}

                    {/* Show normal content when not loading and no error */}
                    {!isLoadingDirectPath && !directPathError && (
                        <Box padding={{ top: "s", horizontal: "l" }}>
                            <SpaceBetween direction="vertical" size="l">
                                <BreadcrumbGroup
                                    items={[
                                        { text: Synonyms.Databases, href: "#/databases/" },
                                        {
                                            text: databaseId || "",
                                            href: "#/databases/" + databaseId + "/assets/",
                                        },
                                        {
                                            text: asset?.assetName || "",
                                            href:
                                                "#/databases/" + databaseId + "/assets/" + assetId,
                                        },
                                        { text: getBreadcrumbText(), href: "#" },
                                    ]}
                                    ariaLabel="Breadcrumbs"
                                />
                                <div>
                                    {/* Main title and File/Preview buttons on same row */}
                                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                        <Box>
                                            <h1>
                                                {getHeaderText()}{" "}
                                                {asset?.status === "archived" && (
                                                    <span style={{ color: "#888" }}>
                                                        (Archived)
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
                                            {viewerOptions.length > 0 && (
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

                                    {/* Version info on its own row */}
                                    {!isMultiFileMode && singleFileInfo?.versionId && (
                                        <div style={{ marginTop: "4px" }}>
                                            <span style={{ fontSize: "14px", color: "#666" }}>
                                                Version: {singleFileInfo.versionId}
                                            </span>
                                        </div>
                                    )}
                                </div>

                                {/* Main content area with horizontal splitter */}
                                <div style={{ height: "calc(100vh - 200px)", minHeight: "400px" }}>
                                    {(!isMultiFileMode ? !singleFileInfo?.isDirectory : true) &&
                                    !hasArchivedFiles ? (
                                        <HorizontalResizableSplitter
                                            topPanel={
                                                <div
                                                    id="view-edit-asset-right-column"
                                                    className={viewerMode}
                                                >
                                                    <DynamicViewer
                                                        key={`${viewType}-${assetId}-${
                                                            singleFileInfo?.versionId ||
                                                            "no-version"
                                                        }`} // Force remount on tab switch
                                                        files={
                                                            isMultiFileMode || viewType === "files"
                                                                ? currentFiles
                                                                : singleFileInfo
                                                                ? [
                                                                      {
                                                                          ...singleFileInfo,
                                                                          key:
                                                                              viewType === "preview"
                                                                                  ? singleFileInfo.previewFile ||
                                                                                    singleFileInfo.key
                                                                                  : singleFileInfo.key,
                                                                      },
                                                                  ]
                                                                : []
                                                        }
                                                        assetId={assetId!}
                                                        databaseId={databaseId!}
                                                        viewerMode={viewerMode}
                                                        onViewerModeChange={changeViewerMode}
                                                        showViewerSelector={true} // Enable plugin-based viewer selection
                                                        isPreviewMode={viewType === "preview"}
                                                        onDeletePreview={undefined} // Don't show delete button in ViewFile.tsx
                                                    />
                                                </div>
                                            }
                                            bottomPanel={
                                                <SpaceBetween direction="vertical" size="l">
                                                    {/* Show file list for multi-file mode */}
                                                    {isMultiFileMode && (
                                                        <Container
                                                            header={
                                                                <Header variant="h3">
                                                                    Selected Files
                                                                </Header>
                                                            }
                                                        >
                                                            <SpaceBetween
                                                                direction="vertical"
                                                                size="xs"
                                                            >
                                                                {currentFiles.map((file, index) => (
                                                                    <Box
                                                                        key={index}
                                                                        padding={{
                                                                            vertical: "xs",
                                                                            horizontal: "s",
                                                                        }}
                                                                    >
                                                                        <span
                                                                            style={{
                                                                                fontFamily:
                                                                                    "monospace",
                                                                            }}
                                                                        >
                                                                            {file.filename}
                                                                            {file.primaryType &&
                                                                                file.primaryType.trim() !==
                                                                                    "" && (
                                                                                    <span
                                                                                        style={{
                                                                                            color: "#666",
                                                                                            marginLeft:
                                                                                                "4px",
                                                                                        }}
                                                                                    >
                                                                                        (
                                                                                        {
                                                                                            file.primaryType
                                                                                        }
                                                                                        )
                                                                                    </span>
                                                                                )}
                                                                            {file.versionId && (
                                                                                <span
                                                                                    style={{
                                                                                        color: "#666",
                                                                                        marginLeft:
                                                                                            "8px",
                                                                                    }}
                                                                                >
                                                                                    (Version:{" "}
                                                                                    {file.versionId}
                                                                                    )
                                                                                </span>
                                                                            )}
                                                                        </span>
                                                                    </Box>
                                                                ))}
                                                            </SpaceBetween>
                                                        </Container>
                                                    )}

                                                    {/* Metadata - only show for single file mode and non-archived files */}
                                                    {!isMultiFileMode && singleFileInfo?.key && (
                                                        <FileMetadata
                                                            databaseId={databaseId!}
                                                            assetId={assetId!}
                                                            prefix={singleFileInfo.key}
                                                            showHeader={false}
                                                            className="viewfile-metadata"
                                                        />
                                                    )}

                                                    {/* File Versions Container - only show for single file mode and non-directories */}
                                                    {!isMultiFileMode &&
                                                        singleFileInfo &&
                                                        singleFileInfo.key &&
                                                        !singleFileInfo.isDirectory &&
                                                        singleFileInfo.versionId && (
                                                            <Container
                                                                header={
                                                                    <Header variant="h3">
                                                                        File Versions
                                                                    </Header>
                                                                }
                                                            >
                                                                <FileVersionsTable
                                                                    databaseId={databaseId!}
                                                                    assetId={assetId!}
                                                                    filePath={singleFileInfo.key}
                                                                    fileName={
                                                                        singleFileInfo.filename
                                                                    }
                                                                    currentVersionId={
                                                                        singleFileInfo.versionId
                                                                    }
                                                                    onVersionRevert={
                                                                        handleVersionRevert
                                                                    }
                                                                    displayMode="container"
                                                                    visible={true}
                                                                />
                                                            </Container>
                                                        )}
                                                </SpaceBetween>
                                            }
                                            className="viewfile-splitter"
                                        />
                                    ) : (
                                        /* Show message when files are archived or are directories */
                                        <Container>
                                            <Box padding="m" textAlign="center">
                                                <div style={{ color: "#666", fontSize: "16px" }}>
                                                    {hasArchivedFiles
                                                        ? "Visualizer is not available for archived files."
                                                        : "Visualizer is not available for directories."}
                                                </div>
                                            </Box>

                                            {/* Show metadata for archived files in single file mode */}
                                            {!isMultiFileMode && hasArchivedFiles && (
                                                <Container
                                                    header={<Header variant="h3">Metadata</Header>}
                                                >
                                                    <Box padding="m" textAlign="center">
                                                        <div
                                                            style={{
                                                                color: "#666",
                                                                fontSize: "16px",
                                                            }}
                                                        >
                                                            Metadata is not available for archived
                                                            files.
                                                        </div>
                                                    </Box>
                                                </Container>
                                            )}
                                        </Container>
                                    )}
                                </div>

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
            {pathViewType && <AssetSelectorWithModal pathViewType={pathViewType} />}
        </div>
    );
}
