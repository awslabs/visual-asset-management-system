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
import { useExecutions, useExecutionActions, type ExecutionScope } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import type { Execution } from "../types";

interface ExecutionsBoardProps {
    scope: ExecutionScope;
    groupByWorkflow?: boolean;
}

const ExecutionsBoard: React.FC<ExecutionsBoardProps> = ({ scope }) => {
    const navigate = useNavigate();
    const [quickViewExecutionId, setQuickViewExecutionId] = useState<string | null>(null);
    const [abortConfirm, setAbortConfirm] = useState<{
        executionId: string;
        isGroup: boolean;
    } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
    const [includeArchived, setIncludeArchived] = useState(false);

    const { data: executions = [], isLoading } = useExecutions(scope, {}, {});
    const { abortExecution, rerunExecution, permanentDeleteExecution } = useExecutionActions();
    const { can } = useAllowedRoutes();

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

    const columns: ColumnDef<Execution>[] = [
        {
            accessorKey: "executionStatus",
            header: "Status",
            cell: ({ row }) => <StatusBadge status={row.original.executionStatus} />,
        },
        {
            accessorKey: "workflowExecutionId",
            header: "Execution ID",
            cell: ({ row }) => (
                <span className="font-mono text-xs">{row.original.workflowExecutionId}</span>
            ),
        },
        {
            accessorKey: "workflowId",
            header: "Workflow",
            cell: ({ row }) => (
                <span className="text-sm">{row.original.workflowId}</span>
            ),
        },
        {
            accessorKey: "triggerType",
            header: "Trigger",
            cell: ({ row }) => (
                <div className="text-sm">
                    <div>{row.original.triggerType || "—"}</div>
                    {row.original.triggeredByUserId && (
                        <div className="text-xs text-gray-500 dark:text-gray-400">
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
            accessorKey: "executionGroupId",
            header: "Group",
            cell: ({ row }) => (
                <span className="text-xs font-mono text-gray-600 dark:text-gray-400">
                    {row.original.executionGroupId || "—"}
                </span>
            ),
        },
        {
            header: "Actions",
            cell: ({ row }) => (
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
                        // Simple logs view - can be enhanced
                        alert(`Logs for ${row.original.workflowExecutionId} (admin only)`);
                    }}
                    onPermanentDelete={() => setDeleteConfirm(row.original.workflowExecutionId)}
                    onOpenDetails={() => navigate(`/executions/${row.original.workflowExecutionId}`)}
                />
            ),
        },
    ];

    return (
        <div className="p-6 space-y-4 bg-white dark:bg-gray-900">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
                    Executions
                </h1>
                <div className="flex items-center gap-2">
                    <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                        <input
                            type="checkbox"
                            checked={includeArchived}
                            onChange={(e) => setIncludeArchived(e.target.checked)}
                        />
                        Show archived
                    </label>
                </div>
            </div>

            {isLoading && (
                <div className="text-gray-600 dark:text-gray-400">Loading executions...</div>
            )}

            {!isLoading && sortedExecutions.length === 0 && (
                <div className="text-gray-600 dark:text-gray-400">No executions found.</div>
            )}

            {!isLoading && sortedExecutions.length > 0 && (
                <DataTable columns={columns} rows={sortedExecutions} pageSize={20} />
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
                            <button
                                onClick={() => setAbortConfirm(null)}
                                className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => {
                                    if (abortConfirm.isGroup) {
                                        const exec = executions.find(
                                            (e) => e.workflowExecutionId === abortConfirm.executionId
                                        );
                                        handleAbort(abortConfirm.executionId, exec?.executionGroupId);
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
                    onOpenChange={(open) => !open && setDeleteConfirm(null)}
                    title="Permanent Delete"
                    footer={
                        <>
                            <button
                                onClick={() => setDeleteConfirm(null)}
                                className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => handlePermanentDelete(deleteConfirm)}
                                className="px-4 py-2 bg-red-600 text-white rounded"
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
                        Type <code className="bg-gray-100 dark:bg-gray-800 px-1">CONFIRM</code> to
                        proceed:
                    </p>
                    <input
                        type="text"
                        placeholder="CONFIRM"
                        className="mt-2 w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        onKeyDown={(e) => {
                            if (e.key === "Enter" && e.currentTarget.value === "CONFIRM") {
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
