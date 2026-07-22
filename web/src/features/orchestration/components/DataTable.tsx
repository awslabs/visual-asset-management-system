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
    // Client-side pagination. Disable when the caller drives paging externally (e.g. a
    // server-side "Load more") so the table doesn't show a second, conflicting pager.
    paginate?: boolean;
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
            <div className="overflow-x-auto border border-border-default rounded-lg">
                <table className="min-w-full border-collapse">
                    <thead>
                        {table.getHeaderGroups().map((headerGroup) => (
                            <tr key={headerGroup.id} className="border-b border-border-default">
                                {headerGroup.headers.map((header) => (
                                    <th
                                        key={header.id}
                                        className="px-4 py-2.5 text-left text-xs font-bold uppercase tracking-wide text-text-secondary cursor-pointer select-none"
                                        onClick={header.column.getToggleSortingHandler()}
                                    >
                                        <div className="flex items-center gap-1.5">
                                            {flexRender(
                                                header.column.columnDef.header,
                                                header.getContext()
                                            )}
                                            {sorting && (
                                                <span className="text-text-secondary">
                                                    {{
                                                        asc: "↑",
                                                        desc: "↓",
                                                    }[header.column.getIsSorted() as string] ?? ""}
                                                </span>
                                            )}
                                        </div>
                                    </th>
                                ))}
                            </tr>
                        ))}
                    </thead>
                    <tbody>
                        {table.getRowModel().rows.map((row) => (
                            <tr
                                key={row.id}
                                className={`border-b border-border-default last:border-0 hover:bg-surface-hover${
                                    onRowClick ? " cursor-pointer" : ""
                                }`}
                                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
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
