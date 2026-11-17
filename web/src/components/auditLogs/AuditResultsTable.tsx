/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Box from "@cloudscape-design/components/box";
import CollectionPreferences, {
    CollectionPreferencesProps,
} from "@cloudscape-design/components/collection-preferences";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import Table, { TableProps } from "@cloudscape-design/components/table";
import { PAGE_SORTING_NOTE, byNumber, byText, useSortedItems } from "../list/tableSorting";
import OutcomeIndicator from "./OutcomeIndicator";
import { AuditRow } from "./auditSearchTypes";
import { auditTimeMs, clipText, visibleText } from "./auditText";

/** The row's one-line summary: why it ended as it did, what it touched, or the cut-off preview. */
export const rowSummary = (row: AuditRow): string => {
    if (row.parseStatus && row.parseStatus !== "parsed") {
        return row.preview ? clipText(visibleText(row.preview), 160) : `(${row.parseStatus})`;
    }
    if (row.reason) return clipText(visibleText(row.reason), 160);
    if (row.filePath) return clipText(visibleText(row.filePath), 160);
    if (typeof row.itemCount === "number") return `${row.itemCount} items`;
    if (typeof row.fileCount === "number") return `${row.fileCount} files`;
    return "-";
};

const entityText = (row: AuditRow): string =>
    row.entity?.type || row.entity?.id
        ? `${visibleText(row.entity?.type, "")} ${visibleText(row.entity?.id, "")}`.trim()
        : "-";

const timeText = (row: AuditRow): string => {
    if (!row.timestamp) return "-";
    const ms = auditTimeMs(row.timestamp);
    return Number.isNaN(ms) ? visibleText(row.timestamp) : new Date(ms).toLocaleString();
};

/**
 * A cell value on one line. A long value is cut with an ellipsis and shown in full in the tooltip and in
 * the record's details (the Wrap lines preference shows it in full in the table).
 */
const OneLine: React.FC<{ text: string }> = ({ text }) => <span title={text}>{text}</span>;

const COLUMN_IDS = [
    "timestamp",
    "group",
    "action",
    "outcome",
    "user",
    "databaseId",
    "assetId",
    "entity",
    "summary",
] as const;

// The order puts Outcome next to the Action, so it is in view without scrolling at 1280 px with the
// side navigation open (Time, Log group, Action and Outcome together are 655 px wide).
const DEFAULT_VISIBLE = [...COLUMN_IDS];

export interface AuditResultsTableProps {
    rows: AuditRow[];
    loading: boolean;
    /** The row whose details are open, by identity. */
    selected: AuditRow | null;
    onSelect: (row: AuditRow | null) => void;
    pageIndex: number;
    knownPages: number;
    /** Whether the query has more pages than the ones known so far. */
    hasMore: boolean;
    onPageChange: (pageIndex: number) => void;
    /** What the empty table says: nothing run yet, nothing found, or a failed run. */
    emptyState: "notRun" | "noRecords" | "failed";
    /** Called with a function that drops the header sort, for when the server order changes. */
    registerClearSorting?: (clear: () => void) => void;
}

/**
 * The results: one row per record, a column per fact, server paging, and single selection that
 * opens the record's details. Column headers sort the page shown only; the order of the whole
 * result is the form's Order choice. All values are text.
 */
