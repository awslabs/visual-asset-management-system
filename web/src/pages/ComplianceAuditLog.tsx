/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Select from "@cloudscape-design/components/select";
import FormField from "@cloudscape-design/components/form-field";
import Link from "@cloudscape-design/components/link";
import { SelectProps } from "@cloudscape-design/components";
import Synonyms from "../synonyms";
import {
    fetchAuditLog,
    AuditEntry,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";

const eventTypeOptions: SelectProps.Option[] = [
    { label: "All Events", value: "" },
    { label: "Compliance Check", value: "compliance_check" },
    { label: "Schema Bound to Database", value: "schema_bound_to_database" },
    { label: "Schema Bound to Asset", value: "schema_bound_to_asset" },
    { label: "Schema Unbound from Database", value: "schema_unbound_from_database" },
    { label: "Schema Unbound from Asset", value: "schema_unbound_from_asset" },
    { label: "Quarantine Released", value: "quarantine_released" },
    { label: "Exception Granted", value: "exception_granted" },
    { label: "Cascade Triggered", value: "cascade_triggered" },
];

const ComplianceAuditLog: React.FC = () => {
    const [items, setItems] = useState<AuditEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedEventType, setSelectedEventType] = useState<SelectProps.Option>(
        eventTypeOptions[0]
    );

    // Server-side token paging: tokens[i] is the startingToken that fetches page i
    // (tokens[0] is undefined). hasMore tracks whether the loaded page reported a
    // NextToken, which is what Pagination's openEnd renders.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            setLoading(true);
            setError(null);
            const [success, result] = await fetchAuditLog({
                eventType: selectedEventType.value || undefined,
                maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
                startingToken,
            });
            if (success && typeof result !== "string") {
                setItems(result.entries);
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (result.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: result.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setError(typeof result === "string" ? result : "Failed to load audit log");
                setItems([]);
                setHasMore(false);
            }
            setLoading(false);
        },
        [selectedEventType]
    );

    // A reload (or a filter change) restarts the walk from the first page: a token belongs
    // to the query that produced it.
    const loadData = useCallback(() => {
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        setHasMore(false);
        return loadPage(0, undefined);
    }, [loadPage]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        const newIndex = detail.currentPageIndex;
        setCurrentPageIndex(newIndex);
        loadPage(newIndex - 1, tokens[newIndex - 1]);
    };

    return (
        <SpaceBetween size="l">
            {error && (
                <Alert type="error" dismissible onDismiss={() => setError(null)}>
                    {error}
                </Alert>
            )}

            <Table
                header={
                    <Header
                        variant="h1"
                        counter={`(${items.length}${hasMore ? "+" : ""})`}
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <FormField label="Event type">
                                    <Select
                                        selectedOption={selectedEventType}
                                        onChange={({ detail }) =>
                                            setSelectedEventType(detail.selectedOption)
                                        }
                                        options={eventTypeOptions}
                                        filteringType="auto"
                                        ariaLabel="Filter audit entries by event type"
                                    />
                                </FormField>
                                <Button
                                    iconName="refresh"
                                    ariaLabel="Refresh audit log"
                                    onClick={loadData}
                                    loading={loading}
                                />
                            </SpaceBetween>
                        }
                    >
                        Compliance Audit Log
                    </Header>
                }
                loading={loading}
                items={items}
                pagination={
                    <Pagination
                        currentPageIndex={currentPageIndex}
                        pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                        openEnd={hasMore}
                        onChange={handlePageChange}
                        disabled={loading}
                        ariaLabels={{
                            nextPageLabel: "Next page of audit entries",
                            previousPageLabel: "Previous page of audit entries",
                            pageLabel: (pageNumber) => `Page ${pageNumber} of audit entries`,
                        }}
                    />
                }
                empty={
                    <Box textAlign="center" padding="l">
                        <b>No audit entries</b>
                        <Box variant="p" color="inherit">
                            No compliance audit events have been recorded.
                        </Box>
                    </Box>
                }
                columnDefinitions={[
                    {
                        id: "timestamp",
                        header: "Timestamp",
                        cell: (item) =>
                            item.timestamp ? new Date(item.timestamp).toLocaleString() : "-",
                        sortingField: "timestamp",
                    },
                    {
                        id: "eventType",
                        header: "Event Type",
                        cell: (item) => item.eventType || "-",
                        sortingField: "eventType",
                    },
                    {
                        id: "databaseId",
                        header: Synonyms.Database,
                        cell: (item) => item.databaseId || "-",
                        sortingField: "databaseId",
                    },
                    {
                        id: "assetId",
                        header: Synonyms.Asset,
                        cell: (item) =>
                            item.assetId && item.assetId !== "*" ? (
                                <Link
                                    href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                >
                                    {item.assetId}
                                </Link>
                            ) : (
                                item.assetId || "-"
                            ),
                        sortingField: "assetId",
                    },
                    {
                        id: "schemaName",
                        header: "Schema",
                        cell: (item) => item.schemaName || "-",
                    },
                    {
                        id: "actor",
                        header: "Actor",
                        cell: (item) => item.actor || "-",
                        sortingField: "actor",
                    },
                    {
                        id: "details",
                        header: "Details",
                        cell: (item) => {
                            if (!item.details) return "-";
                            try {
                                const parsed =
                                    typeof item.details === "string"
                                        ? JSON.parse(item.details)
                                        : item.details;
                                return JSON.stringify(parsed);
                            } catch {
                                return typeof item.details === "string"
                                    ? item.details
                                    : JSON.stringify(item.details);
                            }
                        },
                    },
                ]}
            />
        </SpaceBetween>
    );
};

export default ComplianceAuditLog;
