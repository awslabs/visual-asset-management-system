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
    Table,
    Badge,
    Pagination,
    SpaceBetween,
    Toggle
} from '@cloudscape-design/components';

interface SelectedFile {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    isCurrent?: boolean;
    versionMismatch?: boolean;
}

interface SelectedFilesContainerProps {
    selectedFiles: SelectedFile[];
    filteredSelectedFiles: SelectedFile[];
    setSelectedFileForVersions: (file: SelectedFile | null) => void;
    setSelectedFiles: (files: SelectedFile[] | ((prev: SelectedFile[]) => SelectedFile[])) => void;
    currentSelectedFilesPage: number;
    setCurrentSelectedFilesPage: (page: number) => void;
    selectedFilesPerPage: number;
    paginatedSelectedFiles: SelectedFile[];
    showMismatchedOnly: boolean;
    setShowMismatchedOnly: (show: boolean) => void;
}

export const SelectedFilesContainer: React.FC<SelectedFilesContainerProps> = ({
    selectedFiles,
    filteredSelectedFiles,
    setSelectedFileForVersions,
    setSelectedFiles,
    currentSelectedFilesPage,
    setCurrentSelectedFilesPage,
    selectedFilesPerPage,
    paginatedSelectedFiles,
    showMismatchedOnly,
    setShowMismatchedOnly
}) => {
    return (
        <Container header={<Header variant="h3">Selected Files</Header>}>
            {selectedFiles.length > 0 && (
                <SpaceBetween direction="vertical" size="s">
                    <SpaceBetween direction="horizontal" size="s">
                        <Toggle
                            onChange={({ detail }) => setShowMismatchedOnly(detail.checked)}
                            checked={showMismatchedOnly}
                        >
                            Filter to versions not in current asset version
                        </Toggle>
                    </SpaceBetween>
                </SpaceBetween>
            )}
            
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
                            cell: (item: SelectedFile) => (
                                <Box>
                                    <div>{item.relativeKey.split('/').pop() || item.relativeKey}</div>
                                    {item.versionMismatch && (
                                        <Badge color="blue">Version Mismatch</Badge>
                                    )}
                                </Box>
                            )
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
    );
};