export const AuditResultsTable: React.FC<AuditResultsTableProps> = ({
    rows,
    loading,
    selected,
    onSelect,
    pageIndex,
    knownPages,
    hasMore,
    onPageChange,
    emptyState,
    registerClearSorting,
}) => {
    const { items, sortingProps, clearSorting } = useSortedItems(rows);
    React.useEffect(() => {
        registerClearSorting?.(clearSorting);
    }, [registerClearSorting, clearSorting]);
    const [preferences, setPreferences] = useState<CollectionPreferencesProps.Preferences>({
        // One line per row keeps a page of 100 records short; long values are in the tooltip and details.
        wrapLines: false,
        contentDisplay: COLUMN_IDS.map((id) => ({ id, visible: DEFAULT_VISIBLE.includes(id) })),
    });
    const isPaging = hasMore || pageIndex > 1;

    const columnDefinitions: TableProps.ColumnDefinition<AuditRow>[] = [
        {
            id: "timestamp",
            header: "Time",
            width: 235,
            minWidth: 215,
            cell: (row) => <OneLine text={timeText(row)} />,
            sortingField: "timestamp",
            sortingComparator: byNumber((row) => auditTimeMs(row.timestamp)),
        },
        {
            id: "group",
            header: "Log group",
            width: 125,
            minWidth: 100,
            cell: (row) => <OneLine text={visibleText(row.groupLabel ?? row.groupKey)} />,
            sortingComparator: byText((row) => row.groupLabel ?? row.groupKey),
        },
        {
            id: "action",
            header: "Action",
            width: 170,
            minWidth: 120,
            cell: (row) => <OneLine text={visibleText(row.action ?? row.legacyType)} />,
            sortingComparator: byText((row) => row.action ?? row.legacyType),
        },
        {
            id: "outcome",
            header: "Outcome",
            width: 125,
            minWidth: 110,
            cell: (row) => <OutcomeIndicator outcome={row.outcome} />,
            sortingComparator: byText((row) => row.outcome),
        },
        {
            id: "user",
            header: "User",
            width: 210,
            minWidth: 140,
            cell: (row) => <OneLine text={visibleText(row.user)} />,
            sortingComparator: byText((row) => row.user),
        },
        {
            id: "databaseId",
            header: "Database",
            width: 150,
            minWidth: 100,
            cell: (row) => <OneLine text={visibleText(row.databaseId)} />,
            sortingComparator: byText((row) => row.databaseId),
        },
        {
            id: "assetId",
            header: "Asset",
            width: 150,
            minWidth: 100,
            cell: (row) => <OneLine text={visibleText(row.assetId)} />,
            sortingComparator: byText((row) => row.assetId),
        },
        {
            id: "entity",
            header: "Entity",
            width: 200,
            minWidth: 120,
            cell: (row) => <OneLine text={entityText(row)} />,
            sortingComparator: byText((row) => entityText(row)),
        },
        {
            id: "summary",
            header: "Summary",
            width: 320,
            minWidth: 200,
            cell: (row) => <OneLine text={rowSummary(row)} />,
        },
    ];

    const emptyText =
        emptyState === "notRun"
            ? "Choose filters and a time range, then press Search."
            : emptyState === "failed"
            ? "The records could not be loaded."
            : "No records match this search. Widen the time range or remove a filter.";

    return (
        <Table<AuditRow>
            variant="container"
            wrapLines={preferences.wrapLines}
            resizableColumns
            columnDefinitions={columnDefinitions}
            columnDisplay={preferences.contentDisplay}
            items={items}
            loading={loading}
            loadingText="Loading records"
            trackBy={(row) => row.recordId ?? `${row.timestamp}|${row.action}|${row.user}`}
            selectionType="single"
            selectedItems={selected ? [selected] : []}
            onSelectionChange={({ detail }) => onSelect(detail.selectedItems[0] ?? null)}
            ariaLabels={{
                selectionGroupLabel: "Audit records",
                itemSelectionLabel: (_selection, row) =>
                    `Show details of the ${visibleText(row.action)} record at ${timeText(row)}`,
                allItemsSelectionLabel: () => "Select a record",
                tableLabel: "Audit records",
            }}
            {...sortingProps}
            header={
                <Header
                    variant="h2"
                    counter={rows.length > 0 ? `(${rows.length}${hasMore ? "+" : ""})` : undefined}
                    description={isPaging ? PAGE_SORTING_NOTE : undefined}
                >
                    Records
                </Header>
            }
            pagination={
                <Pagination
                    currentPageIndex={pageIndex}
                    pagesCount={knownPages}
                    openEnd={hasMore}
                    disabled={loading}
                    onChange={({ detail }) => onPageChange(detail.currentPageIndex)}
                    ariaLabels={{
                        nextPageLabel: "Next page of records",
                        previousPageLabel: "Previous page of records",
                        pageLabel: (n) => `Page ${n} of records`,
                    }}
                />
            }
            preferences={
                <CollectionPreferences
                    title="Preferences"
                    confirmLabel="Confirm"
                    cancelLabel="Cancel"
                    preferences={preferences}
                    onConfirm={({ detail }) => setPreferences(detail)}
                    wrapLinesPreference={{
                        label: "Wrap lines",
                        description: "Show long values in full.",
                    }}
                    contentDisplayPreference={{
                        title: "Columns",
                        options: columnDefinitions.map((c) => ({
                            id: c.id as string,
                            label: c.header as string,
                            alwaysVisible: c.id === "timestamp",
                        })),
                    }}
                />
            }
            empty={
                <Box textAlign="center" color="inherit" padding="l">
                    <b>{emptyState === "noRecords" ? "No records" : "No results yet"}</b>
                    <Box variant="p" color="inherit">
                        {emptyText}
                    </Box>
                </Box>
            }
        />
    );
};

export default AuditResultsTable;
