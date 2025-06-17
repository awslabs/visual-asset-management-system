/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext } from 'react';
import {
    Box,
    Button,
    Pagination,
    SpaceBetween,
    Spinner,
    Table,
    TextFilter,
    Badge
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
    
    // Get context values
    const context = useContext(AssetVersionContext);
    
    if (!context) {
        throw new Error('AssetVersionList must be used within an AssetVersionContext.Provider');
    }
    
    const {
        loading,
        versions,
        selectedVersion,
        totalVersions,
        currentPage,
        pageSize,
        setCurrentPage,
        setSelectedVersion,
        compareMode,
        versionToCompare
    } = context;
    
    // Handle download assets
    const handleDownloadAssets = async (version: AssetVersion) => {
        try {
            // Fetch the version details to get the files
            const [success, response] = await fetchAssetVersion({
                databaseId: databaseId!,
                assetId: assetId!,
                assetVersionId: `v${version.Version}`
            });
            
            if (success && response) {
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
                    name: `Asset Version v${version.Version}`,
                    displayName: `Asset Version v${version.Version}`,
                    relativePath: "/",
                    keyPrefix: "/",
                    level: 0,
                    expanded: true,
                    subTree: []
                };
                
                // Add files to the fileTree
                if (response.files && response.files.length > 0) {
                    // Only include files that are not permanently deleted
                    const downloadableFiles = response.files.filter((file: FileVersion) => !file.isPermanentlyDeleted);
                    
                    downloadableFiles.forEach((file: FileVersion) => {
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
                
                // Navigate to the AssetDownload page
                navigate(`/databases/${databaseId}/assets/${assetId}/download`, {
                    state: {
                        assetVersionId: `v${version.Version}`,
                        fileTree: fileTree
                    }
                });
            } else {
                console.error('Failed to fetch version details for download');
            }
        } catch (error) {
            console.error('Error preparing download:', error);
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
    
    // Table columns
    const columns = [
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
                        onClick={() => handleDownloadAssets(item)}
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
        if (selectedItems.length > 0) {
            onVersionSelect(selectedItems[0]);
        } else {
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
        <Table
            columnDefinitions={columns}
            items={versions}
            loading={loading}
            loadingText="Loading asset versions"
            selectionType="single"
            selectedItems={selectedVersion ? [selectedVersion] : []}
            onSelectionChange={({ detail }) => handleSelectionChange(detail.selectedItems)}
            header={
                <SpaceBetween direction="horizontal" size="xs">
                    <div>
                        <strong>Total versions:</strong> {totalVersions}
                    </div>
                    {compareMode && versionToCompare && (
                        <Badge color="blue">
                            Selected for comparison: v{versionToCompare.Version}
                        </Badge>
                    )}
                </SpaceBetween>
            }
            pagination={
                <Pagination
                    currentPageIndex={currentPage}
                    pagesCount={Math.ceil(totalVersions / pageSize)}
                    onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
                />
            }
            empty={
                <Box textAlign="center" padding="l">
                    <div>No asset versions found</div>
                </Box>
            }
        />
    );
};
