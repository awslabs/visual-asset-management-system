/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from 'react';
import {
    Box,
    Button,
    Modal,
    SpaceBetween,
    Alert,
    FormField,
    Textarea,
    SegmentedControl,
    Container,
    Header,
    Spinner,
    Table,
    Toggle,
    Checkbox
} from '@cloudscape-design/components';
import { useParams } from 'react-router';
import { createAssetVersion, fetchAssetS3Files, fetchAssetVersion } from '../../../../services/AssetVersionService';
import { AssetVersion, FileVersion } from '../AssetVersionManager';

interface CreateAssetVersionModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: () => void;
}

interface S3File {
    fileName: string;
    key: string;
    relativePath: string;
    isFolder: boolean;
    size?: number;
    dateCreatedCurrentVersion: string;
    versionId: string;
    storageClass?: string;
    isArchived: boolean;
}

interface SelectedFile {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
}

type CreationMode = 'current' | 'select' | 'modify';

export const CreateAssetVersionModal: React.FC<CreateAssetVersionModalProps> = ({
    visible,
    onDismiss,
    onSuccess
}) => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    
    // State
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const [comment, setComment] = useState<string>('');
    const [creationMode, setCreationMode] = useState<CreationMode>('current');
    
    // Files state
    const [loadingFiles, setLoadingFiles] = useState<boolean>(false);
    const [initialLoading, setInitialLoading] = useState<boolean>(true);
    const [retryCount, setRetryCount] = useState<number>(0);
    const [s3Files, setS3Files] = useState<S3File[]>([]);
    const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([]);
    const [showArchived, setShowArchived] = useState<boolean>(false);
    
    // Version selection state
    const [versions, setVersions] = useState<AssetVersion[]>([]);
    const [selectedVersion, setSelectedVersion] = useState<AssetVersion | null>(null);
    const [selectedVersionFiles, setSelectedVersionFiles] = useState<FileVersion[]>([]);
    
    // Selected file for version selection
    const [selectedFileForVersions, setSelectedFileForVersions] = useState<SelectedFile | null>(null);
    const [fileVersions, setFileVersions] = useState<any[]>([]);
    
    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setLoading(false);
            setError(null);
            setComment('');
            setCreationMode('current');
            setSelectedFiles([]);
            setShowArchived(false);
            setSelectedVersion(null);
            setSelectedVersionFiles([]);
            setSelectedFileForVersions(null);
            setFileVersions([]);
            setInitialLoading(true);
            setRetryCount(0);
            
            // Load S3 files when modal opens with a small delay to prevent immediate errors
            setTimeout(() => {
                loadS3Files();
            }, 100);
        } else {
            // Reset states when modal closes
            setInitialLoading(true);
            setRetryCount(0);
        }
    }, [visible]);
    
    // Load S3 files with improved error handling
    const loadS3Files = async (isRetry = false) => {
        // Validate required parameters
        if (!databaseId || !assetId) {
            console.error('Missing required parameters:', { databaseId, assetId });
            if (!isRetry && !initialLoading) {
                setError('Database ID and Asset ID are required');
            }
            setInitialLoading(false);
            return;
        }
        
        if (!isRetry) {
            setLoadingFiles(true);
        }
        
        // Only clear error if this is a retry attempt
        if (isRetry) {
            setError(null);
        }
        
        try {
            const [success, response] = await fetchAssetS3Files({
                databaseId,
                assetId,
                includeArchived: showArchived
            });
            
            if (success && Array.isArray(response)) {
                // Filter out folders
                const files = response.filter(file => !file.isFolder);
                setS3Files(files);
                setRetryCount(0); // Reset retry count on success
                
                // Clear any previous errors on successful load
                if (error) {
                    setError(null);
                }
            } else {
                // Only show error if not in initial loading state or if this is a retry
                if (!initialLoading || isRetry) {
                    const errorMessage = typeof response === 'string' ? response : 'Failed to load asset files';
                    setError(errorMessage);
                }
                console.error('Failed to fetch asset files:', response);
            }
        } catch (err) {
            console.error('Error loading files:', err);
            
            // Only show error if not in initial loading state or if this is a retry
            if (!initialLoading || isRetry) {
                setError('An error occurred while loading files. Please try again.');
            }
        } finally {
            setLoadingFiles(false);
            setInitialLoading(false);
        }
    };
    
    // Retry function for failed file loads
    const retryLoadFiles = () => {
        setRetryCount(prev => prev + 1);
        loadS3Files(true);
    };
    
    // Load files when showArchived changes
    useEffect(() => {
        if (visible) {
            loadS3Files();
        }
    }, [showArchived]);
    
    // Handle create version
    const handleCreateVersion = async () => {
        if (!databaseId || !assetId) {
            setError('Database ID and Asset ID are required');
            return;
        }
        
        // Validate comment is provided
        if (!comment.trim()) {
            setError('Comment is required');
            return;
        }
        
        setLoading(true);
        setError(null);
        
        try {
            let success, response;
            
            if (creationMode === 'current') {
                // Use all current files
                [success, response] = await createAssetVersion({
                    databaseId,
                    assetId,
                    useLatestFiles: true,
                    files: [], // Provide empty array to satisfy TypeScript
                    comment
                });
            } else if (creationMode === 'select' || creationMode === 'modify') {
                // Use selected files
                if (selectedFiles.length === 0) {
                    setError('No files selected');
                    setLoading(false);
                    return;
                }
                
                [success, response] = await createAssetVersion({
                    databaseId,
                    assetId,
                    useLatestFiles: false,
                    files: selectedFiles,
                    comment
                });
            }
            
            if (success) {
                onSuccess();
            } else {
                setError(typeof response === 'string' ? response : 'Failed to create version');
            }
        } catch (err) {
            setError('An error occurred while creating the version');
            console.error('Error creating version:', err);
        } finally {
            setLoading(false);
        }
    };
    
    // Handle file selection
    const handleFileSelection = (file: S3File, selected: boolean) => {
        if (selected) {
            // Add file to selected files
            setSelectedFiles(prev => [
                ...prev,
                {
                    relativeKey: file.relativePath,
                    versionId: file.versionId,
                    isArchived: file.isArchived
                }
            ]);
        } else {
            // Remove file from selected files
            setSelectedFiles(prev => 
                prev.filter(f => f.relativeKey !== file.relativePath)
            );
        }
    };
    
    // Handle file version selection
    const handleFileVersionSelection = (file: SelectedFile, versionId: string) => {
        // Update the version ID for the selected file
        setSelectedFiles(prev => 
            prev.map(f => 
                f.relativeKey === file.relativeKey 
                    ? { ...f, versionId } 
                    : f
            )
        );
        
        // Close the file versions view
        setSelectedFileForVersions(null);
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
    
    // Render creation mode content
    const renderCreationModeContent = () => {
        switch (creationMode) {
            case 'current':
                return (
                    <Alert type="info">
                        <div>
                            <strong>Use All Current Files and Versions</strong>
                        </div>
                        <div>
                            This will create a new version using all current files and their latest versions.
                        </div>
                    </Alert>
                );
                
            case 'select':
                return (
                    <SpaceBetween direction="vertical" size="l">
                        <Container header={<Header variant="h3">Available Files</Header>}>
                            <SpaceBetween direction="vertical" size="s">
                                <Toggle
                                    onChange={({ detail }) => setShowArchived(detail.checked)}
                                    checked={showArchived}
                                >
                                    Show archived files
                                </Toggle>
                                
                                {loadingFiles ? (
                                    <Box textAlign="center" padding="l">
                                        <Spinner size="normal" />
                                        <div>Loading files...</div>
                                    </Box>
                                ) : (
                                    <Table
                                        columnDefinitions={[
                                            {
                                                id: 'select',
                                                header: 'Select',
                                                cell: (item: S3File) => (
                                                    <Checkbox
                                                        checked={selectedFiles.some(f => f.relativeKey === item.relativePath)}
                                                        onChange={({ detail }) => handleFileSelection(item, detail.checked)}
                                                    />
                                                )
                                            },
                                            {
                                                id: 'fileName',
                                                header: 'File Name',
                                                cell: (item: S3File) => item.fileName
                                            },
                                            {
                                                id: 'path',
                                                header: 'Path',
                                                cell: (item: S3File) => item.relativePath
                                            },
                                            {
                                                id: 'size',
                                                header: 'Size',
                                                cell: (item: S3File) => formatFileSize(item.size)
                                            },
                                            {
                                                id: 'lastModified',
                                                header: 'Last Modified',
                                                cell: (item: S3File) => formatDate(item.dateCreatedCurrentVersion)
                                            },
                                            {
                                                id: 'status',
                                                header: 'Status',
                                                cell: (item: S3File) => item.isArchived ? 'Archived' : 'Active'
                                            }
                                        ]}
                                        items={s3Files}
                                        empty={
                                            <Box textAlign="center" padding="l">
                                                <div>No files found</div>
                                            </Box>
                                        }
                                    />
                                )}
                            </SpaceBetween>
                        </Container>
                        
                        <Container header={<Header variant="h3">Selected Files</Header>}>
                            {selectedFiles.length === 0 ? (
                                <Box textAlign="center" padding="l">
                                    <div>No files selected</div>
                                </Box>
                            ) : (
                                <Table
                                    columnDefinitions={[
                                        {
                                            id: 'fileName',
                                            header: 'File Name',
                                            cell: (item: SelectedFile) => item.relativeKey.split('/').pop() || item.relativeKey
                                        },
                                        {
                                            id: 'path',
                                            header: 'Path',
                                            cell: (item: SelectedFile) => item.relativeKey
                                        },
                                        {
                                            id: 'versionId',
                                            header: 'Version ID',
                                            cell: (item: SelectedFile) => (
                                                <Box>
                                                    <div style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>
                                                        {item.versionId}
                                                    </div>
                                                </Box>
                                            )
                                        },
                                        {
                                            id: 'actions',
                                            header: 'Actions',
                                            cell: (item: SelectedFile) => (
                                                <SpaceBetween direction="horizontal" size="xs">
                                                    <Button
                                                        onClick={() => setSelectedFileForVersions(item)}
                                                    >
                                                        Select Other Version
                                                    </Button>
                                                    <Button
                                                        onClick={() => setSelectedFiles(prev => prev.filter(f => f.relativeKey !== item.relativeKey))}
                                                    >
                                                        Remove
                                                    </Button>
                                                </SpaceBetween>
                                            )
                                        }
                                    ]}
                                    items={selectedFiles}
                                />
                            )}
                        </Container>
                        
                        {selectedFileForVersions && (
                            <Container header={<Header variant="h3">File Versions</Header>}>
                                <Box>
                                    <div>
                                        <strong>File:</strong> {selectedFileForVersions.relativeKey}
                                    </div>
                                    <div>
                                        <strong>Current Version:</strong> {selectedFileForVersions.versionId}
                                    </div>
                                </Box>
                                
                                {/* File versions table would go here - this would require additional API support */}
                                <Box padding="l" textAlign="center">
                                    <div>File version selection requires additional API support</div>
                                    <Button
                                        onClick={() => setSelectedFileForVersions(null)}
                                        variant="primary"
                                    >
                                        Close
                                    </Button>
                                </Box>
                            </Container>
                        )}
                    </SpaceBetween>
                );
                
            case 'modify':
                return (
                    <Alert type="info">
                        <div>
                            <strong>Modify from Existing Version</strong>
                        </div>
                        <div>
                            This feature requires additional API support to list and select from existing versions.
                        </div>
                    </Alert>
                );
                
            default:
                return null;
        }
    };
    
    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header="Create New Asset Version"
            size="max"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleCreateVersion}
                            loading={loading}
                        >
                            Create Version
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {error && (
                    <Alert 
                        type="error" 
                        dismissible 
                        onDismiss={() => setError(null)}
                        action={
                            <Button 
                                onClick={retryLoadFiles}
                                variant="primary"
                            >
                                Retry
                            </Button>
                        }
                    >
                        {error}
                    </Alert>
                )}
                
                {initialLoading && loadingFiles && (
                    <Alert type="info">
                        <Box textAlign="center">
                            <Spinner size="normal" />
                            <div style={{ marginTop: '8px' }}>Loading asset files...</div>
                        </Box>
                    </Alert>
                )}
                
                <FormField
                    label="Version Creation Mode"
                    description="Select how you want to create this version"
                >
                    <SegmentedControl
                        selectedId={creationMode}
                        onChange={({ detail }) => setCreationMode(detail.selectedId as CreationMode)}
                        options={[
                            { text: 'Use all current files and versions', id: 'current' },
                            { text: 'Select specific files and versions', id: 'select' },
                            { text: 'Modify from current asset version', id: 'modify' }
                        ]}
                    />
                </FormField>
                
                {renderCreationModeContent()}
                
                <FormField
                    label="Version Comment *"
                    description="Add a comment to describe this version (required)"
                    errorText={!comment.trim() && error ? "Comment is required" : undefined}
                >
                    <Textarea
                        value={comment}
                        onChange={({ detail }) => setComment(detail.value)}
                        placeholder="Enter a comment for the version (required)"
                        invalid={!comment.trim() && error ? true : false}
                    />
                </FormField>
            </SpaceBetween>
        </Modal>
    );
};
