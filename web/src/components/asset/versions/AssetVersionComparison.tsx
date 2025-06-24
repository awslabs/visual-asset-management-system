import React, { useState, useEffect, useContext, useMemo } from "react";
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    Alert,
    Spinner,
    Badge,
    Grid,
    ColumnLayout,
    Link,
    Toggle,
    Table,
    TextFilter,
    Pagination,
    CollectionPreferences,
} from "@cloudscape-design/components";
import { useNavigate, useParams } from "react-router";

import {
    fetchAssetVersion,
    fetchAssetS3Files,
    compareAssetVersions,
} from "../../../services/AssetVersionService";
import { downloadAsset } from "../../../services/APIService";
import { AssetVersionContext, AssetVersion } from "./AssetVersionManager";

// TypeScript interfaces - using imported AssetVersion from AssetVersionManager

interface FileVersion {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    exists: boolean;
    isPermanentlyDeleted?: boolean;
    isLatestVersionArchived?: boolean;
    size?: number;
    lastModified?: string;
    etag?: string;
}

interface AssetVersionDetails {
    assetId: string;
    assetVersionId: string;
    dateCreated: string;
    comment?: string;
    files: FileVersion[];
    createdBy?: string;
}

interface ComparisonProps {
    databaseId: string;
    assetId: string;
    version1: AssetVersion;
    version2?: AssetVersion; // Make version2 optional for "Compare with Current" mode
    compareWithCurrent?: boolean; // Flag to indicate comparison with current files
    onClose: () => void;
}

// Enhanced version for integration with AssetVersionManager
interface EnhancedComparisonProps {
    onClose: () => void;
}

interface FileComparison {
    relativeKey: string;
    status: "added" | "removed" | "modified" | "unchanged";
    version1File?: FileVersion;
    version2File?: FileVersion;
}

// Shared utility functions
// Get status badge
const getStatusBadge = (status: string) => {
    switch (status) {
        case "added":
            return <Badge color="green">Added</Badge>;
        case "removed":
            return <Badge color="red">Removed</Badge>;
        case "modified":
            return <Badge color="blue">Modified</Badge>;
        case "unchanged":
            return <Badge color="grey">Unchanged</Badge>;
        default:
            return <Badge>{status}</Badge>;
    }
};

// Get status icon
const getStatusIcon = (status: string) => {
    switch (status) {
        case "added":
            return <span style={{ color: "#037f0c", marginRight: "4px" }}>➕</span>;
        case "removed":
            return <span style={{ color: "#d91515", marginRight: "4px" }}>➖</span>;
        case "modified":
            return <span style={{ color: "#0972d3", marginRight: "4px" }}>✏️</span>;
        case "unchanged":
            return <span style={{ color: "#5f6b7a", marginRight: "4px" }}>✓</span>;
        default:
            return null;
    }
};

// Format file size - shared utility function
const formatFileSize = (size?: number): string => {
    if (size === undefined) return "N/A";
    if (size === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(size) / Math.log(1024));
    return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
};

// Format date - shared utility function
const formatDate = (dateString?: string): string => {
    if (!dateString) return "N/A";
    try {
        const date = new Date(dateString);
        return date.toLocaleString();
    } catch (e) {
        return dateString;
    }
};

