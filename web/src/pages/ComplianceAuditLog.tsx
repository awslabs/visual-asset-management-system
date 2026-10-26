/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import { VAMS_PAGE_PADDING } from "../components/compliance/rowActions";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import FormField from "@cloudscape-design/components/form-field";
import Link from "@cloudscape-design/components/link";
import Synonyms from "../synonyms";
import {
    fetchAuditLog,
    AuditEntry,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import { useAssetPageAccess } from "../hooks/useAssetPageAccess";

/** One value of an audit entry's details as a reader sees it: text as is, lists joined, objects as
 * `key value` pairs, and anything else as JSON. */
const detailValue = (value: any): string => {
    if (value === null || value === undefined) return "-";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return value.map(detailValue).join(", ");
    if (typeof value === "object") {
        return Object.entries(value)
            .map(([key, inner]) => `${key} ${detailValue(inner)}`)
            .join(", ");
    }
    return JSON.stringify(value);
};

/**
 * An audit entry's details (stored JSON-encoded) as one line per field, for the Details column;
 * details that are not a JSON object are shown as one `details` line.
 */
export const auditDetailLines = (
    details: string | Record<string, any> | undefined | null
): { key: string; value: string }[] => {
    if (details === undefined || details === null || details === "") return [];
    let parsed: any = details;
    if (typeof details === "string") {
        try {
            parsed = JSON.parse(details);
        } catch {
            return [{ key: "details", value: details }];
        }
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return [{ key: "details", value: detailValue(parsed) }];
    }
    return Object.entries(parsed).map(([key, value]) => ({ key, value: detailValue(value) }));
};

/**
 * Every event type the audit trail records, in the order the filter offers them. The list is the
 * user guide's event-type table (docs/user-guide/compliance.md, "Event types"); the backend test
 * test_compliance_audit_event_types_documented.py holds that table to the types the code writes,
 * and ComplianceAuditLog.test.tsx holds this list to the table.
 */
export const eventTypeOptions: SelectProps.Option[] = [
    { label: "All Events", value: "" },
    { label: "Compliance Check", value: "compliance_check" },
    { label: "Evaluation Error", value: "evaluation_error" },
    { label: `Schema Bound to ${Synonyms.Database}`, value: "schema_bound_to_database" },
    { label: `Schema Bound to ${Synonyms.Asset}`, value: "schema_bound_to_asset" },
    { label: `Schema Unbound from ${Synonyms.Database}`, value: "schema_unbound_from_database" },
    { label: `Schema Unbound from ${Synonyms.Asset}`, value: "schema_unbound_from_asset" },
    { label: "Quarantine Released", value: "quarantine_released" },
    { label: "Exception Granted", value: "exception_granted" },
    { label: "Exception Revoked", value: "exception_revoked" },
    { label: "Exception Superseded", value: "exception_superseded" },
    { label: "Schema Deleted", value: "schema_deleted" },
    { label: "Cascade Triggered", value: "cascade_triggered" },
    { label: "Cascade Auto-Triggered", value: "cascade_auto_triggered" },
    { label: "Cascade Approved", value: "cascade_approved" },
    { label: "Cascade Rejected", value: "cascade_rejected" },
    { label: "Cascade Timed Out", value: "cascade_timed_out" },
    { label: "Cascade Completed", value: "cascade_completed" },
    { label: `${Synonyms.Asset} Deleted`, value: "asset_deleted" },
];

const ComplianceAuditLog: React.FC = () => {
    // Asset ids link to the asset page only when the caller may open it.
    const { canOpenAsset } = useAssetPageAccess();
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
        // The page margins every VAMS list page uses (VAMS_PAGE_PADDING).
        <Box padding={VAMS_PAGE_PADDING}>
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
                                item.assetId && item.assetId !== "*" && canOpenAsset ? (
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
                                const lines = auditDetailLines(item.details);
                                if (lines.length === 0) return "-";
                                return (
                                    <SpaceBetween size="xxxs">
                                        {lines.map((line) => (
                                            <Box key={line.key} fontSize="body-s">
                                                <Box variant="span" fontWeight="bold">
                                                    {line.key}
                                                </Box>
                                                : {line.value}
                                            </Box>
                                        ))}
                                    </SpaceBetween>
                                );
                            },
                        },
                    ]}
                />
            </SpaceBetween>
        </Box>
    );
};

export default ComplianceAuditLog;
