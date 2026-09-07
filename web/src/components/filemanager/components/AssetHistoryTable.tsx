/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Table from "@cloudscape-design/components/table";
import Pagination from "@cloudscape-design/components/pagination";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Header from "@cloudscape-design/components/header";
import Popover from "@cloudscape-design/components/popover";
import { fetchAssetHistory } from "../../../services/APIService";

// One asset lifecycle history record. assetSnapshot is open-schema: render
// whatever keys are present so future snapshot fields display unchanged.
export interface AssetHistoryRecord {
    historyRecordId: string;
    databaseId: string;
    assetId: string;
    recordDate: string;
    changeSource: string;
    changeUserId: string;
    assetSnapshot: Record<string, any>;
    migratedRecord?: boolean;
}

interface AssetHistoryTableProps {
    databaseId: string;
    assetId: string;
    visible?: boolean;
}

const PAGE_SIZE = 20;

const CHANGE_SOURCE_LABELS: Record<string, string> = {
    create: "Created",
    createDirect: "Created (S3 Sync)",
    edit: "Edited",
    archive: "Archived",
    unarchive: "Unarchived",
    unarchiveDirect: "Unarchived (S3 Sync)",
    permanentDelete: "Permanently Deleted",
};

const formatDate = (dateString: string): string => {
    try {
        const date = new Date(dateString);
        return isNaN(date.getTime()) ? dateString : date.toLocaleString();
    } catch (e) {
        return dateString;
    }
};

const formatSnapshotValue = (value: any): string => {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "object") return JSON.stringify(value);
    return `${value}`;
};

export const AssetHistoryTable: React.FC<AssetHistoryTableProps> = ({
    databaseId,
    assetId,
    visible = true,
}) => {
    // Server-side token paging: pages[i] caches page i's records; tokens[i]
    // is the startingToken used to fetch page i (tokens[0] is undefined).
    const [pages, setPages] = useState<Record<number, AssetHistoryRecord[]>>({});
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            setLoading(true);
            setError(null);
            try {
                const result = await fetchAssetHistory({
                    databaseId,
                    assetId,
                    pageSize: PAGE_SIZE,
                    startingToken,
                });
                if (!result || result[0] === false) {
                    setError((result && result[1]) || "Failed to load asset history");
                    return;
                }
                const response = result[1];
                setPages((prev) => ({ ...prev, [pageNumber]: response.Items || [] }));
                if (response.NextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: response.NextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } catch (err: any) {
                setError(err?.message || "Failed to load asset history");
            } finally {
                setLoading(false);
            }
        },
        [databaseId, assetId]
    );

    // Initial load / reload when shown
    useEffect(() => {
        if (visible && databaseId && assetId) {
            setPages({});
            setTokens({});
            setCurrentPageIndex(1);
            setHasMore(false);
            loadPage(0, undefined);
        }
    }, [visible, databaseId, assetId, loadPage]);

    const handlePageChange = ({ detail }: any) => {
        const newIndex = detail.currentPageIndex;
        const pageNumber = newIndex - 1;
        setCurrentPageIndex(newIndex);
        if (!pages[pageNumber] && tokens[pageNumber] !== undefined) {
            loadPage(pageNumber, tokens[pageNumber]);
        }
    };

    const currentRecords = pages[currentPageIndex - 1] || [];
    const knownPages = Object.keys(pages).length;
    const totalLoaded = Object.keys(pages).reduce((sum, key) => sum + pages[+key].length, 0);

    if (error) {
        return (
            <Alert type="error" statusIconAriaLabel="Error">
                {error}
            </Alert>
        );
    }

    return (
        <Box>
            <Table
                header={
                    <Header
                        variant="h3"
                        counter={`(${totalLoaded}${hasMore ? "+" : ""})`}
                        description="Asset history sorted by date (newest first)"
                    >
                        History
                    </Header>
                }
                columnDefinitions={[
                    {
                        id: "recordDate",
                        header: "Date",
                        cell: (item: AssetHistoryRecord) => formatDate(item.recordDate),
                        minWidth: 180,
                    },
                    {
                        id: "changeSource",
                        header: "Action",
                        cell: (item: AssetHistoryRecord) => (
                            <Box>
                                {CHANGE_SOURCE_LABELS[item.changeSource] || item.changeSource}
                                {item.migratedRecord && (
                                    <span style={{ marginLeft: "8px" }}>
                                        <Badge color="grey">migrated</Badge>
                                    </span>
                                )}
                            </Box>
                        ),
                        minWidth: 160,
                    },
                    {
                        id: "changeUserId",
                        header: "User",
                        cell: (item: AssetHistoryRecord) => item.changeUserId,
                        minWidth: 140,
                    },
                    {
                        id: "details",
                        header: "Details",
                        cell: (item: AssetHistoryRecord) => {
                            const entries = Object.entries(item.assetSnapshot || {});
                            if (entries.length === 0) return "-";
                            return (
                                <Popover
                                    dismissButton={false}
                                    position="top"
                                    size="large"
                                    triggerType="text"
                                    content={
                                        <Box>
                                            {entries.map(([key, value]) => (
                                                <div key={key}>
                                                    <strong>{key}:</strong>{" "}
                                                    {formatSnapshotValue(value)}
                                                </div>
                                            ))}
                                        </Box>
                                    }
                                >
                                    {item.assetSnapshot?.assetName || `${entries.length} fields`}
                                </Popover>
                            );
                        },
                    },
                ]}
                items={currentRecords}
                loading={loading}
                loadingText="Loading history"
                empty={
                    <Box textAlign="center" color="inherit">
                        <b>No history records</b>
                    </Box>
                }
            />
            <Box textAlign="center" padding={{ top: "s" }}>
                <Pagination
                    currentPageIndex={currentPageIndex}
                    pagesCount={hasMore ? knownPages + 1 : knownPages}
                    openEnd={hasMore}
                    onChange={handlePageChange}
                />
            </Box>
        </Box>
    );
};

export default AssetHistoryTable;
