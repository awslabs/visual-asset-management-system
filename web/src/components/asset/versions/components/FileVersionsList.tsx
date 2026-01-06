/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useState, useEffect, useMemo } from "react";
import {
    Alert,
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    Spinner,
    Table,
    Link,
    Badge,
    ProgressBar,
    TextFilter,
    Pagination,
    CollectionPreferences,
    Tabs,
    SegmentedControl,
} from "@cloudscape-design/components";
import { useNavigate, useParams } from "react-router";
import { AssetVersionContext, FileVersion, AssetVersionMetadataItem } from "../AssetVersionManager";
import { downloadAsset } from "../../../../services/APIService";

export const FileVersionsList: React.FC = () => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    const navigate = useNavigate();

    // Get context values
    const context = useContext(AssetVersionContext);

    if (!context) {
        throw new Error("FileVersionsList must be used within an AssetVersionContext.Provider");
    }

    const {
        loading,
        selectedVersion,
        selectedVersionDetails,
        fileFilterText,
        setFileFilterText,
        fileCurrentPage,
        setFileCurrentPage,
        filePageSize,
        setFilePageSize,
        filteredFiles,
        totalFiles,
    } = context;

    // State for preferences
    const [preferences, setPreferences] = useState<{
        pageSize: number;
        visibleContent: string[];
    }>({
        pageSize: filePageSize,
        visibleContent: ["fileName", "path", "size", "lastModified", "versionId", "actions"],
    });

    // Update preferences when page size changes
    useEffect(() => {
        if (preferences.pageSize !== filePageSize) {
            setPreferences((prev) => ({
                ...prev,
                pageSize: filePageSize,
            }));
        }
    }, [filePageSize]);

    // Debug logs to trace data flow
    console.log("FileVersionsList - selectedVersion:", selectedVersion);
    console.log("FileVersionsList - selectedVersionDetails:", selectedVersionDetails);
    console.log("FileVersionsList - files:", selectedVersionDetails?.files);

    // Ensure we maintain the selected version reference and prevent unnecessary re-renders
    const [lastRenderedVersionId, setLastRenderedVersionId] = useState<string | null>(null);

    useEffect(() => {
        if (selectedVersion) {
            console.log("FileVersionsList - Selected version:", selectedVersion.Version);

            // Track the last rendered version to detect unnecessary re-renders
            if (lastRenderedVersionId !== selectedVersion.Version) {
                console.log(
                    "FileVersionsList - New version detected, updating lastRenderedVersionId"
                );
                setLastRenderedVersionId(selectedVersion.Version);
            }
        } else {
            setLastRenderedVersionId(null);
        }
    }, [selectedVersion, lastRenderedVersionId]);

    // State for error handling and download progress
    const [downloadError, setDownloadError] = useState<string | null>(null);
    const [downloadingFile, setDownloadingFile] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<{ [key: string]: number }>({});
    const [downloadStatus, setDownloadStatus] = useState<{ [key: string]: string }>({});

    // State for tabs
    const [activeTabId, setActiveTabId] = useState<string>("files");

    // State for metadata filtering
    const [metadataTypeFilter, setMetadataTypeFilter] = useState<string>("all");
    const [metadataLocationFilter, setMetadataLocationFilter] = useState<string>("all");
    const [metadataSearchText, setMetadataSearchText] = useState<string>("");
    const [metadataCurrentPage, setMetadataCurrentPage] = useState<number>(1);
    const [metadataPageSize, setMetadataPageSize] = useState<number>(25);

    // Filter and paginate metadata
    const filteredMetadata = useMemo(() => {
        if (!selectedVersionDetails?.versionedMetadata) return [];

        let filtered = selectedVersionDetails.versionedMetadata;

        // Filter by type
        if (metadataTypeFilter !== "all") {
            filtered = filtered.filter((item) => item.type === metadataTypeFilter);
        }

        // Filter by location
        if (metadataLocationFilter === "asset") {
            filtered = filtered.filter((item) => item.filePath === "/");
        } else if (metadataLocationFilter === "files") {
            filtered = filtered.filter((item) => item.filePath !== "/");
        }

        // Filter by search text
        if (metadataSearchText.trim()) {
            const searchLower = metadataSearchText.toLowerCase();
            filtered = filtered.filter(
                (item) =>
                    item.metadataKey.toLowerCase().includes(searchLower) ||
                    item.metadataValue.toLowerCase().includes(searchLower) ||
                    item.filePath.toLowerCase().includes(searchLower)
            );
        }

        return filtered;
    }, [
        selectedVersionDetails?.versionedMetadata,
        metadataTypeFilter,
        metadataLocationFilter,
        metadataSearchText,
    ]);

    const paginatedMetadata = useMemo(() => {
        const startIndex = (metadataCurrentPage - 1) * metadataPageSize;
        return filteredMetadata.slice(startIndex, startIndex + metadataPageSize);
    }, [filteredMetadata, metadataCurrentPage, metadataPageSize]);

    // Metadata table columns
    const metadataColumns = [
        {
            id: "type",
            header: "Type",
            cell: (item: AssetVersionMetadataItem) => (
                <Badge color={item.type === "metadata" ? "blue" : "grey"}>
                    {item.type === "metadata" ? "Metadata" : "Attribute"}
                </Badge>
            ),
            sortingField: "type",
        },
        {
            id: "location",
            header: "Location",
            cell: (item: AssetVersionMetadataItem) => (
                <Box>
                    {item.filePath === "/" ? (
                        <Badge color="green">Asset</Badge>
                    ) : (
                        <div style={{ fontFamily: "monospace", fontSize: "0.9em" }}>
                            {item.filePath}
                        </div>
                    )}
                </Box>
            ),
            sortingField: "filePath",
        },
        {
            id: "key",
            header: "Key",
            cell: (item: AssetVersionMetadataItem) => (
                <div style={{ fontWeight: "500" }}>{item.metadataKey}</div>
            ),
            sortingField: "metadataKey",
        },
        {
            id: "value",
            header: "Value",
            cell: (item: AssetVersionMetadataItem) => (
                <div style={{ wordBreak: "break-word" }}>{item.metadataValue}</div>
            ),
            sortingField: "metadataValue",
        },
        {
            id: "valueType",
            header: "Value Type",
            cell: (item: AssetVersionMetadataItem) => (
                <div style={{ fontSize: "0.9em", color: "#5f6b7a" }}>{item.metadataValueType}</div>
            ),
            sortingField: "metadataValueType",
        },
    ];

    // Handle view file
    const handleViewFile = (file: FileVersion) => {
        // Don't allow viewing permanently deleted files
        if (file.isPermanentlyDeleted) {
            return;
        }

        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: file.relativeKey.split("/").pop() || file.relativeKey,
                key: file.relativeKey,
                isDirectory: false,
                versionId: file.versionId,
                size: file.size,
                dateCreatedCurrentVersion: file.lastModified,
                isArchived: file.isArchived,
            },
        });
    };

    // Handle download file with progress tracking
    const handleDownloadFile = async (file: FileVersion) => {
        // Don't allow downloading permanently deleted files
        if (file.isPermanentlyDeleted) {
            return;
        }

        try {
            setDownloadingFile(file.relativeKey);
            setDownloadError(null);
            setDownloadProgress((prev) => ({ ...prev, [file.relativeKey]: 0 }));
            setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "downloading" }));

            const response = await downloadAsset({
                assetId: assetId!,
                databaseId: databaseId!,
                key: file.relativeKey,
                versionId: file.versionId,
                downloadType: "assetFile",
            });

            if (response !== false && Array.isArray(response) && response[0] !== false) {
                // Create a download link
                const link = document.createElement("a");
                link.href = response[1];

                // Track download progress if browser supports it
                if (window.XMLHttpRequest) {
                    const xhr = new XMLHttpRequest();
                    xhr.open("GET", response[1], true);
                    xhr.responseType = "blob";

                    xhr.onprogress = (event) => {
                        if (event.lengthComputable) {
                            const progress = Math.round((event.loaded / event.total) * 100);
                            setDownloadProgress((prev) => ({
                                ...prev,
                                [file.relativeKey]: progress,
                            }));
                        }
                    };

                    xhr.onload = () => {
                        if (xhr.status === 200) {
                            // Create a blob URL and trigger download
                            const blob = new Blob([xhr.response]);
                            const url = window.URL.createObjectURL(blob);
                            link.href = url;
                            link.download = file.relativeKey.split("/").pop() || file.relativeKey;
                            link.click();
                            window.URL.revokeObjectURL(url);

                            // Mark as complete
                            setDownloadProgress((prev) => ({ ...prev, [file.relativeKey]: 100 }));
                            setDownloadStatus((prev) => ({
                                ...prev,
                                [file.relativeKey]: "complete",
                            }));
                            setTimeout(() => {
                                setDownloadingFile(null);
                            }, 1000);
                        } else {
                            setDownloadError(
                                `Failed to download file: ${file.relativeKey} (Status: ${xhr.status})`
                            );
                            setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "error" }));
                            setDownloadingFile(null);
                        }
                    };

                    xhr.onerror = () => {
                        setDownloadError(
                            `Network error while downloading file: ${file.relativeKey}`
                        );
                        setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "error" }));
                        setDownloadingFile(null);
                    };

                    xhr.send();
                } else {
                    // Fallback for browsers that don't support XMLHttpRequest
                    link.download = file.relativeKey.split("/").pop() || file.relativeKey;
                    link.click();
                    setDownloadProgress((prev) => ({ ...prev, [file.relativeKey]: 100 }));
                    setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "complete" }));
                    setTimeout(() => {
                        setDownloadingFile(null);
                    }, 1000);
                }
            } else {
                setDownloadError(`Failed to download file: ${file.relativeKey}`);
                setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "error" }));
                console.error("Failed to download file");
                setDownloadingFile(null);
            }
        } catch (err) {
            setDownloadError(
                `Error downloading file: ${err instanceof Error ? err.message : "Unknown error"}`
            );
            setDownloadStatus((prev) => ({ ...prev, [file.relativeKey]: "error" }));
            console.error("Error downloading file:", err);
            setDownloadingFile(null);
        }
    };

    // Format file size
    const formatFileSize = (size?: number): string => {
        if (size === undefined) return "Unknown";
        if (size === 0) return "0 B";

        const units = ["B", "KB", "MB", "GB", "TB"];
        const i = Math.floor(Math.log(size) / Math.log(1024));
        return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
    };

    // Format date
    const formatDate = (dateString?: string): string => {
        if (!dateString) return "Unknown";
        try {
            const date = new Date(dateString);
            return date.toLocaleString();
        } catch (e) {
            return dateString;
        }
    };

    // Table columns
    const columns = [
        {
            id: "fileName",
            header: "File Name",
            cell: (item: FileVersion) => {
                const fileName = item.relativeKey.split("/").pop() || item.relativeKey;
                return (
                    <Box>
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                                opacity: item.isPermanentlyDeleted ? 0.6 : 1,
                            }}
                        >
                            <span>{fileName}</span>
                            {item.isPermanentlyDeleted && (
                                <Badge color="red">Permanently Deleted</Badge>
                            )}
                            {item.isLatestVersionArchived && !item.isPermanentlyDeleted && (
                                <Badge color="grey">Latest Version Archived</Badge>
                            )}
                        </div>
                    </Box>
                );
            },
            sortingField: "relativeKey",
        },
        {
            id: "path",
            header: "Path",
            cell: (item: FileVersion) => (
                <Box>
                    <div
                        style={{
                            opacity: item.isPermanentlyDeleted ? 0.6 : 1,
                            fontFamily: "monospace",
                            fontSize: "0.9em",
                            wordBreak: "break-all",
                        }}
                    >
                        {item.relativeKey}
                    </div>
                </Box>
            ),
            sortingField: "relativeKey",
        },
        {
            id: "size",
            header: "Size",
            cell: (item: FileVersion) => (
                <Box>
                    <div style={{ opacity: item.isPermanentlyDeleted ? 0.6 : 1 }}>
                        {formatFileSize(item.size)}
                    </div>
                </Box>
            ),
            sortingField: "size",
        },
        {
            id: "lastModified",
            header: "Last Modified",
            cell: (item: FileVersion) => formatDate(item.lastModified),
            sortingField: "lastModified",
        },
        {
            id: "versionId",
            header: "Version ID",
            cell: (item: FileVersion) => (
                <Box>
                    <div style={{ fontFamily: "monospace", fontSize: "0.9em" }}>
                        {item.versionId}
                    </div>
                </Box>
            ),
            sortingField: "versionId",
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: FileVersion) => {
                // Don't show actions for permanently deleted files
                if (item.isPermanentlyDeleted) {
                    return <Box>File permanently deleted</Box>;
                }

                // Show download progress if file is being downloaded
                if (downloadingFile === item.relativeKey) {
                    return (
                        <SpaceBetween direction="vertical" size="xs">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={() => handleViewFile(item)} disabled={true}>
                                    View File
                                </Button>
                                <Button iconName="download" loading={true} disabled={true}>
                                    Downloading...
                                </Button>
                            </SpaceBetween>
                            <ProgressBar
                                value={downloadProgress[item.relativeKey] || 0}
                                label={`${downloadProgress[item.relativeKey] || 0}%`}
                                description={
                                    downloadStatus[item.relativeKey] === "error"
                                        ? "Error"
                                        : "Downloading..."
                                }
                                status={
                                    downloadStatus[item.relativeKey] === "error"
                                        ? "error"
                                        : "in-progress"
                                }
                            />
                        </SpaceBetween>
                    );
                }

                // Show completed download status briefly
                if (
                    downloadStatus[item.relativeKey] === "complete" &&
                    downloadProgress[item.relativeKey] === 100
                ) {
                    return (
                        <SpaceBetween direction="vertical" size="xs">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={() => handleViewFile(item)} disabled={false}>
                                    View File
                                </Button>
                                <Button
                                    onClick={() => handleDownloadFile(item)}
                                    iconName="download"
                                    disabled={false}
                                >
                                    Download File
                                </Button>
                            </SpaceBetween>
                            <ProgressBar
                                value={100}
                                label="100%"
                                description="Download complete"
                                status="success"
                            />
                        </SpaceBetween>
                    );
                }

                // Default view
                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            onClick={() => handleViewFile(item)}
                            disabled={item.isPermanentlyDeleted}
                        >
                            View File
                        </Button>
                        <Button
                            onClick={() => handleDownloadFile(item)}
                            iconName="download"
                            loading={downloadingFile === item.relativeKey}
                            disabled={item.isPermanentlyDeleted}
                        >
                            Download File
                        </Button>
                    </SpaceBetween>
                );
            },
        },
    ];

    // Render loading state - only show spinner if actually loading
    if (loading && !selectedVersionDetails) {
        console.log("FileVersionsList - Loading state, no selectedVersionDetails");
        return (
            <Container header={<Header variant="h3">Associated Files</Header>}>
                <Box textAlign="center" padding="l">
                    <Spinner size="normal" />
                    <div>Loading file versions...</div>
                </Box>
            </Container>
        );
    }

    // If we don't have a selected version, don't render anything
    if (!selectedVersion) {
        return null;
    }

    // If we have a selected version but no details yet, and not loading, show loading message
    if (selectedVersion && !selectedVersionDetails && !loading) {
        console.log("FileVersionsList - Selected version but no details, not loading");
        return (
            <Container
                header={<Header variant="h3">Version v{selectedVersion?.Version} Details</Header>}
            >
                <Box textAlign="center" padding="l">
                    <div>Loading version details...</div>
                </Box>
            </Container>
        );
    }

    // Check if BOTH files and metadata are empty
    const hasFiles = selectedVersionDetails?.files && selectedVersionDetails.files.length > 0;
    const hasMetadata =
        selectedVersionDetails?.versionedMetadata &&
        selectedVersionDetails.versionedMetadata.length > 0;

    // Only show "no data" message if BOTH files and metadata are empty
    if (selectedVersionDetails && !hasFiles && !hasMetadata) {
        console.log("FileVersionsList - No files and no metadata");
        return (
            <Container
                header={<Header variant="h3">Version v{selectedVersion?.Version} Details</Header>}
            >
                <Box textAlign="center" padding="l">
                    <div>No files or metadata associated with this asset version</div>
                </Box>
            </Container>
        );
    }

    // If we have metadata but no files, still show the tabs
    // The files tab will show "No files" but metadata tab will show data

    // Render files tab content
    const renderFilesTab = () => (
        <Table
            columnDefinitions={columns}
            items={filteredFiles || []}
            loading={loading}
            loadingText="Loading file versions"
            empty={
                <Box textAlign="center" padding="l">
                    <div>No files associated with this asset version</div>
                </Box>
            }
            header={
                <Box padding="s">
                    <SpaceBetween direction="vertical" size="xs">
                        <SpaceBetween direction="horizontal" size="xs">
                            <div>
                                <strong>Total files:</strong> {totalFiles}
                            </div>
                            <div>
                                <strong>Created by:</strong>{" "}
                                {selectedVersionDetails?.createdBy || "System"}
                            </div>
                            <div>
                                <strong>Created on:</strong>{" "}
                                {formatDate(selectedVersionDetails?.dateCreated)}
                            </div>
                        </SpaceBetween>
                        {selectedVersionDetails?.comment && (
                            <div>
                                <strong>Version comment:</strong> {selectedVersionDetails.comment}
                            </div>
                        )}
                        {selectedVersionDetails?.files && (
                            <div>
                                <strong>File status:</strong>{" "}
                                <span style={{ marginRight: "12px" }}>
                                    <Badge color="green">
                                        {
                                            selectedVersionDetails.files.filter(
                                                (f) =>
                                                    !f.isPermanentlyDeleted &&
                                                    !f.isLatestVersionArchived
                                            ).length
                                        }{" "}
                                        Available
                                    </Badge>
                                </span>
                                {selectedVersionDetails.files.some(
                                    (f) => f.isLatestVersionArchived && !f.isPermanentlyDeleted
                                ) && (
                                    <span style={{ marginRight: "12px" }}>
                                        <Badge color="grey">
                                            {
                                                selectedVersionDetails.files.filter(
                                                    (f) =>
                                                        f.isLatestVersionArchived &&
                                                        !f.isPermanentlyDeleted
                                                ).length
                                            }{" "}
                                            Archived
                                        </Badge>
                                    </span>
                                )}
                                {selectedVersionDetails.files.some(
                                    (f) => f.isPermanentlyDeleted
                                ) && (
                                    <span>
                                        <Badge color="red">
                                            {
                                                selectedVersionDetails.files.filter(
                                                    (f) => f.isPermanentlyDeleted
                                                ).length
                                            }{" "}
                                            Permanently Deleted
                                        </Badge>
                                    </span>
                                )}
                            </div>
                        )}
                    </SpaceBetween>
                </Box>
            }
            filter={
                <TextFilter
                    filteringText={fileFilterText}
                    filteringPlaceholder="Find files"
                    filteringAriaLabel="Filter files"
                    onChange={({ detail }) => setFileFilterText(detail.filteringText)}
                />
            }
            pagination={
                <Pagination
                    currentPageIndex={fileCurrentPage}
                    pagesCount={Math.max(1, Math.ceil(totalFiles / filePageSize))}
                    onChange={({ detail }) => setFileCurrentPage(detail.currentPageIndex)}
                    ariaLabels={{
                        nextPageLabel: "Next page",
                        previousPageLabel: "Previous page",
                        pageLabel: (pageNumber) =>
                            `Page ${pageNumber} of ${Math.max(
                                1,
                                Math.ceil(totalFiles / filePageSize)
                            )}`,
                    }}
                />
            }
            preferences={
                <CollectionPreferences
                    title="Preferences"
                    confirmLabel="Confirm"
                    cancelLabel="Cancel"
                    preferences={preferences}
                    onConfirm={({ detail }) => {
                        // Create a new preferences object with the correct types
                        const newPreferences = {
                            pageSize: detail.pageSize || preferences.pageSize,
                            visibleContent: detail.visibleContent
                                ? [...detail.visibleContent]
                                : preferences.visibleContent,
                        };
                        setPreferences(newPreferences);

                        // Update page size if changed
                        if (detail.pageSize !== undefined && detail.pageSize !== filePageSize) {
                            setFilePageSize(detail.pageSize);
                        }
                    }}
                    pageSizePreference={{
                        title: "Page size",
                        options: [
                            { value: 10, label: "10 files" },
                            { value: 20, label: "20 files" },
                            { value: 50, label: "50 files" },
                            { value: 100, label: "100 files" },
                        ],
                    }}
                    visibleContentPreference={{
                        title: "Select visible columns",
                        options: [
                            {
                                label: "File information",
                                options: [
                                    { id: "fileName", label: "File Name" },
                                    { id: "path", label: "Path" },
                                    { id: "size", label: "Size" },
                                    { id: "lastModified", label: "Last Modified" },
                                    { id: "versionId", label: "Version ID" },
                                ],
                            },
                            {
                                label: "Actions",
                                options: [{ id: "actions", label: "Actions" }],
                            },
                        ],
                    }}
                />
            }
            visibleColumns={preferences.visibleContent}
        />
    );

    // Render metadata tab content
    const renderMetadataTab = () => {
        const hasMetadata =
            selectedVersionDetails?.versionedMetadata &&
            selectedVersionDetails.versionedMetadata.length > 0;

        if (!hasMetadata) {
            return (
                <Box textAlign="center" padding="l">
                    <div>No metadata snapshot available for this version</div>
                </Box>
            );
        }

        return (
            <SpaceBetween direction="vertical" size="l">
                <SpaceBetween direction="horizontal" size="xs">
                    <SegmentedControl
                        selectedId={metadataTypeFilter}
                        onChange={({ detail }) => {
                            setMetadataTypeFilter(detail.selectedId);
                            setMetadataCurrentPage(1);
                        }}
                        label="Filter by type"
                        options={[
                            { text: "All", id: "all" },
                            { text: "Metadata", id: "metadata" },
                            { text: "Attributes", id: "attribute" },
                        ]}
                    />
                    <SegmentedControl
                        selectedId={metadataLocationFilter}
                        onChange={({ detail }) => {
                            setMetadataLocationFilter(detail.selectedId);
                            setMetadataCurrentPage(1);
                        }}
                        label="Filter by location"
                        options={[
                            { text: "All", id: "all" },
                            { text: "Asset-level", id: "asset" },
                            { text: "File-level", id: "files" },
                        ]}
                    />
                </SpaceBetween>

                <Table
                    columnDefinitions={metadataColumns}
                    items={paginatedMetadata}
                    loading={loading}
                    loadingText="Loading metadata"
                    empty={
                        <Box textAlign="center" padding="l">
                            <div>No metadata matches the current filters</div>
                        </Box>
                    }
                    header={
                        <Box padding="s">
                            <SpaceBetween direction="vertical" size="xs">
                                <div>
                                    <strong>Total items:</strong> {filteredMetadata.length}
                                    {selectedVersionDetails?.versionedMetadata &&
                                        filteredMetadata.length !==
                                            selectedVersionDetails.versionedMetadata.length && (
                                            <span style={{ marginLeft: "8px", color: "#5f6b7a" }}>
                                                (filtered from{" "}
                                                {selectedVersionDetails.versionedMetadata.length})
                                            </span>
                                        )}
                                </div>
                            </SpaceBetween>
                        </Box>
                    }
                    filter={
                        <TextFilter
                            filteringText={metadataSearchText}
                            filteringPlaceholder="Search metadata keys, values, or file paths"
                            filteringAriaLabel="Filter metadata"
                            onChange={({ detail }) => {
                                setMetadataSearchText(detail.filteringText);
                                setMetadataCurrentPage(1);
                            }}
                        />
                    }
                    pagination={
                        <Pagination
                            currentPageIndex={metadataCurrentPage}
                            pagesCount={Math.max(
                                1,
                                Math.ceil(filteredMetadata.length / metadataPageSize)
                            )}
                            onChange={({ detail }) =>
                                setMetadataCurrentPage(detail.currentPageIndex)
                            }
                            ariaLabels={{
                                nextPageLabel: "Next page",
                                previousPageLabel: "Previous page",
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of ${Math.max(
                                        1,
                                        Math.ceil(filteredMetadata.length / metadataPageSize)
                                    )}`,
                            }}
                        />
                    }
                    preferences={
                        <CollectionPreferences
                            title="Preferences"
                            confirmLabel="Confirm"
                            cancelLabel="Cancel"
                            preferences={{
                                pageSize: metadataPageSize,
                            }}
                            onConfirm={({ detail }) => {
                                if (detail.pageSize !== undefined) {
                                    setMetadataPageSize(detail.pageSize);
                                    setMetadataCurrentPage(1);
                                }
                            }}
                            pageSizePreference={{
                                title: "Page size",
                                options: [
                                    { value: 10, label: "10 items" },
                                    { value: 25, label: "25 items" },
                                    { value: 50, label: "50 items" },
                                    { value: 100, label: "100 items" },
                                ],
                            }}
                        />
                    }
                />
            </SpaceBetween>
        );
    };

    return (
        <Container
            header={<Header variant="h3">Version v{selectedVersion?.Version} Details</Header>}
        >
            {downloadError && (
                <Alert type="error" dismissible onDismiss={() => setDownloadError(null)}>
                    {downloadError}
                </Alert>
            )}
            <Tabs
                activeTabId={activeTabId}
                onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
                tabs={[
                    {
                        id: "files",
                        label: `Files (${totalFiles})`,
                        content: renderFilesTab(),
                    },
                    {
                        id: "metadata",
                        label: `Metadata${
                            selectedVersionDetails?.versionedMetadata
                                ? ` (${selectedVersionDetails.versionedMetadata.length})`
                                : ""
                        }`,
                        content: renderMetadataTab(),
                    },
                ]}
            />
        </Container>
    );
};
