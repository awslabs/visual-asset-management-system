/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Box,
    SpaceBetween,
    Header,
    Table,
    Button,
    Pagination,
    StatusIndicator,
    Alert,
    Spinner,
} from "@cloudscape-design/components";
import Popover from "@cloudscape-design/components/popover";
import { downloadAsset, revertFileVersion } from "../../../services/APIService";
import { fetchFileVersions } from "../../../services/AssetVersionService";
import { getChangeSourceLabel } from "../utils/changeSourceLabels";
import { useNavigate } from "react-router";
import Synonyms from "../../../synonyms";

// TypeScript interfaces
interface FileVersion {
    versionId: string;
    lastModified: string;
    size: number;
    isLatest: boolean;
    storageClass: string;
    etag?: string;
    isArchived: boolean;
    currentAssetVersionFileVersionMismatch?: boolean;
    assetVersionIds?: { id: string; label: string }[];
    changeSource?: string;
    changeUserId?: string;
    changeWorkflowId?: string;
    changeWorkflowExecutionId?: string;
    changeAssetIdFrom?: string;
    changeDatabaseIdFrom?: string;
    changeAssetFilePathFrom?: string;
    changeAssetFileVersionFrom?: string;
}

interface FileVersionsTableProps {
    databaseId: string;
    assetId: string;
    filePath: string;
    fileName: string;
    currentVersionId?: string; // For ViewFile context
    onVersionRevert?: () => void; // Refresh callback
    displayMode?: "modal" | "container"; // Display context
    visible?: boolean; // For modal context
    assetVersionId?: string; // Current asset version context for highlighting
}

interface RevertConfirmationModalProps {
    visible: boolean;
    onDismiss: () => void;
    onConfirm: () => void;
    versionId: string;
    fileName: string;
    isLoading: boolean;
}

// Helper function to format file size
const formatFileSize = (size: number): string => {
    if (size === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(size) / Math.log(1024));
    return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
};

// Helper function to format date
const formatDate = (dateString: string): string => {
    try {
        const date = new Date(dateString);
        return date.toLocaleString();
    } catch (e) {
        return dateString;
    }
};

// Revert Confirmation Modal Component (imported from the original modal)
const RevertConfirmationModal: React.FC<RevertConfirmationModalProps> = ({
    visible,
    onDismiss,
    onConfirm,
    versionId,
    fileName,
    isLoading,
}) => {
    return (
        <div className="revert-confirmation-modal" style={{ paddingTop: "20px" }}>
            {visible && (
                <Box>
                    <SpaceBetween direction="vertical" size="m">
                        <Alert type="warning">
                            This will create a new current version with the contents of version{" "}
                            <strong>{versionId}</strong>.
                        </Alert>
                        <Box>
                            <p>
                                Are you sure you want to revert <strong>{fileName}</strong> to
                                version <strong>{versionId}</strong>?
                            </p>
                            <p>
                                This action will create a new version that becomes the current
                                version, containing the same content as the selected version.
                            </p>
                        </Box>
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="link" onClick={onDismiss} disabled={isLoading}>
                                    Cancel
                                </Button>
                                <Button variant="primary" onClick={onConfirm} loading={isLoading}>
                                    Revert
                                </Button>
                            </SpaceBetween>
                        </Box>
                    </SpaceBetween>
                </Box>
            )}
        </div>
    );
};