// Original standalone component for comparing with current files
const AssetVersionComparison: React.FC<ComparisonProps> = ({
    databaseId,
    assetId,
    version1,
    version2,
    compareWithCurrent = false,
    onClose,
}) => {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [comparison, setComparison] = useState<any | null>(null);
    const [currentFiles, setCurrentFiles] = useState<any[]>([]);

    // State for table pagination and filtering
    const [comparisonFilterText, setComparisonFilterText] = useState<string>("");
    const [comparisonCurrentPage, setComparisonCurrentPage] = useState<number>(1);
    const [comparisonPageSize, setComparisonPageSize] = useState<number>(10);
    const [showArchivedFiles, setShowArchivedFiles] = useState(false);
    const [showMismatchedOnly, setShowMismatchedOnly] = useState(false);

    // State for table preferences
    const [preferences, setPreferences] = useState<{
        pageSize: number;
        visibleContent: string[];
    }>({
        pageSize: comparisonPageSize,
        visibleContent: [
            "status",
            "fileName",
            "path",
            "size1",
            "size2",
            "lastModified1",
            "lastModified2",
            "actions",
        ],
    });

    // Load comparison data
    useEffect(() => {
        const loadComparison = async () => {
            if (!databaseId || !assetId || !version1) {
                setError("Missing required parameters for comparison");
                setLoading(false);
                return;
            }

            try {
                setLoading(true);
                setError(null);

                if (compareWithCurrent) {
                    // Load current files
                    const [success, files] = await fetchAssetS3Files({
                        databaseId,
                        assetId,
                        includeArchived: false,
                    });

                    if (success && files) {
                        setCurrentFiles(files);

                        // Load version details
                        const [versionSuccess, versionDetails] = await fetchAssetVersion({
                            databaseId,
                            assetId,
                            assetVersionId: version1.Version,
                        });

                        if (versionSuccess && versionDetails) {
                            // Create a comparison object manually
                            const fileComparisons: FileComparison[] = [];
                            const allKeys = new Set<string>();

                            // Add all keys from version files
                            versionDetails.files.forEach((file: FileVersion) => {
                                allKeys.add(file.relativeKey);
                            });

                            // Add all keys from current files
                            files.forEach((file: any) => {
                                allKeys.add(file.relativeKey);
                            });

                            // Create comparison objects
                            allKeys.forEach((key) => {
                                const versionFile = versionDetails.files.find(
                                    (f: FileVersion) => f.relativeKey === key
                                );
                                const currentFile = files.find((f: any) => f.relativeKey === key);

                                let status: "added" | "removed" | "modified" | "unchanged" =
                                    "unchanged";

                                if (versionFile && !currentFile) {
                                    status = "removed";
                                } else if (!versionFile && currentFile) {
                                    status = "added";
                                } else if (versionFile && currentFile) {
                                    // Compare etags or other properties to determine if modified
                                    if (versionFile.etag !== currentFile.etag) {
                                        status = "modified";
                                    }
                                }

                                fileComparisons.push({
                                    relativeKey: key,
                                    status,
                                    version1File: versionFile,
                                    version2File: currentFile,
                                });
                            });

                            // Create summary
                            const added = fileComparisons.filter(
                                (f) => f.status === "added"
                            ).length;
                            const removed = fileComparisons.filter(
                                (f) => f.status === "removed"
                            ).length;
                            const modified = fileComparisons.filter(
                                (f) => f.status === "modified"
                            ).length;
                            const unchanged = fileComparisons.filter(
                                (f) => f.status === "unchanged"
                            ).length;

                            setComparison({
                                fileComparisons,
                                summary: {
                                    total: fileComparisons.length,
                                    added,
                                    removed,
                                    modified,
                                    unchanged,
                                },
                            });
                        } else {
                            setError("Failed to load version details");
                        }
                    } else {
                        setError("Failed to load current files");
                    }
                } else if (version2) {
                    // Compare two versions
                    const [success, result] = await compareAssetVersions({
                        databaseId,
                        assetId,
                        version1Id: version1.Version,
                        version2Id: version2.Version,
                    });

                    if (success && result) {
                        setComparison(result);
                    } else {
                        setError("Failed to compare versions");
                    }
                } else {
                    setError("Missing second version for comparison");
                }
            } catch (err) {
                console.error("Error comparing versions:", err);
                setError("Error comparing versions");
            } finally {
                setLoading(false);
            }
        };

        loadComparison();
    }, [databaseId, assetId, version1, version2, compareWithCurrent]);

    // Filter file comparisons based on settings
    const filteredComparisons = useMemo(() => {
        if (!comparison || !comparison.fileComparisons) return [];

        return comparison.fileComparisons.filter((file: FileComparison) => {
            // Filter out archived files if not showing them
            if (
                !showArchivedFiles &&
                ((file.version1File && file.version1File.isArchived) ||
                    (file.version2File && file.version2File.isArchived))
            ) {
                return false;
            }

            // Filter to only show mismatched files if that option is selected
            if (showMismatchedOnly && file.status === "unchanged") {
                return false;
            }

            return true;
        });
    }, [comparison, showArchivedFiles, showMismatchedOnly]);

    // Apply additional filtering for search text
    const searchFilteredComparisons = useMemo(() => {
        if (!comparisonFilterText.trim() || !filteredComparisons) {
            return filteredComparisons;
        }

        const lowerFilter = comparisonFilterText.toLowerCase();
        return filteredComparisons.filter(
            (item: FileComparison) =>
                item.relativeKey.toLowerCase().includes(lowerFilter) ||
                item.status.toLowerCase().includes(lowerFilter)
        );
    }, [filteredComparisons, comparisonFilterText]);

    // Calculate total files and paginated files
    const totalComparisonFiles = searchFilteredComparisons?.length || 0;
    const paginatedComparisons = useMemo(() => {
        if (!searchFilteredComparisons) return [];
        const startIndex = (comparisonCurrentPage - 1) * comparisonPageSize;
        return searchFilteredComparisons.slice(startIndex, startIndex + comparisonPageSize);
    }, [searchFilteredComparisons, comparisonCurrentPage, comparisonPageSize]);

    // Render loading state
    if (loading) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Comparing Versions
                    </Header>
                }
            >
                <Box textAlign="center" padding="l">
                    <Spinner size="large" />
                    <div>Loading comparison data...</div>
                </Box>
            </Container>
        );
    }

    // Render error state
    if (error) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Version Comparison Error
                    </Header>
                }
            >
                <Alert type="error">{error}</Alert>
            </Container>
        );
    }

    // Render empty state
    if (!comparison || !filteredComparisons.length) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Version Comparison
                    </Header>
                }
            >
                <Box textAlign="center" padding="l">
                    <div>No comparison data available or no files match the current filters.</div>
                </Box>
            </Container>
        );
    }

    // Handle view file
    const handleViewFile = (file: FileVersion | undefined) => {
        // Don't allow viewing permanently deleted files or undefined files
        if (!file || file.isPermanentlyDeleted) {
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

    // Table columns
    const columns = [
        {
            id: "status",
            header: "Status",
            cell: (item: FileComparison) => (
                <Box>
                    <div style={{ display: "flex", alignItems: "center" }}>
                        {getStatusIcon(item.status)}
                        {getStatusBadge(item.status)}
                    </div>
                </Box>
            ),
            sortingField: "status",
        },
        {
            id: "fileName",
            header: "File Name",
            cell: (item: FileComparison) => {
                const fileName = item.relativeKey.split("/").pop() || item.relativeKey;
                return (
                    <Box>
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                            }}
                        >
                            <span>{fileName}</span>
                        </div>
                    </Box>
                );
            },
            sortingField: "relativeKey",
        },
        {
            id: "path",
            header: "Path",
            cell: (item: FileComparison) => (
                <Box>
                    <div
                        style={{
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
            id: "size1",
            header: `Size (v${version1.Version})`,
            cell: (item: FileComparison) => (
                <Box>{item.version1File ? formatFileSize(item.version1File.size) : "N/A"}</Box>
            ),
            sortingField: "version1File.size",
        },
        {
            id: "size2",
            header: compareWithCurrent ? "Size (Current)" : `Size (v${version2?.Version})`,
            cell: (item: FileComparison) => (
                <Box>{item.version2File ? formatFileSize(item.version2File.size) : "N/A"}</Box>
            ),
            sortingField: "version2File.size",
        },
        {
            id: "lastModified1",
            header: `Last Modified (v${version1.Version})`,
            cell: (item: FileComparison) => (
                <Box>
                    {item.version1File ? formatDate(item.version1File.lastModified) : "N/A"}
                    {item.version1File?.isArchived && <Badge color="red">Archived</Badge>}
                </Box>
            ),
            sortingField: "version1File.lastModified",
        },
        {
            id: "lastModified2",
            header: compareWithCurrent
                ? "Last Modified (Current)"
                : `Last Modified (v${version2?.Version})`,
            cell: (item: FileComparison) => (
                <Box>
                    {item.version2File ? formatDate(item.version2File.lastModified) : "N/A"}
                    {item.version2File?.isArchived && <Badge color="red">Archived</Badge>}
                </Box>
            ),
            sortingField: "version2File.lastModified",
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: FileComparison) => {
                // Only show view/compare if the file exists in at least one version
                if (!item.version1File && !item.version2File) {
                    return <Box>No actions available</Box>;
                }

                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        {item.version1File && (
                            <Button
                                iconName="file"
                                variant="normal"
                                disabled={item.version1File.isPermanentlyDeleted}
                                onClick={() => handleViewFile(item.version1File)}
                            >
                                View v{version1.Version}
                            </Button>
                        )}
                        {item.version2File && (
                            <Button
                                iconName="file"
                                variant="normal"
                                disabled={item.version2File.isPermanentlyDeleted}
                                onClick={() => handleViewFile(item.version2File)}
                            >
                                {compareWithCurrent ? "View Current" : `View v${version2?.Version}`}
                            </Button>
                        )}
                        {item.version1File && item.version2File && item.status === "modified" && (
                            <Button iconName="copy" variant="normal">
                                Compare
                            </Button>
                        )}
                    </SpaceBetween>
                );
            },
        },
    ];

    // Render comparison results
    return (
        <Container
            header={
                <Header
                    variant="h2"
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        </SpaceBetween>
                    }
                >
                    {compareWithCurrent
                        ? `Comparing Version ${version1.Version} with Current Files`
                        : `Comparing Version ${version1.Version} with Version ${version2?.Version}`}
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Summary */}
                <Container header={<Header variant="h3">Comparison Summary</Header>}>
                    <ColumnLayout columns={2}>
                        <div>
                            <SpaceBetween direction="vertical" size="s">
                                <Box variant="h4">Version Information</Box>
                                <div>
                                    <strong>First Version:</strong> v{version1.Version} (
                                    {new Date(version1.DateModified || "").toLocaleDateString()})
                                </div>
                                <div>
                                    <strong>Second Version:</strong>{" "}
                                    {compareWithCurrent
                                        ? "Current Files"
                                        : `v${version2?.Version} (${new Date(
                                              version2?.DateModified || ""
                                          ).toLocaleDateString()})`}
                                </div>
                                {comparison.summary && (
                                    <div>
                                        <strong>Total Files:</strong> {comparison.summary.total}
                                    </div>
                                )}
                            </SpaceBetween>
                        </div>

                        <div>
                            <SpaceBetween direction="vertical" size="s">
                                <Box variant="h4">Changes</Box>
                                {comparison.summary && (
                                    <>
                                        <div>
                                            <span style={{ color: "#037f0c", marginRight: "4px" }}>
                                                ➕
                                            </span>
                                            <strong>Added:</strong> {comparison.summary.added}
                                        </div>
                                        <div>
                                            <span style={{ color: "#d91515", marginRight: "4px" }}>
                                                ➖
                                            </span>
                                            <strong>Removed:</strong> {comparison.summary.removed}
                                        </div>
                                        <div>
                                            <span style={{ color: "#0972d3", marginRight: "4px" }}>
                                                ✏️
                                            </span>
                                            <strong>Modified:</strong> {comparison.summary.modified}
                                        </div>
                                        <div>
                                            <span style={{ color: "#5f6b7a", marginRight: "4px" }}>
                                                ✓
                                            </span>
                                            <strong>Unchanged:</strong>{" "}
                                            {comparison.summary.unchanged}
                                        </div>
                                    </>
                                )}
                            </SpaceBetween>
                        </div>
                    </ColumnLayout>
                </Container>

                {/* Filter options */}
                <SpaceBetween direction="horizontal" size="xs">
                    <Toggle
                        onChange={({ detail }: { detail: { checked: boolean } }) =>
                            setShowArchivedFiles(detail.checked)
                        }
                        checked={showArchivedFiles}
                    >
                        Show archived files
                    </Toggle>
                    <Toggle
                        onChange={({ detail }: { detail: { checked: boolean } }) =>
                            setShowMismatchedOnly(detail.checked)
                        }
                        checked={showMismatchedOnly}
                    >
                        Show only changed files
                    </Toggle>
                </SpaceBetween>

                {/* File comparison table */}
                <Table
                    columnDefinitions={columns}
                    items={paginatedComparisons}
                    loading={loading}
                    loadingText="Loading comparison data"
                    empty={
                        <Box textAlign="center" padding="l">
                            <div>No files match the current filter criteria</div>
                        </Box>
                    }
                    header={<Header counter={`(${totalComparisonFiles})`}>File Comparison</Header>}
                    filter={
                        <TextFilter
                            filteringText={comparisonFilterText}
                            filteringPlaceholder="Find files"
                            filteringAriaLabel="Filter files"
                            onChange={({ detail }) => setComparisonFilterText(detail.filteringText)}
                        />
                    }
                    pagination={
                        <Pagination
                            currentPageIndex={comparisonCurrentPage}
                            pagesCount={Math.max(
                                1,
                                Math.ceil(totalComparisonFiles / comparisonPageSize)
                            )}
                            onChange={({ detail }) =>
                                setComparisonCurrentPage(detail.currentPageIndex)
                            }
                            ariaLabels={{
                                nextPageLabel: "Next page",
                                previousPageLabel: "Previous page",
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of ${Math.max(
                                        1,
                                        Math.ceil(totalComparisonFiles / comparisonPageSize)
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
                                if (
                                    detail.pageSize !== undefined &&
                                    detail.pageSize !== comparisonPageSize
                                ) {
                                    setComparisonPageSize(detail.pageSize);
                                    setComparisonCurrentPage(1); // Reset to first page when changing page size
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
                                            { id: "status", label: "Status" },
                                            { id: "fileName", label: "File Name" },
                                            { id: "path", label: "Path" },
                                            { id: "size1", label: `Size (v${version1.Version})` },
                                            {
                                                id: "size2",
                                                label: compareWithCurrent
                                                    ? "Size (Current)"
                                                    : `Size (v${version2?.Version})`,
                                            },
                                            {
                                                id: "lastModified1",
                                                label: `Last Modified (v${version1.Version})`,
                                            },
                                            {
                                                id: "lastModified2",
                                                label: compareWithCurrent
                                                    ? "Last Modified (Current)"
                                                    : `Last Modified (v${version2?.Version})`,
                                            },
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
            </SpaceBetween>
        </Container>
    );
};

// Enhanced version for integration with AssetVersionManager
export const EnhancedAssetVersionComparison: React.FC<EnhancedComparisonProps> = ({ onClose }) => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    const navigate = useNavigate();
    const context = useContext(AssetVersionContext);

    if (!context) {
        throw new Error(
            "EnhancedAssetVersionComparison must be used within an AssetVersionContext.Provider"
        );
    }

    const {
        versionToCompare,
        selectedVersion,
        showArchivedFiles,
        setShowArchivedFiles,
        showMismatchedOnly,
        setShowMismatchedOnly,
    } = context;

    // All state hooks must be declared at the top level
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [comparison, setComparison] = useState<any | null>(null);

    // State for table pagination and filtering - moved to top level
    const [comparisonFilterText, setComparisonFilterText] = useState<string>("");
    const [comparisonCurrentPage, setComparisonCurrentPage] = useState<number>(1);
    const [comparisonPageSize, setComparisonPageSize] = useState<number>(10);

    // State for table preferences - moved to top level
    const [preferences, setPreferences] = useState<{
        pageSize: number;
        visibleContent: string[];
    }>({
        pageSize: comparisonPageSize,
        visibleContent: [
            "status",
            "fileName",
            "path",
            "size1",
            "size2",
            "lastModified1",
            "lastModified2",
            "actions",
        ],
    });

    // Fetch comparison data
    useEffect(() => {
        const fetchComparison = async () => {
            if (!databaseId || !assetId || !versionToCompare || !selectedVersion) {
                setError("Missing required parameters for comparison");
                setLoading(false);
                return;
            }

            try {
                setLoading(true);
                setError(null);

                const [success, result] = await compareAssetVersions({
                    databaseId,
                    assetId,
                    version1Id: versionToCompare.Version,
                    version2Id: selectedVersion.Version,
                });

                if (success && result) {
                    console.log("Comparison result:", result);
                    setComparison(result);
                } else {
                    setError("Failed to compare versions");
                }
            } catch (err) {
                console.error("Error comparing versions:", err);
                setError("Error comparing versions");
            } finally {
                setLoading(false);
            }
        };

        fetchComparison();
    }, [databaseId, assetId, versionToCompare, selectedVersion]);

    // Filter file comparisons based on settings
    const filteredComparisons = useMemo(() => {
        if (!comparison || !comparison.fileComparisons) return [];

        return comparison.fileComparisons.filter((file: FileComparison) => {
            // Filter out archived files if not showing them
            if (
                !showArchivedFiles &&
                ((file.version1File && file.version1File.isArchived) ||
                    (file.version2File && file.version2File.isArchived))
            ) {
                return false;
            }

            // Filter to only show mismatched files if that option is selected
            if (showMismatchedOnly && file.status === "unchanged") {
                return false;
            }

            return true;
        });
    }, [comparison, showArchivedFiles, showMismatchedOnly]);

    // Apply additional filtering for search text - moved to top level
    const searchFilteredComparisons = useMemo(() => {
        if (!comparisonFilterText.trim() || !filteredComparisons) {
            return filteredComparisons;
        }

        const lowerFilter = comparisonFilterText.toLowerCase();
        return filteredComparisons.filter(
            (item: FileComparison) =>
                item.relativeKey.toLowerCase().includes(lowerFilter) ||
                item.status.toLowerCase().includes(lowerFilter)
        );
    }, [filteredComparisons, comparisonFilterText]);

    // Calculate total files and paginated files - moved to top level
    const totalComparisonFiles = searchFilteredComparisons?.length || 0;
    const paginatedComparisons = useMemo(() => {
        if (!searchFilteredComparisons) return [];
        const startIndex = (comparisonCurrentPage - 1) * comparisonPageSize;
        return searchFilteredComparisons.slice(startIndex, startIndex + comparisonPageSize);
    }, [searchFilteredComparisons, comparisonCurrentPage, comparisonPageSize]);

    // Render loading state
    if (loading) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Comparing Versions
                    </Header>
                }
            >
                <Box textAlign="center" padding="l">
                    <Spinner size="large" />
                    <div>Loading comparison data...</div>
                </Box>
            </Container>
        );
    }

    // Render error state
    if (error) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Version Comparison Error
                    </Header>
                }
            >
                <Alert type="error">{error}</Alert>
            </Container>
        );
    }

    // Render empty state
    if (!comparison || !filteredComparisons.length) {
        return (
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        }
                    >
                        Version Comparison
                    </Header>
                }
            >
                <Box textAlign="center" padding="l">
                    <div>No comparison data available or no files match the current filters.</div>
                </Box>
            </Container>
        );
    }

    // Handle view file
    const handleViewFile = (file: FileVersion | undefined) => {
        // Don't allow viewing permanently deleted files or undefined files
        if (!file || file.isPermanentlyDeleted) {
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

    // Table columns
    const columns = [
        {
            id: "status",
            header: "Status",
            cell: (item: FileComparison) => (
                <Box>
                    <div style={{ display: "flex", alignItems: "center" }}>
                        {getStatusIcon(item.status)}
                        {getStatusBadge(item.status)}
                    </div>
                </Box>
            ),
            sortingField: "status",
        },
        {
            id: "fileName",
            header: "File Name",
            cell: (item: FileComparison) => {
                const fileName = item.relativeKey.split("/").pop() || item.relativeKey;
                return (
                    <Box>
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                            }}
                        >
                            <span>{fileName}</span>
                        </div>
                    </Box>
                );
            },
            sortingField: "relativeKey",
        },
        {
            id: "path",
            header: "Path",
            cell: (item: FileComparison) => (
                <Box>
                    <div
                        style={{
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
            id: "size1",
            header: `Size (v${versionToCompare?.Version})`,
            cell: (item: FileComparison) => (
                <Box>{item.version1File ? formatFileSize(item.version1File.size) : "N/A"}</Box>
            ),
            sortingField: "version1File.size",
        },
        {
            id: "size2",
            header: `Size (v${selectedVersion?.Version})`,
            cell: (item: FileComparison) => (
                <Box>{item.version2File ? formatFileSize(item.version2File.size) : "N/A"}</Box>
            ),
            sortingField: "version2File.size",
        },
        {
            id: "lastModified1",
            header: `Last Modified (v${versionToCompare?.Version})`,
            cell: (item: FileComparison) => (
                <Box>
                    {item.version1File ? formatDate(item.version1File.lastModified) : "N/A"}
                    {item.version1File?.isArchived && <Badge color="red">Archived</Badge>}
                </Box>
            ),
            sortingField: "version1File.lastModified",
        },
        {
            id: "lastModified2",
            header: `Last Modified (v${selectedVersion?.Version})`,
            cell: (item: FileComparison) => (
                <Box>
                    {item.version2File ? formatDate(item.version2File.lastModified) : "N/A"}
                    {item.version2File?.isArchived && <Badge color="red">Archived</Badge>}
                </Box>
            ),
            sortingField: "version2File.lastModified",
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: FileComparison) => {
                // Only show view/compare if the file exists in at least one version
                if (!item.version1File && !item.version2File) {
                    return <Box>No actions available</Box>;
                }

                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        {item.version1File && (
                            <Button
                                iconName="file"
                                variant="normal"
                                disabled={item.version1File.isPermanentlyDeleted}
                                onClick={() => handleViewFile(item.version1File)}
                            >
                                View v{versionToCompare?.Version}
                            </Button>
                        )}
                        {item.version2File && (
                            <Button
                                iconName="file"
                                variant="normal"
                                disabled={item.version2File.isPermanentlyDeleted}
                                onClick={() => handleViewFile(item.version2File)}
                            >
                                View v{selectedVersion?.Version}
                            </Button>
                        )}
                        {item.version1File && item.version2File && item.status === "modified" && (
                            <Button iconName="copy" variant="normal">
                                Compare
                            </Button>
                        )}
                    </SpaceBetween>
                );
            },
        },
    ];

    // Render comparison results
    return (
        <Container
            header={
                <Header
                    variant="h2"
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={onClose} variant="normal">
                                Close Comparison
                            </Button>
                        </SpaceBetween>
                    }
                >
                    Comparing Version {versionToCompare?.Version} with Version{" "}
                    {selectedVersion?.Version}
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Summary */}
                <Container header={<Header variant="h3">Comparison Summary</Header>}>
                    <ColumnLayout columns={2}>
                        <div>
                            <SpaceBetween direction="vertical" size="s">
                                <Box variant="h4">Version Information</Box>
                                <div>
                                    <strong>First Version:</strong> v{versionToCompare?.Version} (
                                    {new Date(
                                        versionToCompare?.DateModified || ""
                                    ).toLocaleDateString()}
                                    )
                                </div>
                                <div>
                                    <strong>Second Version:</strong> v{selectedVersion?.Version} (
                                    {new Date(
                                        selectedVersion?.DateModified || ""
                                    ).toLocaleDateString()}
                                    )
                                </div>
                                {comparison.summary && (
                                    <div>
                                        <strong>Total Files:</strong> {comparison.summary.total}
                                    </div>
                                )}
                            </SpaceBetween>
                        </div>

                        <div>
                            <SpaceBetween direction="vertical" size="s">
                                <Box variant="h4">Changes</Box>
                                {comparison.summary && (
                                    <>
                                        <div>
                                            <span style={{ color: "#037f0c", marginRight: "4px" }}>
                                                ➕
                                            </span>
                                            <strong>Added:</strong> {comparison.summary.added}
                                        </div>
                                        <div>
                                            <span style={{ color: "#d91515", marginRight: "4px" }}>
                                                ➖
                                            </span>
                                            <strong>Removed:</strong> {comparison.summary.removed}
                                        </div>
                                        <div>
                                            <span style={{ color: "#0972d3", marginRight: "4px" }}>
                                                ✏️
                                            </span>
                                            <strong>Modified:</strong> {comparison.summary.modified}
                                        </div>
                                        <div>
                                            <span style={{ color: "#5f6b7a", marginRight: "4px" }}>
                                                ✓
                                            </span>
                                            <strong>Unchanged:</strong>{" "}
                                            {comparison.summary.unchanged}
                                        </div>
                                    </>
                                )}
                            </SpaceBetween>
                        </div>
                    </ColumnLayout>
                </Container>

                {/* Filter options */}
                <SpaceBetween direction="horizontal" size="xs">
                    <Toggle
                        onChange={({ detail }: { detail: { checked: boolean } }) =>
                            setShowArchivedFiles(detail.checked)
                        }
                        checked={showArchivedFiles}
                    >
                        Show archived files
                    </Toggle>
                    <Toggle
                        onChange={({ detail }: { detail: { checked: boolean } }) =>
                            setShowMismatchedOnly(detail.checked)
                        }
                        checked={showMismatchedOnly}
                    >
                        Show only changed files
                    </Toggle>
                </SpaceBetween>

                {/* File comparison table */}
                <Table
                    columnDefinitions={columns}
                    items={paginatedComparisons}
                    loading={loading}
                    loadingText="Loading comparison data"
                    empty={
                        <Box textAlign="center" padding="l">
                            <div>No files match the current filter criteria</div>
                        </Box>
                    }
                    header={<Header counter={`(${totalComparisonFiles})`}>File Comparison</Header>}
                    filter={
                        <TextFilter
                            filteringText={comparisonFilterText}
                            filteringPlaceholder="Find files"
                            filteringAriaLabel="Filter files"
                            onChange={({ detail }) => setComparisonFilterText(detail.filteringText)}
                        />
                    }
                    pagination={
                        <Pagination
                            currentPageIndex={comparisonCurrentPage}
                            pagesCount={Math.max(
                                1,
                                Math.ceil(totalComparisonFiles / comparisonPageSize)
                            )}
                            onChange={({ detail }) =>
                                setComparisonCurrentPage(detail.currentPageIndex)
                            }
                            ariaLabels={{
                                nextPageLabel: "Next page",
                                previousPageLabel: "Previous page",
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of ${Math.max(
                                        1,
                                        Math.ceil(totalComparisonFiles / comparisonPageSize)
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
                                if (
                                    detail.pageSize !== undefined &&
                                    detail.pageSize !== comparisonPageSize
                                ) {
                                    setComparisonPageSize(detail.pageSize);
                                    setComparisonCurrentPage(1); // Reset to first page when changing page size
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
                                            { id: "status", label: "Status" },
                                            { id: "fileName", label: "File Name" },
                                            { id: "path", label: "Path" },
                                            {
                                                id: "size1",
                                                label: `Size (v${versionToCompare?.Version})`,
                                            },
                                            {
                                                id: "size2",
                                                label: `Size (v${selectedVersion?.Version})`,
                                            },
                                            {
                                                id: "lastModified1",
                                                label: `Last Modified (v${versionToCompare?.Version})`,
                                            },
                                            {
                                                id: "lastModified2",
                                                label: `Last Modified (v${selectedVersion?.Version})`,
                                            },
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
            </SpaceBetween>
        </Container>
    );
};

export default AssetVersionComparison;
