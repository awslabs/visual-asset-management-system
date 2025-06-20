/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import {
    Box,
    Button,
    Container,
    Header,
    Spinner,
    Table,
    Badge,
    Pagination
} from '@cloudscape-design/components';

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

interface FileVersionsContainerProps {
    selectedFileForVersions: SelectedFile | null;
    fileVersions: S3FileVersion[];
    loadingFileVersions: boolean;
    handleFileVersionSelection: (file: SelectedFile, versionId: string) => void;
    setSelectedFileForVersions: (file: SelectedFile | null) => void;
    formatFileSize: (size?: number) => string;
    formatDate: (dateString?: string) => string;
    currentFileVersionsPage: number;
    setCurrentFileVersionsPage: (page: number) => void;
    fileVersionsPerPage: number;
    paginatedFileVersions: S3FileVersion[];
}

export const FileVersionsContainer: React.FC<FileVersionsContainerProps> = ({
    selectedFileForVersions,
    fileVersions,
    loadingFileVersions,
    handleFileVersionSelection,
    setSelectedFileForVersions,
    formatFileSize,
    formatDate,
    currentFileVersionsPage,
    setCurrentFileVersionsPage,
    fileVersionsPerPage,
    paginatedFileVersions
}) => {
    if (!selectedFileForVersions) {
        return null;
    }

    return (
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
    );
};
