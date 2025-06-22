/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useState, useEffect } from 'react';
import {
    Box,
    Button,
    Pagination,
    SpaceBetween,
    Spinner,
    Table,
    TextFilter,
    Badge,
    Modal,
    Select,
    CollectionPreferences
} from '@cloudscape-design/components';
import { useNavigate, useParams } from 'react-router';
import { AssetVersionContext, AssetVersion, FileVersion } from '../AssetVersionManager';
import { fetchAssetVersion } from '../../../../services/AssetVersionService';

interface AssetVersionListProps {
    onRevertVersion: (version: AssetVersion) => void;
    onVersionSelect: (version: AssetVersion) => void;
}

export const AssetVersionList: React.FC<AssetVersionListProps> = ({ 
    onRevertVersion,
    onVersionSelect
}) => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    const navigate = useNavigate();
    
    // State for download confirmation modal
    const [showDownloadModal, setShowDownloadModal] = useState(false);
    const [downloadVersion, setDownloadVersion] = useState<AssetVersion | null>(null);
    const [downloadDetails, setDownloadDetails] = useState<{
        fileCount: number;
        totalSize: number;
        files: FileVersion[];
    } | null>(null);
    const [downloadLoading, setDownloadLoading] = useState(false);
    
    // State for preferences
    const [preferences, setPreferences] = useState<{
        pageSize: number;
        visibleContent: string[];
    }>({
        pageSize: 10,
        visibleContent: ['version', 'dateModified', 'createdBy', 'comment', 'actions']
    });
    
    // Get context values
    const context = useContext(AssetVersionContext);
    
    if (!context) {
        throw new Error('AssetVersionList must be used within an AssetVersionContext.Provider');
    }
    
    const {
        loading,
        versions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions,
        currentPage,
        pageSize,
        setCurrentPage,
        setPageSize,
        setSelectedVersion,
        compareMode,
        versionToCompare,
        // New properties for enhanced functionality
        versionsForComparison,
        handleVersionSelectionForComparison,
        filterText,
        setFilterText,
        // Additional properties needed for comparison UI
        showComparisonOptions,
        comparisonType
    } = context;
    
    // Debug effect to track re-renders
    useEffect(() => {
        console.log('AssetVersionList - Component re-rendered');
    }, []);
    
    // Update preferences when page size changes
    useEffect(() => {
        if (preferences.pageSize !== pageSize) {
            setPreferences(prev => ({
                ...prev,
                pageSize
            }));
        }
    }, [pageSize]);
    
    // Show download confirmation modal
    const showDownloadConfirmation = async (version: AssetVersion) => {
        setDownloadVersion(version);
        setDownloadLoading(true);
        setShowDownloadModal(true);
        
        try {
            // Fetch the version details to get the files
            const [success, response] = await fetchAssetVersion({
                databaseId: databaseId!,
                assetId: assetId!,
                assetVersionId: `${version.Version}`
            });
            
            if (success && response) {
                // Only include files that are not permanently deleted
                const downloadableFiles = response.files.filter((file: FileVersion) => !file.isPermanentlyDeleted);
                
                // Calculate total size
                const totalSize = downloadableFiles.reduce((sum: number, file: FileVersion) => sum + (file.size || 0), 0);
                
                setDownloadDetails({
                    fileCount: downloadableFiles.length,
                    totalSize: totalSize,
                    files: downloadableFiles
                });
            } else {
                console.error('Failed to fetch version details for download');
                setDownloadDetails({
                    fileCount: 0,
                    totalSize: 0,
                    files: []
                });
            }
        } catch (error) {
            console.error('Error preparing download:', error);
            setDownloadDetails({
                fileCount: 0,
                totalSize: 0,
                files: []
            });
        } finally {
            setDownloadLoading(false);
        }
    };
    
    // Format file size
    const formatFileSize = (size: number): string => {
        if (size === 0) return '0 B';
        
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(size) / Math.log(1024));
        return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
    };
    
    // Handle download assets
    const handleDownloadAssets = async () => {
        if (!downloadVersion || !downloadDetails) return;
        
        try {
            // Define the FileTreeNode interface
            interface FileTreeNode {
                name: string;
                displayName: string;
                relativePath: string;
                keyPrefix: string;
                level: number;
                expanded: boolean;
                subTree: FileTreeNode[];
                isFolder?: boolean;
                size?: number;
                dateCreatedCurrentVersion?: string;
                versionId?: string;
            }
            
            // Create a fileTree structure from the version files
            const fileTree: FileTreeNode = {
                name: `Asset Version v${downloadVersion.Version}`,
                displayName: `Asset Version v${downloadVersion.Version}`,
                relativePath: "/",
                keyPrefix: "/",
                level: 0,
                expanded: true,
                subTree: []
            };
            
            // Add files to the fileTree
            if (downloadDetails.files.length > 0) {
                downloadDetails.files.forEach((file: FileVersion) => {
                    // Create file node
                    const fileName = file.relativeKey.split('/').pop() || file.relativeKey;
                    fileTree.subTree.push({
                        name: fileName,
                        displayName: fileName,
                        relativePath: file.relativeKey,
                        keyPrefix: file.relativeKey,
                        level: 1,
                        expanded: false,
                        subTree: [],
                        isFolder: false,
                        size: file.size,
                        dateCreatedCurrentVersion: file.lastModified,
                        versionId: file.versionId
                    });
                });
            }
            
            // Close the modal
            setShowDownloadModal(false);
            
            // Navigate to the AssetDownload page
            navigate(`/databases/${databaseId}/assets/${assetId}/download`, {
                state: {
                    assetVersionId: `${downloadVersion.Version}`,
                    fileTree: fileTree
                }
            });
        } catch (error) {
            console.error('Error preparing download:', error);
            setShowDownloadModal(false);
        }
    };
    
    // Format date
    const formatDate = (dateString: string): string => {
        try {
            const date = new Date(dateString);
            return date.toLocaleString();
        } catch (e) {
            return dateString;
        }
    };
    
    // Check if a version is selected for comparison
    const isVersionSelectedForComparison = (version: AssetVersion): boolean => {
        return versionsForComparison.some(v => v.Version === version.Version);
    };
    
    // Get the selection index for a version (for two-version comparison)
    const getComparisonSelectionIndex = (version: AssetVersion): number => {
        return versionsForComparison.findIndex(v => v.Version === version.Version);
    };
    
    // Format file count with appropriate label
    const formatFileCount = (count: number): string => {
        return `${count} ${count === 1 ? 'file' : 'files'}`;
    };
    
    // Table columns
    const columns = [
        {
            id: 'compare',
            header: 'Compare',
            cell: (item: AssetVersion) => {
                const isSelected = isVersionSelectedForComparison(item);
                const selectionIndex = getComparisonSelectionIndex(item);
                
                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant={isSelected ? "primary" : "normal"}
                            onClick={(e) => {
                                e.stopPropagation(); // Prevent row selection
                                console.log('AssetVersionList - Select button clicked for version:', item);
                                console.log('AssetVersionList - Current versionsForComparison before selection:', versionsForComparison);
                                
                                // Directly call the function with the version
                                handleVersionSelectionForComparison(item);
                                
                                console.log('AssetVersionList - Selection completed for version:', item.Version);
                            }}
                            iconName={isSelected ? "check" : "add-plus"}
                            ariaLabel={
                                isSelected 
                                    ? `Remove version ${item.Version} from comparison`
                                    : `Add version ${item.Version} to comparison`
                            }
                        >
                            {isSelected 
                                ? (selectionIndex > -1 ? `Selected (${selectionIndex + 1})` : "Selected") 
                                : "Select for Comparison"}
                        </Button>
                        <Button
                            variant="normal"
                            onClick={(e) => {
                                e.stopPropagation(); // Prevent row selection
                                console.log('AssetVersionList - Compare with Current button clicked for version:', item);
                                // Pass true for forceWithCurrent parameter
                                handleVersionSelectionForComparison(item, true);
                            }}
                            iconName="copy"
                            ariaLabel={`Compare version ${item.Version} with current files`}
                        >
                            Compare with Current
                        </Button>
                    </SpaceBetween>
                );
            }
        },
        {
            id: 'version',
            header: 'Version',
            cell: (item: AssetVersion) => (
                <Box>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        v{item.Version}
                        {item.isCurrent && <Badge color="blue">Current</Badge>}
                    </div>
                </Box>
            ),
            sortingField: 'Version'
        },
        {
            id: 'dateModified',
            header: 'Date Created',
            cell: (item: AssetVersion) => formatDate(item.DateModified),
            sortingField: 'DateModified'
        },
        {
            id: 'createdBy',
            header: 'Created By',
            cell: (item: AssetVersion) => item.createdBy || 'System',
            sortingField: 'createdBy'
        },
        {
            id: 'comment',
            header: 'Comment',
            cell: (item: AssetVersion) => item.Comment || '-',
            sortingField: 'Comment'
        },
        {
            id: 'actions',
            header: 'Actions',
            cell: (item: AssetVersion) => (
                <SpaceBetween direction="horizontal" size="xs">
                    <Button
                        onClick={() => showDownloadConfirmation(item)}
                        iconName="download"
                        disabled={item.fileCount === 0}
                        ariaLabel={
                            item.fileCount > 0 
                                ? `Download ${item.fileCount} asset files`
                                : "No files available for download"
                        }
                    >
                        Download Assets
                    </Button>
                    {/* Only show revert button for non-current versions */}
                    {!item.isCurrent && (
                        <Button
                            onClick={() => onRevertVersion(item)}
                            iconName="undo"
                        >
                            Revert to this Version
                        </Button>
                    )}
                </SpaceBetween>
            )
        }
    ];
    
    // Handle selection
    const handleSelectionChange = (selectedItems: AssetVersion[]) => {
        console.log('AssetVersionList - handleSelectionChange called with:', selectedItems);
        
        // Only update selection if it's actually changing
        if (selectedItems.length > 0) {
            // Check if we're already selected this version to avoid unnecessary updates
            if (!selectedVersion || selectedVersion.Version !== selectedItems[0].Version) {
                console.log('AssetVersionList - Selecting version:', selectedItems[0]);
                onVersionSelect(selectedItems[0]);
            }
        } else if (selectedVersion) {
            // Only clear if we have a selection and the user explicitly cleared it
            console.log('AssetVersionList - Clearing selected version');
            setSelectedVersion(null);
        }
    };
    
    // Render loading state
    if (loading && versions.length === 0) {
        return (
            <Box textAlign="center" padding="l">
                <Spinner size="large" />
                <div>Loading asset versions...</div>
            </Box>
        );
    }
    
    return (
        <>
            <Table
                columnDefinitions={columns}
                items={versions}
                loading={loading}
                loadingText="Loading asset versions"
                selectionType="single"
                selectedItems={selectedVersion ? [selectedVersion] : []}
                onSelectionChange={({ detail }) => {
                    console.log('AssetVersionList - onSelectionChange raw event:', detail);
                    
                    // Prevent selection changes if we're already processing one
                    // This helps prevent infinite loops
                    if (loading) {
                        console.log('AssetVersionList - Ignoring selection change during loading');
                        return;
                    }
                    
                    // Always prevent clearing the selection when files are showing
                    if (detail.selectedItems.length === 0 && selectedVersion) {
                        console.log('AssetVersionList - Preventing selection clearing, keeping current selection');
                        return; // Don't clear the selection at all
                    }
                    
                    // Only process actual selection changes
                    if (detail.selectedItems.length > 0) {
                        // Check if we're already selected this version to avoid unnecessary updates
                        if (!selectedVersion || selectedVersion.Version !== detail.selectedItems[0].Version) {
                            handleSelectionChange(detail.selectedItems);
                        } else {
                            console.log('AssetVersionList - Ignoring selection of already selected version');
                        }
                    }
                }}
                header={
                    <div>
                        <SpaceBetween direction="vertical" size="xs">
                            <SpaceBetween direction="horizontal" size="xs">
                                <div>
                                    <strong>Total versions:</strong> {totalVersions}
                                    {selectedVersion && !compareMode && (
                                        <span style={{ marginLeft: '12px' }}>
                                            <Badge color="blue">Selected: v{selectedVersion.Version}</Badge>
                                        </span>
                                    )}
                                </div>
                                {compareMode && versionToCompare && (
                                    <Badge color="blue">
                                        Selected for comparison: v{versionToCompare.Version}
                                    </Badge>
                                )}
                                {versionsForComparison.length > 0 && showComparisonOptions && (
                                    <div>
                                        <strong>Selected for comparison:</strong>{' '}
                                        {versionsForComparison.map((v, index) => (
                                            <span key={v.Version} style={{ marginRight: '8px' }}>
                                                <Badge color="blue">
                                                    {index + 1}: v{v.Version}
                                                </Badge>
                                            </span>
                                        ))}
                                        {versionsForComparison.length === 1 && comparisonType === 'two-versions' && (
                                            <span> (Select one more version to compare)</span>
                                        )}
                                    </div>
                                )}
                            </SpaceBetween>
                        </SpaceBetween>
                    </div>
                }
                filter={
                    <TextFilter
                        filteringText={filterText}
                        filteringPlaceholder="Find versions"
                        filteringAriaLabel="Filter versions"
                        onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />
                }
                pagination={
                    <Pagination
                        currentPageIndex={currentPage}
                        pagesCount={Math.max(1, Math.ceil(totalVersions / pageSize))}
                        onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
                        ariaLabels={{
                            nextPageLabel: 'Next page',
                            previousPageLabel: 'Previous page',
                            pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(totalVersions / pageSize))}`
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
                                visibleContent: detail.visibleContent ? [...detail.visibleContent] : preferences.visibleContent
                            };
                            setPreferences(newPreferences);
                            
                            // Update page size if changed
                            if (detail.pageSize !== undefined && detail.pageSize !== pageSize) {
                                setPageSize(detail.pageSize);
                            }
                        }}
                        pageSizePreference={{
                            title: "Page size",
                            options: [
                                { value: 10, label: "10 versions" },
                                { value: 20, label: "20 versions" },
                                { value: 50, label: "50 versions" },
                                { value: 100, label: "100 versions" }
                            ]
                        }}
                        visibleContentPreference={{
                            title: "Select visible columns",
                            options: [
                                {
                                    label: "Version information",
                                    options: [
                                        { id: "version", label: "Version" },
                                        { id: "dateModified", label: "Date Created" },
                                        { id: "createdBy", label: "Created By" },
                                        { id: "comment", label: "Comment" }
                                    ]
                                },
                                {
                                    label: "Actions",
                                    options: [
                                        { id: "compare", label: "Compare" },
                                        { id: "actions", label: "Actions" }
                                    ]
                                }
                            ]
                        }}
                    />
                }
                visibleColumns={preferences.visibleContent}
                empty={
                    <Box textAlign="center" padding="l">
                        <div>No asset versions found</div>
                    </Box>
                }
            />
            
            {/* Download Confirmation Modal */}
            <Modal
                visible={showDownloadModal}
                onDismiss={() => setShowDownloadModal(false)}
                header={`Download Asset Version ${downloadVersion ? `v${downloadVersion.Version}` : ''}`}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={() => setShowDownloadModal(false)}>
                                Cancel
                            </Button>
                            <Button 
                                variant="primary" 
                                onClick={handleDownloadAssets}
                                disabled={downloadLoading || !downloadDetails || downloadDetails.fileCount === 0}
                            >
                                Download
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="l">
                    {downloadLoading ? (
                        <Box textAlign="center" padding="l">
                            <Spinner size="normal" />
                            <div>Preparing download information...</div>
                        </Box>
                    ) : downloadDetails ? (
                        <>
                            <Box>
                                <SpaceBetween direction="vertical" size="s">
                                    <div>
                                        <strong>Files to download:</strong> {downloadDetails.fileCount}
                                    </div>
                                    <div>
                                        <strong>Total size:</strong> {formatFileSize(downloadDetails.totalSize)}
                                    </div>
                                    {downloadVersion && downloadVersion.Comment && (
                                        <div>
                                            <strong>Version comment:</strong> {downloadVersion.Comment}
                                        </div>
                                    )}
                                </SpaceBetween>
                            </Box>
                            
                            {downloadDetails.fileCount > 0 ? (
                                <Box>
                                    <p>
                                        You will be redirected to the download page where you can select a folder to save these files.
                                        The files will maintain their relative paths within the selected folder.
                                    </p>
                                    <p>
                                        <strong>Note:</strong> Permanently deleted files are not included in the download.
                                    </p>
                                </Box>
                            ) : (
                                <Box>
                                    <p>
                                        There are no files available for download in this version.
                                        This may be because all files have been permanently deleted.
                                    </p>
                                </Box>
                            )}
                        </>
                    ) : (
                        <Box>
                            <p>Failed to load download information. Please try again.</p>
                        </Box>
                    )}
                </SpaceBetween>
            </Modal>
        </>
    );
};
