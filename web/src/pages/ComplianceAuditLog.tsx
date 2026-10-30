/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import { VAMS_PAGE_PADDING } from "../components/compliance/rowActions";
import BreakableIdentifier from "../components/compliance/BreakableIdentifier";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import FormField from "@cloudscape-design/components/form-field";
import DatePicker from "@cloudscape-design/components/date-picker";
import Input from "@cloudscape-design/components/input";
import {
    DatabaseFilter,
    TextFilterInput,
    useLatestRequest,
    useUrlFilters,
} from "../components/compliance/listingFilters";
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

/** The listing's server-side filters, kept in the URL. Dates are days (YYYY-MM-DD, UTC). */
export const AUDIT_FILTER_KEYS = [
    "eventType",
    "databaseId",
    "assetId",
    "text",
    "from",
    "to",
] as const;

/** A day filter as the inclusive timestamp bound the route takes. */
export const dayBound = (day: string, end: boolean): string | undefined =>
    day ? `${day}T${end ? "23:59:59" : "00:00:00"}Z` : undefined;

/**
 * Minimum widths (px) of the two columns that hold identifiers. Without them the wrapping table
 * gives Event Type and Details the least width of all and the Actor or Database column the
 * spare width, so an event type took three lines and a detail key two; with them the common event
 * types ("compliance_check") and detail keys ("pipelineRulesPending") fit on one line at 1280 px
 * and the table still fits the page.
 */
export const AUDIT_EVENT_TYPE_MIN_WIDTH = 170;
export const AUDIT_DETAILS_MIN_WIDTH = 210;

const ComplianceAuditLog: React.FC = () => {
    // Asset ids link to the asset page only when the caller may open it.
    const { canOpenAsset } = useAssetPageAccess();
    const [items, setItems] = useState<AuditEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filters, setFilters] = useUrlFilters(AUDIT_FILTER_KEYS);
    const selectedEventType =
        eventTypeOptions.find((option) => option.value === filters.eventType) ||
        eventTypeOptions[0];
    const [assetDraft, setAssetDraft] = useState(filters.assetId);

    // Server-side token paging: tokens[i] is the startingToken that fetches page i
    // (tokens[0] is undefined). hasMore tracks whether the loaded page reported a
    // NextToken, which is what Pagination's openEnd renders.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    // A load is dropped when a later one has begun (other filters, another page), so the rows
    // shown are always the latest request's.
    const beginLoad = useLatestRequest();
    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            const isCurrent = beginLoad();
            setLoading(true);
            setError(null);
            const [success, result] = await fetchAuditLog({
                eventType: filters.eventType || undefined,
                maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
                startingToken,
                ...(filters.databaseId ? { databaseId: filters.databaseId } : {}),
                ...(filters.databaseId && filters.assetId ? { assetId: filters.assetId } : {}),
                ...(filters.text ? { text: filters.text } : {}),
                ...(filters.from ? { startDate: dayBound(filters.from, false) } : {}),
                ...(filters.to ? { endDate: dayBound(filters.to, true) } : {}),
            });
            if (!isCurrent()) return;
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
        [
            beginLoad,
            filters.eventType,
            filters.databaseId,
            filters.assetId,
            filters.text,
            filters.from,
            filters.to,
        ]
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
                    // Columns wrap, so the trail fits the page at 1280 px instead of scrolling.
                    wrapLines
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
                                                setFilters({
                                                    eventType: detail.selectedOption.value || "",
                                                })
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
                    filter={
                        <SpaceBetween direction="horizontal" size="xs">
                            <DatabaseFilter
                                value={filters.databaseId}
                                onChange={(databaseId) =>
                                    setFilters({
                                        databaseId,
                                        assetId: databaseId ? filters.assetId : "",
                                    })
                                }
                            />
                            <Input
                                value={assetDraft}
                                disabled={!filters.databaseId}
                                placeholder={`${Synonyms.Asset} ID (in the ${Synonyms.database})`}
                                ariaLabel={`Filter by ${Synonyms.asset} ID`}
                                onChange={({ detail }) => setAssetDraft(detail.value)}
                                onBlur={() => setFilters({ assetId: assetDraft.trim() })}
                                onKeyDown={({ detail }) => {
                                    if (detail.key === "Enter") {
                                        setFilters({ assetId: assetDraft.trim() });
                                    }
                                }}
                            />
                            <TextFilterInput
                                value={filters.text}
                                onCommit={(text) => setFilters({ text })}
                                placeholder={`Search ${Synonyms.asset} name, ID or actor`}
                                ariaLabel="Search audit entries"
                            />
                            <DatePicker
                                value={filters.from}
                                placeholder="From (YYYY/MM/DD)"
                                ariaLabel="Entries from"
                                onChange={({ detail }) => setFilters({ from: detail.value })}
                            />
                            <DatePicker
                                value={filters.to}
                                placeholder="To (YYYY/MM/DD)"
                                ariaLabel="Entries to"
                                onChange={({ detail }) => setFilters({ to: detail.value })}
                            />
                        </SpaceBetween>
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
                                {hasMore
                                    ? "None of the entries read for this page are visible to you. Open the next page to continue."
                                    : "No compliance audit events have been recorded."}
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
                            minWidth: AUDIT_EVENT_TYPE_MIN_WIDTH,
                            // An event type is one long identifier (schema_bound_to_database): it
                            // wraps at its underscores only. The schema the event concerns is shown
                            // beneath it, in one column, so the table fits the page at 1280 px.
                            cell: (item) => (
                                <SpaceBetween size="xxxs">
                                    {item.eventType ? (
                                        <BreakableIdentifier text={item.eventType} />
                                    ) : (
                                        "-"
                                    )}
                                    {item.schemaName && item.schemaName !== "-" && (
                                        <Box variant="small" color="text-body-secondary">
                                            {item.schemaName}
                                        </Box>
                                    )}
                                </SpaceBetween>
                            ),
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
                            cell: (item) => {
                                const label =
                                    item.assetId && item.assetId !== "*" && canOpenAsset ? (
                                        <Link
                                            href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                        >
                                            {item.assetName || item.assetId}
                                        </Link>
                                    ) : (
                                        item.assetName || item.assetId || "-"
                                    );
                                // The name recorded with the entry, with the id beneath it.
                                return item.assetName ? (
                                    <SpaceBetween size="xxxs">
                                        {label}
                                        <Box variant="small" color="text-body-secondary">
                                            {item.assetId}
                                        </Box>
                                    </SpaceBetween>
                                ) : (
                                    label
                                );
                            },
                            sortingField: "assetId",
                        },
                        {
                            id: "actor",
                            header: "Actor",
                            // A user id is often an e-mail address with no break opportunity of
                            // its own; it wraps after its `@` and `.` so the column stays narrow
                            // enough for the table to fit the page at 1280 px.
                            cell: (item) =>
                                item.actor ? <BreakableIdentifier text={item.actor} userId /> : "-",
                            sortingField: "actor",
                        },
                        {
                            id: "details",
                            header: "Details",
                            minWidth: AUDIT_DETAILS_MIN_WIDTH,
                            cell: (item) => {
                                const lines = auditDetailLines(item.details);
                                if (lines.length === 0) return "-";
                                return (
                                    <SpaceBetween size="xxxs">
                                        {lines.map((line) => (
                                            <Box key={line.key} fontSize="body-s">
                                                {/* Keys are camelCase, values often identifiers
                                                    too: both wrap at word boundaries only. */}
                                                <Box variant="span" fontWeight="bold">
                                                    <BreakableIdentifier text={line.key} />
                                                </Box>
                                                : <BreakableIdentifier text={line.value} />
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
