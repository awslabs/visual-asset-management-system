/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    Box,
    Button,
    Container,
    Header,
    Spinner,
    Table,
    Badge,
    Pagination,
} from "@cloudscape-design/components";

interface AssetVersion {
    Version: number;
    DateModified?: string;
    Comment?: string;
    createdBy?: string;
    isCurrent?: boolean;
}

interface BaseVersionSelectionContainerProps {
    versions: AssetVersion[];
    loadingVersions: boolean;
    selectedVersion: AssetVersion | null;
    selectVersionAsBase: (version: AssetVersion) => void;
    formatDate: (dateString?: string) => string;
    currentVersionPage: number;
    setCurrentVersionPage: (page: number) => void;
    versionsPerPage: number;
    paginatedVersions: AssetVersion[];
}

export const BaseVersionSelectionContainer: React.FC<BaseVersionSelectionContainerProps> = ({
    versions,
    loadingVersions,
    selectedVersion,
    selectVersionAsBase,
    formatDate,
    currentVersionPage,
    setCurrentVersionPage,
    versionsPerPage,
    paginatedVersions,
}) => {
    return (
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
                            id: "version",
                            header: "Version",
                            cell: (item: AssetVersion) => (
                                <Box>
                                    <div
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            gap: "8px",
                                        }}
                                    >
                                        v{item.Version}
                                        {item.isCurrent && <Badge color="blue">Current</Badge>}
                                    </div>
                                </Box>
                            ),
                        },
                        {
                            id: "dateModified",
                            header: "Date Created",
                            cell: (item: AssetVersion) => formatDate(item.DateModified),
                        },
                        {
                            id: "createdBy",
                            header: "Created By",
                            cell: (item: AssetVersion) => item.createdBy || "System",
                        },
                        {
                            id: "comment",
                            header: "Comment",
                            cell: (item: AssetVersion) => item.Comment || "-",
                        },
                        {
                            id: "actions",
                            header: "Actions",
                            cell: (item: AssetVersion) => (
                                <Button
                                    onClick={() => selectVersionAsBase(item)}
                                    variant={
                                        selectedVersion?.Version === item.Version
                                            ? "primary"
                                            : "normal"
                                    }
                                >
                                    {selectedVersion?.Version === item.Version
                                        ? "Selected"
                                        : "Use as Base"}
                                </Button>
                            ),
                        },
                    ]}
                    items={paginatedVersions}
                    pagination={
                        <Pagination
                            currentPageIndex={currentVersionPage}
                            pagesCount={Math.max(1, Math.ceil(versions.length / versionsPerPage))}
                            onChange={({ detail }) =>
                                setCurrentVersionPage(detail.currentPageIndex)
                            }
                            ariaLabels={{
                                nextPageLabel: "Next page",
                                previousPageLabel: "Previous page",
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of ${Math.max(
                                        1,
                                        Math.ceil(versions.length / versionsPerPage)
                                    )}`,
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
    );
};
