/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { type ColumnDef } from "@tanstack/react-table";
import DataTable from "../components/DataTable";
import StatusBadge from "../components/StatusBadge";
import Dialog from "../components/Dialog";
import ExecutionRowActions from "./ExecutionRowActions";
import ExecutionQuickView from "./ExecutionQuickView";
import ExecuteWorkflowButton from "./ExecuteWorkflowButton";
import { control, btnSecondary } from "../components/controlStyles";
import RefreshButton from "../components/RefreshButton";
import SearchInput from "../components/SearchInput";
import { useExecutions, useExecutionActions, type ExecutionScope } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import type { Execution } from "../types";

interface ExecutionsBoardProps {
    scope: ExecutionScope;
}

const ExecutionsBoard: React.FC<ExecutionsBoardProps> = ({ scope }) => {
    const navigate = useNavigate();
    const [quickViewExecutionId, setQuickViewExecutionId] = useState<string | null>(null);
    const [abortConfirm, setAbortConfirm] = useState<{
        executionId: string;
        isGroup: boolean;
    } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
    const [deleteTypedValue, setDeleteTypedValue] = useState("");
    const [searchText, setSearchText] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [triggerFilter, setTriggerFilter] = useState("");
    // Time-window filter (executions started within the window). A preset ("90"/"120"/"180" days)
    // resolves to a filterStartDate N days before now; "custom" reveals an explicit from/to date
    // range. Default is the last 90 days (matching the backend default).
    const [dateWindow, setDateWindow] = useState<"90" | "120" | "180" | "custom">("90");
    const [startDateFilter, setStartDateFilter] = useState("");
    const [endDateFilter, setEndDateFilter] = useState("");

    // Server-side filters (the global-list handler AND-s these; empty values are omitted).
    // The backend already defaults to 90 days, so the "90" preset sends no explicit start date.
    const filters = useMemo(() => {
        const f: Record<string, string> = {};
        if (statusFilter) f.status = statusFilter;
        if (triggerFilter) f.triggerType = triggerFilter;
        if (dateWindow === "custom") {
            if (startDateFilter) f.filterStartDate = `${startDateFilter}T00:00:00Z`;
            if (endDateFilter) f.filterEndDate = `${endDateFilter}T23:59:59Z`;
        } else if (dateWindow !== "90") {
            // 120 / 180 day presets: N days before now (the 90-day default needs no override).
            const cutoff = new Date(Date.now() - Number(dateWindow) * 24 * 60 * 60 * 1000);
            f.filterStartDate = cutoff.toISOString().replace(/\.\d{3}Z$/, "Z");
        }
        return f;
    }, [statusFilter, triggerFilter, dateWindow, startDateFilter, endDateFilter]);

    const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage, refetch, isFetching } =
        useExecutions(scope, filters, {});
    const { abortExecution, rerunExecution, permanentDeleteExecution } = useExecutionActions();
    const { can } = useAllowedRoutes();

    const executions = React.useMemo(
        () => data?.pages?.flatMap((page: any) => page.Items) ?? [],
        [data]
    );

    // Sort: non-terminal first, then by start date descending
    const sortedExecutions = useMemo(() => {
        const terminal = ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT", "COMPLETE"];
        const nonTerminal = ["NEW", "RUNNING"];

        return [...executions].sort((a, b) => {
            const aIsNonTerminal = nonTerminal.includes(a.executionStatus);
            const bIsNonTerminal = nonTerminal.includes(b.executionStatus);

            // Non-terminal rows first
            if (aIsNonTerminal && !bIsNonTerminal) return -1;
            if (!aIsNonTerminal && bIsNonTerminal) return 1;

            // Within same terminal status, sort by start date desc
            const aDate = new Date(a.executionStartDate || 0).getTime();
            const bDate = new Date(b.executionStartDate || 0).getTime();
            return bDate - aDate;
        });
    }, [executions]);

    // Client-side text search over the loaded rows (id / workflow / database). Server-side filters
    // (status, trigger, date window) are applied via the query; this narrows what's already loaded.
    const visibleExecutions = useMemo(() => {
        const q = searchText.trim().toLowerCase();
        if (!q) return sortedExecutions;
        return sortedExecutions.filter((e) =>
            [e.workflowExecutionId, e.workflowId, e.workflowDatabaseId].some((v) =>
                (v || "").toLowerCase().includes(q)
            )
        );
    }, [sortedExecutions, searchText]);

    const handleAbort = async (executionId: string, groupId?: string) => {
        try {
            await abortExecution.mutateAsync({ executionId, groupId });
            setAbortConfirm(null);
        } catch (err) {
            console.error("Failed to abort execution:", err);
        }
    };

    const handleRerun = async (executionId: string, groupId?: string) => {
        try {
            await rerunExecution.mutateAsync({ executionId, executionGroupId: groupId });
        } catch (err) {
            console.error("Failed to rerun execution:", err);
        }
    };

    const handlePermanentDelete = async (executionId: string) => {
        try {
            await permanentDeleteExecution.mutateAsync(executionId);
            setDeleteConfirm(null);
            setDeleteTypedValue("");
        } catch (err) {
            console.error("Failed to delete execution:", err);
        }
    };

    const formatDate = (dateStr?: string) => {
        if (!dateStr) return "—";
        try {
            return new Date(dateStr).toLocaleString();
        } catch {
            return dateStr;
        }
    };

    const calculateDuration = (start?: string, stop?: string) => {
        if (!start) return "—";
        if (!stop) return "In progress";
        try {
            const startTime = new Date(start).getTime();
            const stopTime = new Date(stop).getTime();
            const durationMs = stopTime - startTime;
            const seconds = Math.floor(durationMs / 1000);
            const minutes = Math.floor(seconds / 60);
            const hours = Math.floor(minutes / 60);
            if (hours > 0) return `${hours}h ${minutes % 60}m`;
            if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
            return `${seconds}s`;
        } catch {
            return "—";
        }
    };

    const columns: ColumnDef<Execution>[] = useMemo(
        () => [
            {
                accessorKey: "executionStatus",
                header: "Status",
                cell: ({ row }) => <StatusBadge status={row.original.executionStatus} />,
            },
            {
                accessorKey: "workflowExecutionId",
                header: "Execution ID",
                cell: ({ row }) => (
                    <span className="font-mono text-sm">{row.original.workflowExecutionId}</span>
                ),
            },
            {
                accessorKey: "workflowId",
                header: "Workflow",
                cell: ({ row }) => <span className="text-sm">{row.original.workflowId}</span>,
            },
            {
                accessorKey: "workflowDatabaseId",
                header: "Database",
                cell: ({ row }) => (
                    <span className="text-sm">{row.original.workflowDatabaseId || "—"}</span>
                ),
            },
            {
                accessorKey: "triggerType",
                header: "Trigger",
                cell: ({ row }) => (
                    <div className="text-sm">
                        <div>{row.original.triggerType || "—"}</div>
                        {row.original.triggeredByUserId && (
                            <div className="text-sm text-text-secondary">
                                {row.original.triggeredByUserId}
                            </div>
                        )}
                    </div>
                ),
            },
            {
                accessorKey: "executionStartDate",
                header: "Started",
                cell: ({ row }) => (
                    <span className="text-sm">{formatDate(row.original.executionStartDate)}</span>
                ),
            },
            {
                accessorKey: "executionStopDate",
                header: "Stopped",
                cell: ({ row }) => (
                    <span className="text-sm">{formatDate(row.original.executionStopDate)}</span>
                ),
            },
            {
                header: "Duration",
                cell: ({ row }) => (
                    <span className="text-sm">
                        {calculateDuration(
                            row.original.executionStartDate,
                            row.original.executionStopDate
                        )}
                    </span>
                ),
            },
            {
                header: "Actions",
                cell: ({ row }) => (
                    // Stop propagation so opening the actions menu doesn't also trigger the
                    // row-click quick-view.
                    <div onClick={(e) => e.stopPropagation()}>
                        <ExecutionRowActions
                            execution={row.original}
                            can={can}
                            onView={() => setQuickViewExecutionId(row.original.workflowExecutionId)}
                            onAbort={() =>
                                setAbortConfirm({
                                    executionId: row.original.workflowExecutionId,
                                    isGroup: false,
                                })
                            }
                            onAbortGroup={
                                row.original.executionGroupId
                                    ? () =>
                                          setAbortConfirm({
                                              executionId: row.original.workflowExecutionId,
                                              isGroup: true,
                                          })
                                    : undefined
                            }
                            onRerun={() =>
                                handleRerun(
                                    row.original.workflowExecutionId,
                                    row.original.executionGroupId
                                )
                            }
                            onLogs={() => {
                                navigate(
                                    `/executions/${row.original.workflowExecutionId}?tab=logs`
                                );
                            }}
                            onPermanentDelete={() =>
                                setDeleteConfirm(row.original.workflowExecutionId)
                            }
                            onOpenDetails={() =>
                                navigate(`/executions/${row.original.workflowExecutionId}`)
                            }
                        />
                    </div>
                ),
            },
        ],
        [can, navigate, handleRerun, setQuickViewExecutionId, setAbortConfirm, setDeleteConfirm]
    );

    return (
        <div className="orchestration-root px-6 pb-6 pt-4 space-y-4 bg-surface">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold text-text-primary">Executions</h1>
                {/* Execute sits in the header row (matching Pipelines/Workflows) and is available
                    wherever the board is shown (global page + asset tab). */}
                <ExecuteWorkflowButton scope={scope} />
            </div>

            {/* One aligned filter row (matching Pipelines/Workflows): search + refresh on the left,
                all filter dropdowns + clear on the right. */}
            <div className="flex items-center gap-2 flex-wrap justify-between">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                    />
                    <RefreshButton onClick={() => refetch()} busy={isFetching} />
                </div>
                <div className="flex items-center gap-2 flex-wrap justify-end">
                    <select
                        aria-label="Filter by status"
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
                        className={control}
                    >
                        <option value="">All statuses</option>
                        <option value="RUNNING">Running</option>
                        <option value="SUCCEEDED">Succeeded</option>
                        <option value="FAILED">Failed</option>
                        <option value="ABORTED">Aborted</option>
                        <option value="TIMED_OUT">Timed out</option>
                    </select>
                    <select
                        aria-label="Filter by trigger"
                        value={triggerFilter}
                        onChange={(e) => setTriggerFilter(e.target.value)}
                        className={control}
                    >
                        <option value="">All triggers</option>
                        <option value="Manual">Manual</option>
                        <option value="fileUpload">File upload</option>
                    </select>
                    {/* Time window: preset day-counts (default 90) or a custom from/to range. */}
                    <select
                        aria-label="Time window"
                        value={dateWindow}
                        onChange={(e) => setDateWindow(e.target.value as typeof dateWindow)}
                        className={control}
                    >
                        <option value="90">Last 90 days</option>
                        <option value="120">Last 120 days</option>
                        <option value="180">Last 180 days</option>
                        <option value="custom">Custom range…</option>
                    </select>
                    {dateWindow === "custom" && (
                        <>
                            <input
                                type="date"
                                aria-label="Started on or after"
                                title="Started on or after"
                                value={startDateFilter}
                                max={endDateFilter || undefined}
                                onChange={(e) => setStartDateFilter(e.target.value)}
                                className={control}
                            />
                            <span className="text-sm text-text-secondary">to</span>
                            <input
                                type="date"
                                aria-label="Started on or before"
                                title="Started on or before"
                                value={endDateFilter}
                                min={startDateFilter || undefined}
                                onChange={(e) => setEndDateFilter(e.target.value)}
                                className={control}
                            />
                        </>
                    )}
                    {(statusFilter ||
                        triggerFilter ||
                        dateWindow !== "90" ||
                        startDateFilter ||
                        endDateFilter) && (
                        <button
                            onClick={() => {
                                setStatusFilter("");
                                setTriggerFilter("");
                                setDateWindow("90");
                                setStartDateFilter("");
                                setEndDateFilter("");
                            }}
                            className="px-2 py-1 text-sm text-blue-600 dark:text-blue-400 hover:underline"
                        >
                            Clear
                        </button>
                    )}
                </div>
            </div>

            {isLoading && <div className="text-text-secondary">Loading executions...</div>}

            {!isLoading && visibleExecutions.length === 0 && (
                <div className="text-text-secondary">No executions found.</div>
            )}

            {!isLoading && visibleExecutions.length > 0 && (
                <>
                    <DataTable
                        columns={columns}
                        rows={visibleExecutions}
                        paginate={false}
                        // The board owns the search box (in the filter row); the table's own search
                        // is disabled so it doesn't render a second, redundant search bar.
                        filtering={false}
                        onRowClick={(row) => setQuickViewExecutionId(row.workflowExecutionId)}
                    />
                    {hasNextPage && (
                        <div className="flex justify-center mt-4">
                            <button
                                onClick={() => fetchNextPage()}
                                disabled={isFetchingNextPage}
                                className={btnSecondary}
                            >
                                {isFetchingNextPage ? "Loading more..." : "Load more"}
                            </button>
                        </div>
                    )}
                </>
            )}

            {/* Quick view drawer */}
            {quickViewExecutionId && (
                <ExecutionQuickView
                    open={!!quickViewExecutionId}
                    onClose={() => setQuickViewExecutionId(null)}
                    executionId={quickViewExecutionId}
                />
            )}

            {/* Abort confirmation */}
            {abortConfirm && (
                <Dialog
                    open={!!abortConfirm}
                    onOpenChange={(open) => !open && setAbortConfirm(null)}
                    title={abortConfirm.isGroup ? "Abort Execution Group" : "Abort Execution"}
                    footer={
                        <>
                            <button onClick={() => setAbortConfirm(null)} className={btnSecondary}>
                                Cancel
                            </button>
                            <button
                                onClick={() => {
                                    if (abortConfirm.isGroup) {
                                        const exec = executions.find(
                                            (e) =>
                                                e.workflowExecutionId === abortConfirm.executionId
                                        );
                                        handleAbort(
                                            abortConfirm.executionId,
                                            exec?.executionGroupId
                                        );
                                    } else {
                                        handleAbort(abortConfirm.executionId);
                                    }
                                }}
                                className="px-4 py-2 bg-red-600 text-white rounded"
                            >
                                Abort
                            </button>
                        </>
                    }
                >
                    <p>
                        Are you sure you want to abort{" "}
                        {abortConfirm.isGroup ? "this execution group" : "this execution"}?
                    </p>
                </Dialog>
            )}

            {/* Permanent delete confirmation */}
            {deleteConfirm && (
                <Dialog
                    open={!!deleteConfirm}
                    onOpenChange={(open) => {
                        if (!open) {
                            setDeleteConfirm(null);
                            setDeleteTypedValue("");
                        }
                    }}
                    title="Permanent Delete"
                    footer={
                        <>
                            <button onClick={() => setDeleteConfirm(null)} className={btnSecondary}>
                                Cancel
                            </button>
                            <button
                                onClick={() => handlePermanentDelete(deleteConfirm)}
                                disabled={deleteTypedValue !== "CONFIRM"}
                                className="px-4 py-2 bg-red-600 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                Permanent Delete
                            </button>
                        </>
                    }
                >
                    <p className="mb-4">
                        This will permanently delete the execution record. This action cannot be
                        undone.
                    </p>
                    <p className="font-semibold">
                        Type <code className="bg-surface-secondary px-1">CONFIRM</code> to proceed:
                    </p>
                    <input
                        type="text"
                        placeholder="CONFIRM"
                        value={deleteTypedValue}
                        onChange={(e) => setDeleteTypedValue(e.target.value)}
                        className="mt-2 w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                            if (e.key === "Enter" && deleteTypedValue === "CONFIRM") {
                                handlePermanentDelete(deleteConfirm);
                            }
                        }}
                    />
                </Dialog>
            )}
        </div>
    );
};

export default ExecutionsBoard;
