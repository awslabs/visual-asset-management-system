/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import {
    Box,
    Container,
    Header,
    Spinner,
    Table,
    Toggle,
    Checkbox,
    Badge,
    Pagination
} from '@cloudscape-design/components';
import { SpaceBetween } from '@cloudscape-design/components';

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

interface AvailableFilesContainerProps {
    loadingFiles: boolean;
    filteredS3Files: S3File[];
    selectedFiles: SelectedFile[];
    showArchived: boolean;
    showMismatchedOnly: boolean;
    setShowArchived: (show: boolean) => void;
    setShowMismatchedOnly: (show: boolean) => void;
    handleFileSelection: (file: S3File, selected: boolean) => void;
    normalizePath: (path: string) => string;
    formatFileSize: (size?: number) => string;
    formatDate: (dateString?: string) => string;
    currentAvailableFilesPage: number;
    setCurrentAvailableFilesPage: (page: number) => void;
    availableFilesPerPage: number;
    paginatedAvailableFiles: S3File[];
}

export const AvailableFilesContainer: React.FC<AvailableFilesContainerProps> = ({
    loadingFiles,
    filteredS3Files,
    selectedFiles,
    showArchived,
    showMismatchedOnly,
    setShowArchived,
    setShowMismatchedOnly,
    handleFileSelection,
    normalizePath,
    formatFileSize,
    formatDate,
    currentAvailableFilesPage,
    setCurrentAvailableFilesPage,
    availableFilesPerPage,
    paginatedAvailableFiles
}) => {
    return (
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
                        Filter to versions not in current asset version
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
    );
};