// Main FileVersionsTable Component
export const FileVersionsTable: React.FC<FileVersionsTableProps> = ({
    databaseId,
    assetId,
    filePath,
    fileName,
    currentVersionId,
    onVersionRevert,
    displayMode = "container",
    visible = true,
    assetVersionId,
}) => {
    const navigate = useNavigate();

    // State management
    const [versions, setVersions] = useState<FileVersion[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState(1);
    const [showRevertModal, setShowRevertModal] = useState(false);
    const [selectedVersionForRevert, setSelectedVersionForRevert] = useState<string>("");
    const [revertLoading, setRevertLoading] = useState(false);

    const itemsPerPage = 20;
    const totalPages = Math.ceil(versions.length / itemsPerPage);
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const currentVersions = versions.slice(startIndex, endIndex);

    // Load file versions when component mounts or props change
    useEffect(() => {
        if (visible && databaseId && assetId && filePath) {
            loadFileVersions();
        }
    }, [visible, databaseId, assetId, filePath]);

    const loadFileVersions = async () => {
        setLoading(true);
        setError(null);

        try {
            const [success, response] = await fetchFileVersions({
                databaseId,
                assetId,
                filePath,
            });

            if (success && response?.versions) {
                setVersions(response.versions);
                setCurrentPage(1); // Reset to first page
            } else {
                setError(response || "Failed to load file versions");
                setVersions([]);
            }
        } catch (err: any) {
            setError(err.message || "An error occurred while loading file versions");
            setVersions([]);
        } finally {
            setLoading(false);
        }
    };

    // Handle download version
    const handleDownloadVersion = async (versionId: string) => {
        try {
            const [success, downloadUrl] = (await downloadAsset({
                databaseId,
                assetId,
                key: filePath,
                versionId: versionId,
                downloadType: "assetFile",
            })) as [boolean, any];

            if (success && downloadUrl) {
                const link = document.createElement("a");
                link.href = downloadUrl;
                link.click();
            } else {
                setError("Failed to download file version");
            }
        } catch (err: any) {
            setError(err.message || "An error occurred while downloading the file");
        }
    };

    // Handle view version
    const handleViewVersion = (versionId: string) => {
        // Find the version to get its isArchived status
        const version = versions.find((v) => v.versionId === versionId);

        // Get the relative path from the file path
        // The filePath format is typically: assetId/relativePath
        // We need to extract just the relativePath part
        const pathParts = filePath.split("/");
        let relativePath = filePath;
        if (pathParts.length > 1 && pathParts[0] === assetId) {
            relativePath = pathParts.slice(1).join("/");
        }

        // Encode the path for URL
        const encodedPath = encodeURIComponent(relativePath);

        // Build URL with version query parameter
        const url = `/databases/${databaseId}/assets/${assetId}/file/${encodedPath}?version=${encodeURIComponent(
            versionId
        )}`;

        // Navigate with state for fast loading, URL for bookmarking/sharing
        navigate(url, {
            state: {
                filename: fileName,
                key: filePath,
                isDirectory: false,
                versionId: versionId,
                isArchived: version?.isArchived,
            },
        });
    };

    // Handle revert version
    const handleRevertVersion = (versionId: string) => {
        setSelectedVersionForRevert(versionId);
        setShowRevertModal(true);
    };

    // Confirm revert
    const confirmRevert = async () => {
        if (!selectedVersionForRevert) return;

        setRevertLoading(true);

        try {
            const [success, response] = await revertFileVersion({
                databaseId,
                assetId,
                filePath,
                versionId: selectedVersionForRevert,
            });

            if (success) {
                setShowRevertModal(false);
                setSelectedVersionForRevert("");

                // Refresh versions list
                await loadFileVersions();

                // Call parent refresh callback if provided
                if (onVersionRevert) {
                    onVersionRevert();
                }

                setError(null);
            } else {
                setError(response || "Failed to revert file version");
            }
        } catch (err: any) {
            setError(err.message || "An error occurred while reverting the file");
        } finally {
            setRevertLoading(false);
        }
    };

    // Table column definitions
    const columnDefinitions = [
        {
            id: "version",
            header: "Version",
            cell: (item: FileVersion) => (
                <div className="version-cell">
                    <span className="version-id">{item.versionId}</span>
                    {item.isLatest && (
                        <span style={{ marginLeft: "12px" }}>
                            <StatusIndicator type="success">Latest</StatusIndicator>
                        </span>
                    )}
                    {currentVersionId === item.versionId && (
                        <span style={{ marginLeft: "12px" }}>
                            <StatusIndicator type="info">Viewing</StatusIndicator>
                        </span>
                    )}
                    {item.isArchived && (
                        <span style={{ marginLeft: "12px" }}>
                            <StatusIndicator type="error">Archived</StatusIndicator>
                        </span>
                    )}
                    {item.currentAssetVersionFileVersionMismatch && (
                        <span style={{ marginLeft: "12px" }}>
                            <StatusIndicator type="warning">
                                {`Not Included in Current ${Synonyms.Asset} Version`}
                            </StatusIndicator>
                        </span>
                    )}
                </div>
            ),
            sortingField: "versionId",
        },
        {
            id: "assetVersions",
            header: `${Synonyms.Asset} Versions`,
            cell: (item: FileVersion) => {
                if (!item.assetVersionIds || item.assetVersionIds.length === 0) {
                    return <Box color="text-status-inactive">--</Box>;
                }
                return (
                    <SpaceBetween direction="horizontal" size="xxs">
                        {item.assetVersionIds.map((av) => (
                            <span
                                key={av.id}
                                title={av.label}
                                style={{
                                    display: "inline-block",
                                    padding: "1px 6px",
                                    fontSize: "12px",
                                    borderRadius: "3px",
                                    backgroundColor:
                                        av.id === assetVersionId ? "#0972d3" : "#e9ebed",
                                    color: av.id === assetVersionId ? "#fff" : "#414d5c",
                                    cursor: "default",
                                }}
                            >
                                {av.label}
                            </span>
                        ))}
                    </SpaceBetween>
                );
            },
            width: 150,
        },
        {
            id: "date",
            header: "Date Created",
            cell: (item: FileVersion) => formatDate(item.lastModified),
            sortingField: "lastModified",
        },
        {
            id: "change",
            header: "Change Source",
            cell: (item: FileVersion) => {
                const changeSourceLabel = getChangeSourceLabel(item.changeSource);

                // Build the list of populated provenance detail rows. Each field is
                // independent — any one of them may be present without the others.
                const details: { label: string; value: string }[] = [];
                if (changeSourceLabel) {
                    details.push({ label: "Source", value: changeSourceLabel });
                }
                if (item.changeUserId) {
                    details.push({ label: "User", value: item.changeUserId });
                }
                if (item.changeWorkflowId) {
                    details.push({ label: "Workflow", value: item.changeWorkflowId });
                }
                if (item.changeWorkflowExecutionId) {
                    details.push({
                        label: "Workflow Execution",
                        value: item.changeWorkflowExecutionId,
                    });
                }

                // "Changed From" grouping for asset-copy provenance
                const changedFrom: string[] = [];
                if (item.changeDatabaseIdFrom) {
                    changedFrom.push(`${Synonyms.Database}: ${item.changeDatabaseIdFrom}`);
                }
                if (item.changeAssetIdFrom) {
                    changedFrom.push(`${Synonyms.Asset}: ${item.changeAssetIdFrom}`);
                }
                if (item.changeAssetFilePathFrom) {
                    changedFrom.push(`Path: ${item.changeAssetFilePathFrom}`);
                }
                if (item.changeAssetFileVersionFrom) {
                    changedFrom.push(`Version: ${item.changeAssetFileVersionFrom}`);
                }

                // No provenance recorded at all (e.g. a legacy version).
                if (details.length === 0 && changedFrom.length === 0) {
                    return <Box color="text-status-inactive">--</Box>;
                }

                // Primary inline text is the change source when known; otherwise fall
                // back to the acting user so the cell still surfaces who changed the
                // file, and finally to a neutral placeholder.
                const primaryText = changeSourceLabel || item.changeUserId || "Unknown";

                // The richer provenance (workflow / changed-from) is revealed in a
                // Popover so the row height stays consistent. Source and user are shown
                // inline directly.
                const hasExtraDetail =
                    Boolean(item.changeWorkflowId) ||
                    Boolean(item.changeWorkflowExecutionId) ||
                    changedFrom.length > 0;

                // The user is shown as a secondary muted line only when it is not
                // already the primary text (avoids duplicating it when the source is
                // unknown and the user became the primary text).
                const showUserSecondary =
                    Boolean(item.changeUserId) && primaryText !== item.changeUserId;

                const primaryContent = hasExtraDetail ? (
                    <Popover
                        dismissButton={false}
                        position="top"
                        size="medium"
                        triggerType="custom"
                        content={
                            <SpaceBetween direction="vertical" size="xs">
                                {details.map((d) => (
                                    <div key={d.label}>
                                        <Box variant="awsui-key-label">{d.label}</Box>
                                        <Box>{d.value}</Box>
                                    </div>
                                ))}
                                {changedFrom.length > 0 && (
                                    <div>
                                        <Box variant="awsui-key-label">From</Box>
                                        {changedFrom.map((cf) => (
                                            <Box key={cf}>{cf}</Box>
                                        ))}
                                    </div>
                                )}
                            </SpaceBetween>
                        }
                    >
                        <Button variant="inline-link" iconName="status-info" iconAlign="right">
                            {primaryText}
                        </Button>
                    </Popover>
                ) : (
                    <Box>{primaryText}</Box>
                );

                return (
                    <SpaceBetween direction="vertical" size="xxxs">
                        {primaryContent}
                        {showUserSecondary && (
                            <Box fontSize="body-s" color="text-status-inactive">
                                {item.changeUserId}
                            </Box>
                        )}
                    </SpaceBetween>
                );
            },
        },
        {
            id: "size",
            header: "File Size",
            cell: (item: FileVersion) => formatFileSize(item.size),
            sortingField: "size",
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: FileVersion) => (
                <SpaceBetween direction="horizontal" size="xs">
                    <Button
                        variant="link"
                        onClick={() => handleDownloadVersion(item.versionId)}
                        disabled={item.isArchived}
                    >
                        Download
                    </Button>
                    {currentVersionId !== item.versionId && (
                        <Button
                            variant="link"
                            onClick={() => handleViewVersion(item.versionId)}
                            disabled={item.isArchived}
                        >
                            View
                        </Button>
                    )}
                    {!item.isLatest && !item.isArchived && (
                        <Button variant="link" onClick={() => handleRevertVersion(item.versionId)}>
                            Revert To
                        </Button>
                    )}
                </SpaceBetween>
            ),
        },
    ];

    if (!visible) {
        return null;
    }

    return (
        <>
            <SpaceBetween direction="vertical" size="l">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                {loading ? (
                    <Box textAlign="center" padding="l">
                        <Spinner size="large" />
                        <div>Loading file versions...</div>
                    </Box>
                ) : versions.length === 0 ? (
                    <Box textAlign="center" padding="l">
                        <div>No version history available for this file.</div>
                    </Box>
                ) : (
                    <>
                        <Table
                            columnDefinitions={columnDefinitions}
                            items={currentVersions}
                            loadingText="Loading versions"
                            sortingDisabled={false}
                            empty={
                                <Box textAlign="center" color="inherit">
                                    <div>No versions found</div>
                                </Box>
                            }
                            header={
                                <Header
                                    counter={`(${versions.length})`}
                                    description="File version history sorted by date (newest first)"
                                >
                                    Versions
                                </Header>
                            }
                        />

                        <Box textAlign="center">
                            <Pagination
                                currentPageIndex={currentPage}
                                pagesCount={totalPages}
                                onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
                            />
                        </Box>
                    </>
                )}
            </SpaceBetween>

            <RevertConfirmationModal
                visible={showRevertModal}
                onDismiss={() => {
                    setShowRevertModal(false);
                    setSelectedVersionForRevert("");
                }}
                onConfirm={confirmRevert}
                versionId={selectedVersionForRevert}
                fileName={fileName}
                isLoading={revertLoading}
            />
        </>
    );
};

export default FileVersionsTable;
