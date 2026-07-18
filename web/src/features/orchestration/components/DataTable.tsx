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

interface DataTableProps<T> {
    columns: ColumnDef<T, any>[];
    rows: T[];
    onRowContextMenu?: (row: T) => void;
    getRowActions?: (row: T) => React.ReactNode;
    pageSize?: number;
    sorting?: boolean;
    filtering?: boolean;
}

function DataTable<T>({
    columns,
    rows,
    onRowContextMenu,
    getRowActions,
    pageSize = 10,
    sorting = true,
    filtering = true,
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
        getPaginationRowModel: getPaginationRowModel(),
        initialState: {
            pagination: {
                pageSize,
            },
        },
    });

    return (
        <div className="w-full">
            {filtering && (
                <div className="mb-4">
                    <input
                        type="text"
                        value={globalFilter ?? ""}
                        onChange={(e) => setGlobalFilter(e.target.value)}
                        placeholder="Filter..."
                        className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    />
                </div>
            )}

            <div className="overflow-x-auto">
                <table className="min-w-full border-collapse border border-gray-300 dark:border-gray-700">
                    <thead className="bg-gray-100 dark:bg-gray-800">
                        {table.getHeaderGroups().map((headerGroup) => (
                            <tr key={headerGroup.id}>
                                {headerGroup.headers.map((header) => (
                                    <th
                                        key={header.id}
                                        className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100 cursor-pointer select-none"
                                        onClick={header.column.getToggleSortingHandler()}
                                    >
                                        <div className="flex items-center gap-2">
                                            {flexRender(
                                                header.column.columnDef.header,
                                                header.getContext()
                                            )}
                                            {sorting && (
                                                <span className="text-gray-500 dark:text-gray-400">
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
                                className="hover:bg-gray-50 dark:hover:bg-gray-800"
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
                                        className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100"
                                    >
                                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination controls */}
            <div className="flex items-center gap-4 mt-4">
                <button
                    onClick={() => table.previousPage()}
                    disabled={!table.getCanPreviousPage()}
                    className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    Previous
                </button>
                <span className="text-gray-900 dark:text-gray-100">
                    Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
                </span>
                <button
                    onClick={() => table.nextPage()}
                    disabled={!table.getCanNextPage()}
                    className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    Next
                </button>
            </div>
        </div>
    );
}

export default DataTable;
