/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import { archiveFile } from "../../services/FileOperationsService";
import {
    Box,
    BreadcrumbGroup,
    Button,
    Container,
    Grid,
    Header,
    Modal,
    SegmentedControl,
    SpaceBetween,
} from "@cloudscape-design/components";
import { useLocation, useParams } from "react-router";

import ControlledMetadata from "../metadata/ControlledMetadata";
import { fetchAsset } from "../../services/APIService";
import { FileVersionsTable } from "../filemanager/components/FileVersionsTable";
// File format constants no longer needed - handled by plugin system
import DynamicViewer from "../../visualizerPlugin/components/DynamicViewer";
import AssetSelectorWithModal from "../selectors/AssetSelectorWithModal";
import { ErrorBoundary } from "react-error-boundary";
import Synonyms from "../../synonyms";

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
    const { state } = useLocation() as { state: ViewFileState };
    const { databaseId, assetId, pathViewType } = useParams<{
        databaseId: string;
        assetId: string;
        pathViewType?: string;
    }>();

    // Determine if we're in multi-file mode
    const isMultiFileMode = state?.files && state.files.length > 1;
    const currentFiles = isMultiFileMode ? state.files! : [];

    // For single file mode, use existing logic
    const singleFileInfo = isMultiFileMode
        ? null
        : {
              filename: state?.filename || "",
              key: state?.key || "",
              isDirectory: state?.isDirectory || false,
              versionId: state?.versionId,
              size: state?.size,
              dateCreatedCurrentVersion: state?.dateCreatedCurrentVersion,
              isArchived: state?.isArchived,
              primaryType: state?.primaryType,
              previewFile: state?.previewFile,
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
        <>
            {assetId && (
                <>
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
                                        href: "#/databases/" + databaseId + "/assets/" + assetId,
                                    },
                                    { text: getBreadcrumbText(), href: "#" },
                                ]}
                                ariaLabel="Breadcrumbs"
                            />
                            <div>
                                <h1>
                                    {getHeaderText()}{" "}
                                    {asset?.status === "archived" && (
                                        <span style={{ color: "#888" }}>(Archived)</span>
                                    )}
                                </h1>
                                {/* Show version info for single file mode - directly underneath title */}
                                {!isMultiFileMode && singleFileInfo?.versionId && (
                                    <div style={{ marginTop: "4px" }}>
                                        <span style={{ fontSize: "14px", color: "#666" }}>
                                            Version: {singleFileInfo.versionId}
                                        </span>
                                    </div>
                                )}
                            </div>

                            {/* Visualizer - show for both single and multi-file modes, but not for directories or archived files */}
                            {(!isMultiFileMode ? !singleFileInfo?.isDirectory : true) &&
                                !hasArchivedFiles && (
                                    <div id="view-edit-asset-right-column" className={viewerMode}>
                                        <SpaceBetween direction="vertical" size="m">
                                            <Container
                                                header={
                                                    <Grid
                                                        gridDefinition={[
                                                            { colspan: 3 },
                                                            { colspan: 9 },
                                                        ]}
                                                    >
                                                        <Box margin={{ bottom: "m" }}>
                                                            <Header variant="h2">Visualizer</Header>
                                                        </Box>
                                                        {viewerOptions.length > 0 && (
                                                            <SegmentedControl
                                                                label="Visualizer Control"
                                                                options={viewerOptions}
                                                                selectedId={viewType}
                                                                onChange={changeViewType}
                                                                className="visualizer-segment-control"
                                                            />
                                                        )}
                                                    </Grid>
                                                }
                                            >
                                                <>
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

                                                    {/* Delete Preview Modal */}
                                                    <Modal
                                                        visible={showDeletePreviewModal}
                                                        onDismiss={() =>
                                                            setShowDeletePreviewModal(false)
                                                        }
                                                        header="Delete Preview File"
                                                        footer={
                                                            <Box float="right">
                                                                <SpaceBetween
                                                                    direction="horizontal"
                                                                    size="xs"
                                                                >
                                                                    <Button
                                                                        variant="link"
                                                                        onClick={() =>
                                                                            setShowDeletePreviewModal(
                                                                                false
                                                                            )
                                                                        }
                                                                        disabled={isPreviewDeleting}
                                                                    >
                                                                        Cancel
                                                                    </Button>
                                                                    <Button
                                                                        variant="primary"
                                                                        onClick={async () => {
                                                                            setIsPreviewDeleting(
                                                                                true
                                                                            );
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
                                                                                setShowDeletePreviewModal(
                                                                                    false
                                                                                );
                                                                            } catch (error) {
                                                                                console.error(
                                                                                    "Error deleting preview:",
                                                                                    error
                                                                                );
                                                                            } finally {
                                                                                setIsPreviewDeleting(
                                                                                    false
                                                                                );
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
                                                            Are you sure you want to delete this
                                                            preview file? This action cannot be
                                                            undone.
                                                        </p>
                                                    </Modal>
                                                </>
                                            </Container>
                                        </SpaceBetween>
                                    </div>
                                )}

                            {/* Show message when files are archived */}
                            {hasArchivedFiles && (
                                <Container>
                                    <Box padding="m" textAlign="center">
                                        <div style={{ color: "#666", fontSize: "16px" }}>
                                            Visualizer is not available for archived files.
                                        </div>
                                    </Box>
                                </Container>
                            )}

                            {/* Show file list for multi-file mode - moved below visualizer */}
                            {isMultiFileMode && (
                                <Container header={<Header variant="h3">Selected Files</Header>}>
                                    <SpaceBetween direction="vertical" size="xs">
                                        {currentFiles.map((file, index) => (
                                            <Box
                                                key={index}
                                                padding={{ vertical: "xs", horizontal: "s" }}
                                            >
                                                <span style={{ fontFamily: "monospace" }}>
                                                    {file.filename}
                                                    {file.primaryType &&
                                                        file.primaryType.trim() !== "" && (
                                                            <span
                                                                style={{
                                                                    color: "#666",
                                                                    marginLeft: "4px",
                                                                }}
                                                            >
                                                                ({file.primaryType})
                                                            </span>
                                                        )}
                                                    {file.versionId && (
                                                        <span
                                                            style={{
                                                                color: "#666",
                                                                marginLeft: "8px",
                                                            }}
                                                        >
                                                            (Version: {file.versionId})
                                                        </span>
                                                    )}
                                                </span>
                                            </Box>
                                        ))}
                                    </SpaceBetween>
                                </Container>
                            )}

                            {/* Metadata - only show for single file mode and non-archived files */}
                            {!isMultiFileMode && !hasArchivedFiles && (
                                <ErrorBoundary
                                    fallback={
                                        <div>
                                            Metadata failed to load due to an error. Contact your
                                            VAMS administrator for help.
                                        </div>
                                    }
                                >
                                    <ControlledMetadata
                                        databaseId={databaseId!}
                                        assetId={assetId!}
                                        prefix={singleFileInfo?.key || ""}
                                    />
                                </ErrorBoundary>
                            )}

                            {/* Show message when files are archived in single file mode */}
                            {!isMultiFileMode && hasArchivedFiles && (
                                <Container header={<Header variant="h3">Metadata</Header>}>
                                    <Box padding="m" textAlign="center">
                                        <div style={{ color: "#666", fontSize: "16px" }}>
                                            Metadata is not available for archived files.
                                        </div>
                                    </Box>
                                </Container>
                            )}

                            {/* File Versions Container - only show for single file mode and non-directories */}
                            {!isMultiFileMode &&
                                singleFileInfo &&
                                !singleFileInfo.isDirectory &&
                                singleFileInfo.versionId && (
                                    <Container header={<Header variant="h3">File Versions</Header>}>
                                        <FileVersionsTable
                                            databaseId={databaseId!}
                                            assetId={assetId!}
                                            filePath={singleFileInfo.key}
                                            fileName={singleFileInfo.filename}
                                            currentVersionId={singleFileInfo.versionId}
                                            onVersionRevert={handleVersionRevert}
                                            displayMode="container"
                                            visible={true}
                                        />
                                    </Container>
                                )}
                        </SpaceBetween>
                    </Box>
                </>
            )}
            {pathViewType && <AssetSelectorWithModal pathViewType={pathViewType} />}
        </>
    );
}
