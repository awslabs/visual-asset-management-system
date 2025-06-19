/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo } from 'react';
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
    Checkbox,
    Badge,
    Pagination
} from '@cloudscape-design/components';
import { useParams } from 'react-router';
import { 
    createAssetVersion, 
    fetchAssetS3Files, 
    fetchAssetVersion, 
    fetchAssetVersions,
    fetchFileVersions 
} from '../../../../services/AssetVersionService';
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
    currentAssetVersionFileVersionMismatch?: boolean;
}

interface SelectedFile {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    isCurrent?: boolean;
}

interface S3FileVersion {
    versionId: string;
    lastModified?: string;
    size?: number;
    isArchived: boolean;
    isLatest?: boolean;
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
    
    // Normalize path to ensure consistent format
    const normalizePath = (path: string): string => {
        // Remove leading slash if present
        return path.startsWith('/') ? path.substring(1) : path;
    };
    
    // Files state
    const [loadingFiles, setLoadingFiles] = useState<boolean>(false);
    const [initialLoading, setInitialLoading] = useState<boolean>(true);
    const [retryCount, setRetryCount] = useState<number>(0);
    const [s3Files, setS3Files] = useState<S3File[]>([]);
    const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([]);
    const [showArchived, setShowArchived] = useState<boolean>(false);
    const [showMismatchedOnly, setShowMismatchedOnly] = useState<boolean>(false);
    
    // Version selection state
    const [versions, setVersions] = useState<AssetVersion[]>([]);
    const [loadingVersions, setLoadingVersions] = useState<boolean>(false);
    const [selectedVersion, setSelectedVersion] = useState<AssetVersion | null>(null);
    const [selectedVersionFiles, setSelectedVersionFiles] = useState<FileVersion[]>([]);
    
    // Selected file for version selection
    const [selectedFileForVersions, setSelectedFileForVersions] = useState<SelectedFile | null>(null);
    const [fileVersions, setFileVersions] = useState<S3FileVersion[]>([]);
    const [loadingFileVersions, setLoadingFileVersions] = useState<boolean>(false);
    
    // Pagination state
    const [currentVersionPage, setCurrentVersionPage] = useState<number>(1);
    const [currentAvailableFilesPage, setCurrentAvailableFilesPage] = useState<number>(1);
    const [currentSelectedFilesPage, setCurrentSelectedFilesPage] = useState<number>(1);
    const [currentFileVersionsPage, setCurrentFileVersionsPage] = useState<number>(1);
    
    // Pagination constants
    const versionsPerPage = 3;
    const availableFilesPerPage = 10;
    const selectedFilesPerPage = 10;
    const fileVersionsPerPage = 10;
    
    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setLoading(false);
            setError(null);
            setComment('');
            setCreationMode('current');
            setSelectedFiles([]);
            setShowArchived(false);
            setShowMismatchedOnly(false);
            setSelectedVersion(null);
            setSelectedVersionFiles([]);
            setSelectedFileForVersions(null);
            setFileVersions([]);
            setInitialLoading(true);
            setRetryCount(0);
            setVersions([]);
            
            // Reset pagination
            setCurrentVersionPage(1);
            setCurrentAvailableFilesPage(1);
            setCurrentSelectedFilesPage(1);
            setCurrentFileVersionsPage(1);
            
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
    
