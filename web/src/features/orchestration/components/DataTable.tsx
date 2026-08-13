/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    useReactTable,
    getCoreRowModel,
    getSortedRowModel,
    getFilteredRowModel,
    getPaginationRowModel,
    flexRender,
    type ColumnDef,
    type SortingState,
    type ColumnFiltersState,
} from "@tanstack/react-table";
import SearchInput from "./SearchInput";

interface DataTableProps<T> {
    columns: ColumnDef<T, any>[];
    rows: T[];
    onRowContextMenu?: (row: T) => void;
    onRowClick?: (row: T) => void;
    getRowActions?: (row: T) => React.ReactNode;
    pageSize?: number;
    sorting?: boolean;
    filtering?: boolean;
    // Stable per-row identity. Supply it whenever the row set can be re-ordered or refreshed while
    // mounted (a polling list): without it react-table keys rows by position, so React reuses a row's
    // subtree — and any state it owns, such as an open row menu — for whichever row lands there next.
    getRowId?: (row: T, index: number) => string;
    // Client-side pagination. Disable when the caller drives paging externally (e.g. a
    // server-side "Load more") so the table doesn't show a second, conflicting pager.
    paginate?: boolean;
    // Drop the table's own outline + surface, for a table that already sits inside a bordered
    // container (a Card). Without this the table paints a second white box inside the Card's white
    // padding, which against the grey page reads as two different background tones.
    flush?: boolean;
}

function DataTable<T>({
    columns,
    rows,
    onRowContextMenu,
    onRowClick,
    getRowActions,
    pageSize = 10,
    sorting = true,
    filtering = true,
    paginate = true,
    flush = false,
    getRowId,
}: DataTableProps<T>) {
    const [sortingState, setSortingState] = useState<SortingState>([]);
    const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
    const [globalFilter, setGlobalFilter] = useState("");

    const table = useReactTable({
        data: rows,
        columns,
        state: {
            sorting: sortingState,
            columnFilters,
            globalFilter,
        },
        onSortingChange: setSortingState,
        onColumnFiltersChange: setColumnFilters,
        onGlobalFilterChange: setGlobalFilter,
        getCoreRowModel: getCoreRowModel(),
        getSortedRowModel: sorting ? getSortedRowModel() : undefined,
        getFilteredRowModel: filtering ? getFilteredRowModel() : undefined,
        getPaginationRowModel: paginate ? getPaginationRowModel() : undefined,
        getRowId,
        initialState: {
            pagination: {
                pageSize,
            },
        },
    });

    return (
        <div className="w-full">
            {filtering && (
                <div className="mb-3 flex justify-start">
                    <SearchInput
                        value={globalFilter ?? ""}
                        onChange={(e) => setGlobalFilter(e.target.value)}
                    />
                </div>
            )}

            {/* Header/row styling mirrors Cloudscape's table: a light header row with a bottom
                divider and per-row bottom borders (no full grid), muted small-caps header text.
                The sort affordance (arrows) is kept. */}
            <div
                className={
                    flush
                        ? "overflow-x-auto"
                        : "orch-outline overflow-x-auto border border-border-default rounded-lg bg-surface-container"
                }
            >
                <table className="min-w-full border-collapse">
                    <thead>
                        {table.getHeaderGroups().map((headerGroup) => (
                            <tr
                                key={headerGroup.id}
                                className="orch-outline border-b border-border-default"
                            >
                                {headerGroup.headers.map((header) => {
                                    const sortDirection = header.column.getIsSorted() as
                                        | "asc"
                                        | "desc"
                                        | false;
                                    const toggleSort = sorting
                                        ? header.column.getToggleSortingHandler()
                                        : undefined;
                                    return (
                                        <th
                                            key={header.id}
                                            aria-sort={
                                                !toggleSort
                                                    ? undefined
                                                    : sortDirection === "asc"
                                                    ? "ascending"
                                                    : sortDirection === "desc"
                                                    ? "descending"
                                                    : "none"
                                            }
                                            className="px-4 py-2.5 text-left text-sm font-bold uppercase tracking-wide text-text-secondary select-none"
                                        >
                                            {toggleSort ? (
                                                <button
                                                    type="button"
                                                    onClick={toggleSort}
                                                    className="flex items-center gap-1.5 font-bold uppercase tracking-wide text-text-secondary"
                                                >
                                                    {flexRender(
                                                        header.column.columnDef.header,
                                                        header.getContext()
                                                    )}
                                                    <span aria-hidden="true">
                                                        {sortDirection === "asc"
                                                            ? "↑"
                                                            : sortDirection === "desc"
                                                            ? "↓"
                                                            : ""}
                                                    </span>
                                                </button>
                                            ) : (
                                                <div className="flex items-center gap-1.5">
                                                    {flexRender(
                                                        header.column.columnDef.header,
                                                        header.getContext()
                                                    )}
                                                </div>
                                            )}
                                        </th>
                                    );
                                })}
                            </tr>
                        ))}
                    </thead>
                    <tbody>
                        {table.getRowModel().rows.map((row) => (
                            <tr
                                key={row.id}
                                className={`orch-outline border-b border-border-default last:border-0 hover:bg-surface-hover${
                                    onRowClick ? " cursor-pointer" : ""
                                }`}
                                tabIndex={onRowClick ? 0 : undefined}
                                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                                onKeyDown={
                                    onRowClick
                                        ? (e) => {
                                              // Only the row itself activates; a keypress on a
                                              // control inside a cell belongs to that control.
                                              if (e.target !== e.currentTarget) return;
                                              if (e.key === "Enter" || e.key === " ") {
                                                  e.preventDefault();
                                                  onRowClick(row.original);
                                              }
                                          }
                                        : undefined
                                }
                                onContextMenu={(e) => {
                                    if (onRowContextMenu) {
                                        e.preventDefault();
                                        onRowContextMenu(row.original);
                                    }
                                }}
                            >
                                {row.getVisibleCells().map((cell) => (
                                    <td
                                        key={cell.id}
                                        className="px-4 py-2.5 text-sm text-text-primary"
                                    >
                                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination controls (only when the table owns paging) */}
            {paginate && table.getPageCount() > 1 && (
                <div className="flex items-center justify-end gap-3 mt-3 text-sm">
                    <button
                        onClick={() => table.previousPage()}
                        disabled={!table.getCanPreviousPage()}
                        className="px-3 py-1.5 border border-border-input rounded text-text-primary hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Previous
                    </button>
                    <span className="text-text-secondary">
                        Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
                    </span>
                    <button
                        onClick={() => table.nextPage()}
                        disabled={!table.getCanNextPage()}
                        className="px-3 py-1.5 border border-border-input rounded text-text-primary hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Next
                    </button>
                </div>
            )}
        </div>
    );
}

export default DataTable;
