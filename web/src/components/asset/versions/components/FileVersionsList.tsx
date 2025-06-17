/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext } from 'react';
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    Spinner,
    Table,
    Link,
    Badge
} from '@cloudscape-design/components';
import { useNavigate, useParams } from 'react-router';
import { AssetVersionContext, FileVersion } from '../AssetVersionManager';
import { downloadAsset } from '../../../../services/APIService';

export const FileVersionsList: React.FC = () => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    const navigate = useNavigate();
    
    // Get context values
    const context = useContext(AssetVersionContext);
    
    if (!context) {
        throw new Error('FileVersionsList must be used within an AssetVersionContext.Provider');
    }
    
    const {
        loading,
        selectedVersion,
        selectedVersionDetails
    } = context;
    
    // Handle view file
    const handleViewFile = (file: FileVersion) => {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: file.relativeKey.split('/').pop() || file.relativeKey,
                key: file.relativeKey,
                isDirectory: false,
                versionId: file.versionId,
                size: file.size,
                dateCreatedCurrentVersion: file.lastModified
            }
        });
    };
    
    // Handle download file
    const handleDownloadFile = async (file: FileVersion) => {
        try {
            const response = await downloadAsset({
                assetId: assetId!,
                databaseId: databaseId!,
                key: file.relativeKey,
                versionId: file.versionId,
                downloadType: "assetFile"
            });
            
            if (response !== false && Array.isArray(response) && response[0] !== false) {
                const link = document.createElement('a');
                link.href = response[1];
                link.click();
            } else {
                console.error('Failed to download file');
            }
        } catch (err) {
            console.error('Error downloading file:', err);
        }
    };
    
    // Format file size
    const formatFileSize = (size?: number): string => {
        if (size === undefined) return 'Unknown';
        if (size === 0) return '0 B';
        
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(size) / Math.log(1024));
        return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
    };
    
    // Format date
    const formatDate = (dateString?: string): string => {
        if (!dateString) return 'Unknown';
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
            id: 'fileName',
            header: 'File Name',
            cell: (item: FileVersion) => {
                const fileName = item.relativeKey.split('/').pop() || item.relativeKey;
                return (
                    <Box>
                        <div>{fileName}</div>
                        {item.isPermanentlyDeleted && <Badge color="red">Permanently Deleted</Badge>}
                        {item.isLatestVersionArchived && <Badge color="grey">Latest Version Archived</Badge>}
                    </Box>
                );
            },
            sortingField: 'relativeKey'
        },
        {
            id: 'path',
            header: 'Path',
            cell: (item: FileVersion) => item.relativeKey,
            sortingField: 'relativeKey'
        },
        {
            id: 'size',
            header: 'Size',
            cell: (item: FileVersion) => formatFileSize(item.size),
            sortingField: 'size'
        },
        {
            id: 'lastModified',
            header: 'Last Modified',
            cell: (item: FileVersion) => formatDate(item.lastModified),
            sortingField: 'lastModified'
        },
        {
            id: 'versionId',
            header: 'Version ID',
            cell: (item: FileVersion) => (
                <Box>
                    <div style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>
                        {item.versionId}
                    </div>
                </Box>
            ),
            sortingField: 'versionId'
        },
        {
            id: 'actions',
            header: 'Actions',
            cell: (item: FileVersion) => {
                // Don't show actions for permanently deleted files
                if (item.isPermanentlyDeleted) {
                    return <Box>File permanently deleted</Box>;
                }
                
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
                            disabled={item.isPermanentlyDeleted}
                        >
                            Download File
                        </Button>
                    </SpaceBetween>
                );
            }
        }
    ];
    
    // Render loading state - only show spinner if actually loading
    if (loading && !selectedVersionDetails) {
        return (
            <Container header={<Header variant="h3">Associated Files</Header>}>
                <Box textAlign="center" padding="l">
                    <Spinner size="normal" />
                    <div>Loading file versions...</div>
                </Box>
            </Container>
        );
    }
    
    // If we have a selected version but no details yet, and not loading, show no files message
    if (selectedVersion && !selectedVersionDetails && !loading) {
        return (
            <Container header={<Header variant="h3">Associated Files</Header>}>
                <Box textAlign="center" padding="l">
                    <div>No files associated with this asset version</div>
                </Box>
            </Container>
        );
    }
    
    // If we don't have a selected version, don't render anything
    if (!selectedVersion) {
        return null;
    }
    
    return (
        <Container
            header={
                <Header variant="h3">
                    Files in Version v{selectedVersion?.Version}
                </Header>
            }
        >
            <Table
                columnDefinitions={columns}
                items={selectedVersionDetails?.files || []}
                loading={loading}
                loadingText="Loading file versions"
                empty={
                    <Box textAlign="center" padding="l">
                        <div>No files associated with this asset version</div>
                    </Box>
                }
                header={
                    <Box padding="s">
                        <SpaceBetween direction="horizontal" size="xs">
                            <div>
                                <strong>Total files:</strong> {selectedVersionDetails?.files?.length || 0}
                            </div>
                            {selectedVersionDetails?.comment && (
                                <div>
                                    <strong>Version comment:</strong> {selectedVersionDetails.comment}
                                </div>
                            )}
                        </SpaceBetween>
                    </Box>
                }
            />
        </Container>
    );
};