    // Reset selected files when changing creation mode
    useEffect(() => {
        setSelectedFiles([]);
        setSelectedVersion(null);
        setSelectedVersionFiles([]);
        
        // Reset pagination when changing mode
        setCurrentVersionPage(1);
        setCurrentAvailableFilesPage(1);
        setCurrentSelectedFilesPage(1);
    }, [creationMode]);
    
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
            setCurrentAvailableFilesPage(1); // Reset pagination when filter changes
        }
    }, [showArchived]);
    
    // Reset pagination when filter changes
    useEffect(() => {
        setCurrentAvailableFilesPage(1);
    }, [showMismatchedOnly]);
    
    // Reset selected files pagination when selected files change
    useEffect(() => {
        setCurrentSelectedFilesPage(1);
    }, [selectedFiles.length]);
    
    // Reset file versions pagination when file versions change
    useEffect(() => {
        setCurrentFileVersionsPage(1);
    }, [fileVersions.length]);
    
    // Load versions when in modify mode
    useEffect(() => {
        if (visible && creationMode === 'modify') {
            loadVersions();
        }
    }, [creationMode, visible]);
    
    // Load file versions when a file is selected for version selection
    useEffect(() => {
        if (selectedFileForVersions) {
            loadFileVersions(selectedFileForVersions.relativeKey);
        }
    }, [selectedFileForVersions]);
    
    // Filter files based on toggles
    const filteredS3Files = useMemo(() => {
        let files = s3Files;
        
        if (showMismatchedOnly) {
            files = files.filter(file => file.currentAssetVersionFileVersionMismatch === true);
        }
        
        return files;
    }, [s3Files, showMismatchedOnly]);
    
    // Filter selected files for modify mode
    const filteredSelectedFiles = useMemo(() => {
        if (!showMismatchedOnly || creationMode !== 'modify') {
            return selectedFiles;
        }
        
        // Find files with version mismatch by comparing with s3Files
        const mismatchedKeys = s3Files
            .filter(file => file.currentAssetVersionFileVersionMismatch === true)
            .map(file => normalizePath(file.relativePath));
            
        return selectedFiles.filter(file => mismatchedKeys.includes(normalizePath(file.relativeKey)));
    }, [selectedFiles, s3Files, showMismatchedOnly, creationMode]);
    
    // Create paginated data
    const paginatedVersions = useMemo(() => {
        const startIndex = (currentVersionPage - 1) * versionsPerPage;
        return versions.slice(startIndex, startIndex + versionsPerPage);
    }, [versions, currentVersionPage, versionsPerPage]);
    
    const paginatedAvailableFiles = useMemo(() => {
        const startIndex = (currentAvailableFilesPage - 1) * availableFilesPerPage;
        return filteredS3Files.slice(startIndex, startIndex + availableFilesPerPage);
    }, [filteredS3Files, currentAvailableFilesPage, availableFilesPerPage]);
    
    const paginatedSelectedFiles = useMemo(() => {
        const startIndex = (currentSelectedFilesPage - 1) * selectedFilesPerPage;
        return filteredSelectedFiles.slice(startIndex, startIndex + selectedFilesPerPage);
    }, [filteredSelectedFiles, currentSelectedFilesPage, selectedFilesPerPage]);
    
    const paginatedFileVersions = useMemo(() => {
        const startIndex = (currentFileVersionsPage - 1) * fileVersionsPerPage;
        return fileVersions.slice(startIndex, startIndex + fileVersionsPerPage);
    }, [fileVersions, currentFileVersionsPage, fileVersionsPerPage]);
    
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
            // Normalize the path
            const normalizedPath = normalizePath(file.relativePath);
            
            // Add file to selected files
            setSelectedFiles(prev => [
                ...prev,
                {
                    relativeKey: normalizedPath,
                    versionId: file.versionId,
                    isArchived: file.isArchived,
                    isCurrent: true // Mark as current version since it's selected from current files
                }
            ]);
        } else {
            // Normalize the path for comparison
            const normalizedPath = normalizePath(file.relativePath);
            
            // Remove file from selected files
            setSelectedFiles(prev => 
                prev.filter(f => normalizePath(f.relativeKey) !== normalizedPath)
            );
        }
    };
    
    // Load asset versions
    const loadVersions = async () => {
        if (!databaseId || !assetId) {
            return;
        }
        
        setLoadingVersions(true);
        try {
            const [success, response] = await fetchAssetVersions({
                databaseId,
                assetId,
                maxItems: 100,
                startingToken: null
            });
            
            if (success && response && response.versions) {
                setVersions(response.versions);
            } else {
                setVersions([]);
                setError('Failed to load asset versions');
            }
        } catch (err) {
            console.error('Error loading versions:', err);
            setError('An error occurred while loading versions');
            setVersions([]);
        } finally {
            setLoadingVersions(false);
        }
    };
    
    // Load file versions
    const loadFileVersions = async (relativeKey: string) => {
        if (!databaseId || !assetId) {
            return;
        }
        
        const dbId = databaseId;
        const aId = assetId;
        
        // Normalize the path
        const normalizedPath = normalizePath(relativeKey);
        
        setLoadingFileVersions(true);
        try {
            const [success, response] = await fetchFileVersions({
                databaseId: dbId,
                assetId: aId,
                filePath: normalizedPath
            });
            
            if (success && response && response.versions) {
                setFileVersions(response.versions);
            } else {
                setFileVersions([]);
                setError('Failed to load file versions');
            }
        } catch (err) {
            console.error('Error loading file versions:', err);
            setError('An error occurred while loading file versions');
            setFileVersions([]);
        } finally {
            setLoadingFileVersions(false);
        }
    };
    
    
    // Select version as base for modification
    const selectVersionAsBase = async (version: AssetVersion) => {
        setSelectedVersion(version);
        
        // Load files from this version
        try {
            if (!databaseId || !assetId) {
                setError('Database ID and Asset ID are required');
                return;
            }
            
            const [success, response] = await fetchAssetVersion({
                databaseId,
                assetId,
                assetVersionId: `${version.Version}`
            });
            
            if (success && response && response.files) {
                // Normalize s3Files paths for comparison
                const normalizedS3Files = s3Files.map(file => ({
                    ...file,
                    normalizedPath: normalizePath(file.relativePath)
                }));
                
                // Convert to selected files format and filter out permanently deleted files
                const files = response.files
                    .filter((file: FileVersion) => !file.isPermanentlyDeleted)
                    .map((file: FileVersion) => {
                        // Normalize the path
                        const normalizedPath = normalizePath(file.relativeKey);
                        
                        // Find matching file in s3Files to check if it's current
                        const currentFile = normalizedS3Files.find(s3File => 
                            s3File.normalizedPath === normalizedPath && 
                            s3File.versionId === file.versionId
                        );
                        
                        return {
                            relativeKey: normalizedPath, // Use normalized path consistently
                            versionId: file.versionId,
                            isArchived: file.isLatestVersionArchived,
                            isCurrent: !!currentFile // Mark as current if found in s3Files with same version
                        };
                    });
                
                setSelectedFiles(files);
                setSelectedVersionFiles(response.files);
            } else {
                setError('Failed to load version files');
            }
        } catch (err) {
            console.error('Error loading version files:', err);
            setError('An error occurred while loading version files');
        }
    };
    
    // Handle file version selection
    const handleFileVersionSelection = (file: SelectedFile, versionId: string) => {
        // Update the version ID for the selected file
        setSelectedFiles(prev => 
            prev.map((f: SelectedFile) => 
                f.relativeKey === file.relativeKey 
                    ? { 
                        ...f, 
                        versionId,
                        // If the version ID is different from the current one, it's not the current version
                        isCurrent: versionId === file.versionId ? f.isCurrent : false
                      } 
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
                                <SpaceBetween direction="horizontal" size="s">
                                    <Toggle
                                        onChange={({ detail }) => setShowArchived(detail.checked)}
                                        checked={showArchived}
                                    >
                                        Show archived files
                                    </Toggle>
                                    <Toggle
                                        onChange={({ detail }) => setShowMismatchedOnly(detail.checked)}
                                        checked={showMismatchedOnly}
                                    >
                                        Show only mismatched file versions
                                    </Toggle>
                                </SpaceBetween>
                                
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
                                                        checked={selectedFiles.some(f => f.relativeKey === normalizePath(item.relativePath))}
                                                        onChange={({ detail }) => handleFileSelection(item, detail.checked)}
                                                    />
                                                )
                                            },
                                            {
                                                id: 'fileName',
                                                header: 'File Name',
                                                cell: (item: S3File) => (
                                                    <Box>
                                                        <div>{item.fileName}</div>
                                                        {item.currentAssetVersionFileVersionMismatch && (
                                                            <Badge color="blue">Version Mismatch</Badge>
                                                        )}
                                                    </Box>
                                                )
                                            },
                                            {
                                                id: 'path',
                                                header: 'Path',
                                                cell: (item: S3File) => normalizePath(item.relativePath)
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
                                        items={paginatedAvailableFiles}
                                        pagination={
                                            <Pagination
                                                currentPageIndex={currentAvailableFilesPage}
                                                pagesCount={Math.max(1, Math.ceil(filteredS3Files.length / availableFilesPerPage))}
                                                onChange={({ detail }) => setCurrentAvailableFilesPage(detail.currentPageIndex)}
                                                ariaLabels={{
                                                    nextPageLabel: 'Next page',
                                                    previousPageLabel: 'Previous page',
                                                    pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(filteredS3Files.length / availableFilesPerPage))}`
                                                }}
                                            />
                                        }
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
                                                    {item.isCurrent && <Badge color="blue">Current</Badge>}
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
                                                items={paginatedSelectedFiles}
                                                pagination={
                                                    <Pagination
                                                        currentPageIndex={currentSelectedFilesPage}
                                                        pagesCount={Math.max(1, Math.ceil(filteredSelectedFiles.length / selectedFilesPerPage))}
                                                        onChange={({ detail }) => setCurrentSelectedFilesPage(detail.currentPageIndex)}
                                                        ariaLabels={{
                                                            nextPageLabel: 'Next page',
                                                            previousPageLabel: 'Previous page',
                                                            pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(filteredSelectedFiles.length / selectedFilesPerPage))}`
                                                        }}
                                                    />
                                                }
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
                                
                                {loadingFileVersions ? (
                                    <Box textAlign="center" padding="l">
                                        <Spinner size="normal" />
                                        <div>Loading file versions...</div>
                                    </Box>
                                ) : (
                                    <Table
                                        columnDefinitions={[
                                                    {
                                                        id: 'versionId',
                                                        header: 'Version ID',
                                                        cell: (item: S3FileVersion) => (
                                                            <Box>
                                                                <div style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>
                                                                    {item.versionId}
                                                                </div>
                                                                {item.isLatest && <Badge color="blue">Latest</Badge>}
                                                            </Box>
                                                        )
                                                    },
                                                    {
                                                        id: 'lastModified',
                                                        header: 'Last Modified',
                                                        cell: (item: S3FileVersion) => formatDate(item.lastModified)
                                                    },
                                                    {
                                                        id: 'size',
                                                        header: 'Size',
                                                        cell: (item: S3FileVersion) => formatFileSize(item.size)
                                                    },
                                                    {
                                                        id: 'status',
                                                        header: 'Status',
                                                        cell: (item: S3FileVersion) => item.isArchived ? 'Archived' : 'Active'
                                                    },
                                                    {
                                                        id: 'actions',
                                                        header: 'Actions',
                                                        cell: (item: S3FileVersion) => (
                                                    <Button
                                                        onClick={() => {
                                                            if (selectedFileForVersions) {
                                                                handleFileVersionSelection(selectedFileForVersions, item.versionId);
                                                            }
                                                        }}
                                                        disabled={item.isArchived}
                                                    >
                                                        Select This Version
                                                    </Button>
                                                )
                                            }
                                        ]}
                                        items={paginatedFileVersions}
                                        pagination={
                                            <Pagination
                                                currentPageIndex={currentFileVersionsPage}
                                                pagesCount={Math.max(1, Math.ceil(fileVersions.length / fileVersionsPerPage))}
                                                onChange={({ detail }) => setCurrentFileVersionsPage(detail.currentPageIndex)}
                                                ariaLabels={{
                                                    nextPageLabel: 'Next page',
                                                    previousPageLabel: 'Previous page',
                                                    pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(fileVersions.length / fileVersionsPerPage))}`
                                                }}
                                            />
                                        }
                                        empty={
                                            <Box textAlign="center" padding="l">
                                                <div>No versions found</div>
                                            </Box>
                                        }
                                    />
                                )}
                                
                                <Box padding="m" textAlign="right">
                                    <Button
                                        onClick={() => setSelectedFileForVersions(null)}
                                        variant="normal"
                                    >
                                        Cancel
                                    </Button>
                                </Box>
                            </Container>
                        )}
                    </SpaceBetween>
                );
                
            case 'modify':
                return (
                    <SpaceBetween direction="vertical" size="l">
                        <Container header={<Header variant="h3">Select Base Version</Header>}>
                            {loadingVersions ? (
                                <Box textAlign="center" padding="l">
                                    <Spinner size="normal" />
                                    <div>Loading asset versions...</div>
                                </Box>
                            ) : (
                                <Table
                                    columnDefinitions={[
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
                                            )
                                        },
                                        {
                                            id: 'dateModified',
                                            header: 'Date Created',
                                            cell: (item: AssetVersion) => formatDate(item.DateModified)
                                        },
                                        {
                                            id: 'createdBy',
                                            header: 'Created By',
                                            cell: (item: AssetVersion) => item.createdBy || 'System'
                                        },
                                        {
                                            id: 'comment',
                                            header: 'Comment',
                                            cell: (item: AssetVersion) => item.Comment || '-'
                                        },
                                        {
                                            id: 'actions',
                                            header: 'Actions',
                                            cell: (item: AssetVersion) => (
                                                <Button
                                                    onClick={() => selectVersionAsBase(item)}
                                                    variant={selectedVersion?.Version === item.Version ? "primary" : "normal"}
                                                >
                                                    {selectedVersion?.Version === item.Version ? "Selected" : "Use as Base"}
                                                </Button>
                                            )
                                        }
                                    ]}
                                    items={paginatedVersions}
                                    pagination={
                                        <Pagination
                                            currentPageIndex={currentVersionPage}
                                            pagesCount={Math.max(1, Math.ceil(versions.length / versionsPerPage))}
                                            onChange={({ detail }) => setCurrentVersionPage(detail.currentPageIndex)}
                                            ariaLabels={{
                                                nextPageLabel: 'Next page',
                                                previousPageLabel: 'Previous page',
                                                pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(versions.length / versionsPerPage))}`
                                            }}
                                        />
                                    }
                                    empty={
                                        <Box textAlign="center" padding="l">
                                            <div>No versions found</div>
                                        </Box>
                                    }
                                />
                            )}
                        </Container>
                        
                        {selectedVersion && (
                            <>
                                <Container header={<Header variant="h3">Selected Files from Version v{selectedVersion.Version}</Header>}>
                                    <SpaceBetween direction="vertical" size="s">
                                        <Toggle
                                            onChange={({ detail }) => setShowMismatchedOnly(detail.checked)}
                                            checked={showMismatchedOnly}
                                        >
                                            Show only mismatched file versions
                                        </Toggle>
                                        
                                        {selectedFiles.length === 0 ? (
                                            <Box textAlign="center" padding="l">
                                                <div>No files in this version</div>
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
                                                                {item.isCurrent && <Badge color="blue">Current</Badge>}
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
                                                items={paginatedSelectedFiles}
                                                pagination={
                                                    <Pagination
                                                        currentPageIndex={currentSelectedFilesPage}
                                                        pagesCount={Math.max(1, Math.ceil(filteredSelectedFiles.length / selectedFilesPerPage))}
                                                        onChange={({ detail }) => setCurrentSelectedFilesPage(detail.currentPageIndex)}
                                                        ariaLabels={{
                                                            nextPageLabel: 'Next page',
                                                            previousPageLabel: 'Previous page',
                                                            pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(filteredSelectedFiles.length / selectedFilesPerPage))}`
                                                        }}
                                                    />
                                                }
                                            />
                                        )}
                                    </SpaceBetween>
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
                                        
                                        {loadingFileVersions ? (
                                            <Box textAlign="center" padding="l">
                                                <Spinner size="normal" />
                                                <div>Loading file versions...</div>
                                            </Box>
                                        ) : (
                                            <Table
                                                columnDefinitions={[
                                                    {
                                                        id: 'versionId',
                                                        header: 'Version ID',
                                                        cell: (item: S3FileVersion) => (
                                                            <Box>
                                                                <div style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>
                                                                    {item.versionId}
                                                                </div>
                                                                {item.isLatest && <Badge color="blue">Latest</Badge>}
                                                            </Box>
                                                        )
                                                    },
                                                    {
                                                        id: 'lastModified',
                                                        header: 'Last Modified',
                                                        cell: (item: S3FileVersion) => formatDate(item.lastModified)
                                                    },
                                                    {
                                                        id: 'size',
                                                        header: 'Size',
                                                        cell: (item: S3FileVersion) => formatFileSize(item.size)
                                                    },
                                                    {
                                                        id: 'status',
                                                        header: 'Status',
                                                        cell: (item: S3FileVersion) => item.isArchived ? 'Archived' : 'Active'
                                                    },
                                                    {
                                                        id: 'actions',
                                                        header: 'Actions',
                                                        cell: (item: S3FileVersion) => (
                                                            <Button
                                                                onClick={() => {
                                                                    if (selectedFileForVersions) {
                                                                        handleFileVersionSelection(selectedFileForVersions, item.versionId);
                                                                    }
                                                                }}
                                                                disabled={item.isArchived}
                                                            >
                                                                Select This Version
                                                            </Button>
                                                        )
                                                    }
                                                ]}
                                                items={paginatedFileVersions}
                                                pagination={
                                                    <Pagination
                                                        currentPageIndex={currentFileVersionsPage}
                                                        pagesCount={Math.max(1, Math.ceil(fileVersions.length / fileVersionsPerPage))}
                                                        onChange={({ detail }) => setCurrentFileVersionsPage(detail.currentPageIndex)}
                                                        ariaLabels={{
                                                            nextPageLabel: 'Next page',
                                                            previousPageLabel: 'Previous page',
                                                            pageLabel: pageNumber => `Page ${pageNumber} of ${Math.max(1, Math.ceil(fileVersions.length / fileVersionsPerPage))}`
                                                        }}
                                                    />
                                                }
                                                empty={
                                                    <Box textAlign="center" padding="l">
                                                        <div>No versions found</div>
                                                    </Box>
                                                }
                                            />
                                        )}
                                        
                                        <Box padding="m" textAlign="right">
                                            <Button
                                                onClick={() => setSelectedFileForVersions(null)}
                                                variant="normal"
                                            >
                                                Cancel
                                            </Button>
                                        </Box>
                                    </Container>
                                )}
                            </>
                        )}
                    </SpaceBetween>
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
                            disabled={loading || !comment.trim()}
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
