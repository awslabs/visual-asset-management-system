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
import Breadcrumb from "../components/Breadcrumb";
import {
    useExecutions,
    useExecutionActions,
    useAllWorkflows,
    useDatabases,
    useWorkflow,
    type ExecutionScope,
} from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import type { Execution } from "../types";

interface ExecutionsBoardProps {
    scope: ExecutionScope;
}

// Lower bound for a custom range whose "from" side is left empty, meaning "everything up to the
// chosen end date".
const OPEN_ENDED_RANGE_START = "1970-01-01T00:00:00Z";

const ExecutionsBoard: React.FC<ExecutionsBoardProps> = ({ scope }) => {
    const toast = useToast();
    const navigate = useNavigate();
    const [quickViewExecutionId, setQuickViewExecutionId] = useState<string | null>(null);
    // The group id is captured with the row so a list refetch (poll / mutation invalidation) while
    // the dialog is open cannot downgrade a group abort to a single-execution abort.
    const [abortConfirm, setAbortConfirm] = useState<{
        executionId: string;
        isGroup: boolean;
        executionGroupId?: string;
    } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
    const [deleteTypedValue, setDeleteTypedValue] = useState("");
    // Failure message from the last abort / rerun / permanent-delete attempt. Shown inside the
    // confirmation dialog that triggered it (the dialog stays open so the action can be retried or
    // cancelled), and in a page-level banner for rerun, which has no dialog.
    const [actionError, setActionError] = useState<string | null>(null);
    const [searchText, setSearchText] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [triggerFilter, setTriggerFilter] = useState("");
    // Workflow-database / workflow filters. Only meaningful in the global scope: a WORKFLOW-scoped
    // board already has these pinned by the scope (see useExecutions), so the controls are hidden
    // there rather than offering a choice that would be overridden.
    const [workflowDatabaseFilter, setWorkflowDatabaseFilter] = useState("");
    const [workflowFilter, setWorkflowFilter] = useState("");
    // The asset tab's workflow filter, held as the composite "databaseId:workflowId" because a
    // workflowId is unique only within its database. Kept separate from the two global controls so
    // one dropdown picks both halves at once — the asset tab has no workflow-database dropdown to
    // pair with, and sending half a composite would filter against ":wf1" and match nothing.
    const [assetWorkflowFilter, setAssetWorkflowFilter] = useState("");
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
        if (workflowDatabaseFilter) f.workflowDatabaseId = workflowDatabaseFilter;
        if (workflowFilter) f.workflowId = workflowFilter;
        // The asset tab sends both halves of the composite together. The backend matches these two
        // per field rather than as a joined key, so sending only one would not narrow to a single
        // workflow when two databases happen to share a workflow id.
        if (assetWorkflowFilter) {
            const separator = assetWorkflowFilter.indexOf(":");
            f.workflowDatabaseId = assetWorkflowFilter.slice(0, separator);
            f.workflowId = assetWorkflowFilter.slice(separator + 1);
        }
        if (dateWindow === "custom") {
            // The lower bound is always sent. The server applies its own 90-day floor when none
            // arrives, so an end-only range ("everything before X") would otherwise come back
            // silently clipped to the last 90 days — and inverted against an older end date, which
            // the key-range BETWEEN rejects.
            f.filterStartDate = startDateFilter
                ? `${startDateFilter}T00:00:00Z`
                : OPEN_ENDED_RANGE_START;
            if (endDateFilter) f.filterEndDate = `${endDateFilter}T23:59:59Z`;
        } else if (dateWindow !== "90") {
            // 120 / 180 day presets: N days before now (the 90-day default needs no override).
            const cutoff = new Date(Date.now() - Number(dateWindow) * 24 * 60 * 60 * 1000);
            f.filterStartDate = cutoff.toISOString().replace(/\.\d{3}Z$/, "Z");
        }
        return f;
    }, [
        statusFilter,
        triggerFilter,
        workflowDatabaseFilter,
        workflowFilter,
        assetWorkflowFilter,
        dateWindow,
        startDateFilter,
        endDateFilter,
    ]);

    // Only the global board offers workflow/database filters: useExecutions pins both from the scope
    // for asset- and workflow-scoped boards, so a control there would be silently overridden.
    const isGlobalScope = scope.kind === "global";
    // The asset tab gets a workflow filter too, built from the workflows that actually ran on this
    // asset (see assetWorkflowOptions). A workflow-scoped board is excluded: it is already pinned to
    // one workflow, so a picker there could only re-select what is already in force.
    const isAssetScope = scope.kind === "asset";
    // Only fetched for the workflow-scoped board, to label its breadcrumb.
    const { data: scopedWorkflow } = useWorkflow(
        scope.kind === "workflow" ? scope.databaseId : "",
        scope.kind === "workflow" ? scope.workflowId : ""
    );
    const scopedWorkflowName =
        scope.kind === "workflow" ? (scopedWorkflow as any)?.workflowName || scope.workflowId : "";
    const { data: allDatabases } = useDatabases(isGlobalScope);
    // Global scope narrows the workflow list to the chosen database. The asset tab passes no database
    // (its executions can come from workflows in any of them) and uses this only to put NAMES on the
    // ids its rows carry — an execution row has workflowId but no workflowName.
    const { data: allWorkflows } = useAllWorkflows(
        isGlobalScope ? workflowDatabaseFilter || undefined : undefined,
        undefined,
        isGlobalScope || isAssetScope
    );

    // Database ids for the filter: the databases the caller can see, unioned with any id already
    // present on a loaded row (mirrors WorkflowsPage) so a row's database is always selectable.
    const databaseOptions = React.useMemo(() => {
        if (!isGlobalScope) return [] as string[];
        const ids = new Set<string>();
        (allDatabases || []).forEach((d: any) => d?.databaseId && ids.add(d.databaseId));
        return Array.from(ids).sort();
    }, [allDatabases, isGlobalScope]);

    // Workflows for the filter, narrowed to the selected database when one is chosen.
    const workflowOptions = React.useMemo(() => {
        if (!isGlobalScope) return [] as any[];
        return [...(allWorkflows || [])].sort((a: any, b: any) =>
            (a.workflowName || a.workflowId).localeCompare(b.workflowName || b.workflowId)
        );
    }, [allWorkflows, isGlobalScope]);

    // Composite key -> display name for every workflow the caller can see, so the asset tab can
    // label a row's ids. Rows carry ids only.
    const workflowNamesByKey = React.useMemo(() => {
        const map = new Map<string, string>();
        (allWorkflows || []).forEach((w: any) => {
            if (w?.workflowId) {
                map.set(`${w.databaseId}:${w.workflowId}`, w.workflowName || w.workflowId);
            }
        });
        return map;
    }, [allWorkflows]);

    // The asset tab's workflow choices, ACCUMULATED across loads rather than recomputed from the
    // rows currently on screen.
    //
    // This has to accumulate. The filter is applied server-side, so the moment a workflow is chosen
    // the response contains only that workflow's executions — recomputing options from those rows
    // would collapse the dropdown to the single selected entry, stranding the user with no way back
    // to another workflow without first clearing the filter. Growing a set instead keeps every
    // workflow seen in this asset's history selectable. Paging in more rows with "Load more" adds to
    // it for the same reason.
    const [seenAssetWorkflows, setSeenAssetWorkflows] = useState<Map<string, string>>(new Map());

    const {
        data,
        isLoading,
        error: loadError,
        fetchNextPage,
        hasNextPage,
        isFetchingNextPage,
        refetch,
        isFetching,
    } = useExecutions(scope, filters, {});
    const { abortExecution, rerunExecution, permanentDeleteExecution } = useExecutionActions();
    const { can } = useAllowedRoutes();
    // `can` is a fresh closure on each render, and it feeds the column definitions. Reading it
    // through a ref keeps the definitions stable across a poll, which is what keeps an open row
    // action menu from being unmounted mid-click.
    const canRef = React.useRef(can);
    canRef.current = can;
    const canStable = React.useCallback(
        (method: string, pathTemplate: string) => canRef.current(method, pathTemplate),
        []
    );

    const executions = React.useMemo(
        () => data?.pages?.flatMap((page: any) => page.Items) ?? [],
        [data]
    );

    // Page-level notices from the list endpoint. A page that hit the per-page asset-resolution bound
    // withholds rows it could not evaluate and says so here; without it a short page is
    // indistinguishable from a window in which almost nothing ran.
    const listWarnings = React.useMemo(() => {
        const seen: string[] = [];
        (data?.pages || []).forEach((page: any) => {
            (Array.isArray(page?.warnings) ? page.warnings : []).forEach((w: string) => {
                if (w && !seen.includes(w)) seen.push(w);
            });
        });
        return seen;
    }, [data]);

    // Fold whatever workflows the loaded rows reveal into the accumulated option set (asset tab
    // only). Rows carry ids, so the label comes from the workflow list when it is available and falls
    // back to the id — a workflow that has since been archived or that the caller cannot read still
    // gets a selectable entry rather than vanishing from its own history.
    React.useEffect(() => {
        if (!isAssetScope) return;
        setSeenAssetWorkflows((previous) => {
            let added = false;
            const next = new Map(previous);
            executions.forEach((e: any) => {
                if (!e?.workflowId) return;
                const key = `${e.workflowDatabaseId || ""}:${e.workflowId}`;
                const label = workflowNamesByKey.get(key) || e.workflowId;
                if (next.get(key) !== label) {
                    next.set(key, label);
                    added = true;
                }
            });
            // Returning the same reference when nothing changed keeps this from re-rendering on
            // every 5s poll of an unchanged list.
            return added ? next : previous;
        });
    }, [executions, isAssetScope, workflowNamesByKey]);

    // Sorted by label, with the id-only fallbacks ordering alongside names.
    const assetWorkflowOptions = React.useMemo(
        () => Array.from(seenAssetWorkflows.entries()).sort((a, b) => a[1].localeCompare(b[1])),
        [seenAssetWorkflows]
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

    // Client-side text search over the loaded rows (id / workflow / database / group). Server-side
    // filters (status, trigger, date window) are applied via the query; this narrows what's already
    // loaded.
    const visibleExecutions = useMemo(() => {
        const q = searchText.trim().toLowerCase();
        if (!q) return sortedExecutions;
        return sortedExecutions.filter((e) =>
            [e.workflowExecutionId, e.workflowId, e.workflowDatabaseId, e.executionGroupId].some(
                (v) => (v || "").toLowerCase().includes(q)
            )
        );
    }, [sortedExecutions, searchText]);

    // Abort and permanent-delete are raised from a confirm dialog, so their failure also stays inline
    // in that dialog (the message sits next to the action that caused it). The toast is what carries
    // the outcome once the dialog closes, and is the only feedback for actions with no dialog.
    // Surface a load failure once per occurrence; the inline message above stays for reference.
    React.useEffect(() => {
        if (loadError) {
            toast.error("Load failed", {
                description: `Executions: ${toastErrorMessage(loadError)}`,
            });
        }
    }, [loadError, toast]);

    const handleAbort = async (executionId: string, groupId?: string) => {
        setActionError(null);
        try {
            await abortExecution.mutateAsync({ executionId, groupId });
            setAbortConfirm(null);
            toast.success(groupId ? "Aborting execution group" : "Aborting execution", {
                description: groupId
                    ? `Every active execution in group ${groupId} was signalled to stop.`
                    : `Execution ${executionId.slice(0, 12)}… was signalled to stop.`,
            });
        } catch (err) {
            const message = toastErrorMessage(err, "Failed to abort execution");
            setActionError(message);
            toast.error("Abort failed", { description: message });
        }
    };

    const handleRerun = React.useCallback(
        async (executionId: string, groupId?: string) => {
            setActionError(null);
            try {
                const result: any = await rerunExecution.mutateAsync({
                    executionId,
                    executionGroupId: groupId,
                });
                // The re-run response passes the execute handler's body through, so it carries the NEW
                // execution's id and any non-fatal warnings. Naming the new id matters because the row
                // the user acted on is the OLD execution — without it there is no way to tell which run
                // to watch. Warnings are surfaced rather than dropped: a run that launched with caveats
                // (skipped inputs, say) is not the same as a clean one.
                const newId: string | undefined =
                    result?.executionId || result?.workflowExecutionId;
                const warnings: string[] = Array.isArray(result?.warnings) ? result.warnings : [];
                const parts = [
                    newId ? `New execution ${newId}.` : "A new execution was launched.",
                    // One row, one re-run: the group id is carried onto the new execution as a label
                    // so it files alongside its siblings, and says nothing about them being replayed.
                    groupId
                        ? `Filed under group ${groupId}; the other executions in that group were not re-run.`
                        : "",
                    warnings.length ? `Warnings: ${warnings.join("; ")}` : "",
                ].filter(Boolean);
                if (warnings.length) {
                    // Not a plain success: it started, but with something the operator should read.
                    toast.warning("Re-run started with warnings", { description: parts.join(" ") });
                } else {
                    toast.success("Re-run started", { description: parts.join(" ") });
                }
            } catch (err) {
                const message = toastErrorMessage(err, "Failed to rerun execution");
                setActionError(message);
                toast.error("Re-run failed", { description: message });
            }
        },
        // The mutation object is a new reference on every render while mutateAsync is stable, so the
        // dependency is the function rather than its wrapper.
        [rerunExecution.mutateAsync, toast]
    );

    const handlePermanentDelete = async (executionId: string) => {
        setActionError(null);
        try {
            await permanentDeleteExecution.mutateAsync(executionId);
            setDeleteConfirm(null);
            setDeleteTypedValue("");
            toast.success("Execution deleted", {
                description: `${executionId.slice(0, 12)}… was permanently removed.`,
            });
        } catch (err) {
            const message = toastErrorMessage(err, "Failed to delete execution");
            setActionError(message);
            toast.error("Delete failed", { description: message });
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
                // "Workflow Database" rather than "Database": the row also carries an OUTPUT database,
                // so an unqualified label was ambiguous once that column existed.
                header: "Workflow Database",
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
            // Output-target columns, global scope only. The per-asset list is built by joining the
            // execution-inputs rows with the main rows and never reads the configuration row the
            // output target lives on, so on the asset tab these would be permanently blank. Adding
            // them there would cost one extra read per row, which is the N+1 the global list is
            // deliberately shaped to avoid.
            ...(isGlobalScope
                ? [
                      {
                          accessorKey: "outputLocationType",
                          header: "Output Type",
                          cell: ({ row }: { row: { original: Execution } }) => (
                              <span className="text-sm">
                                  {row.original.outputLocationType || "—"}
                              </span>
                          ),
                      },
                      {
                          accessorKey: "outputDatabaseId",
                          header: "Output Database",
                          cell: ({ row }: { row: { original: Execution } }) => (
                              <span className="text-sm">
                                  {row.original.outputDatabaseId || "—"}
                              </span>
                          ),
                      },
                      {
                          accessorKey: "outputAssetId",
                          header: "Output Asset ID",
                          cell: ({ row }: { row: { original: Execution } }) => (
                              <span className="font-mono text-xs">
                                  {row.original.outputAssetId || "—"}
                              </span>
                          ),
                      },
                  ]
                : []),
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
                            can={canStable}
                            onView={() => setQuickViewExecutionId(row.original.workflowExecutionId)}
                            onAbort={() => {
                                setActionError(null);
                                setAbortConfirm({
                                    executionId: row.original.workflowExecutionId,
                                    isGroup: false,
                                });
                            }}
                            onAbortGroup={
                                row.original.executionGroupId
                                    ? () => {
                                          setActionError(null);
                                          setAbortConfirm({
                                              executionId: row.original.workflowExecutionId,
                                              isGroup: true,
                                              executionGroupId: row.original.executionGroupId,
                                          });
                                      }
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
                            onPermanentDelete={() => {
                                setActionError(null);
                                setDeleteConfirm(row.original.workflowExecutionId);
                            }}
                            onOpenDetails={() =>
                                navigate(`/executions/${row.original.workflowExecutionId}`)
                            }
                        />
                    </div>
                ),
            },
        ],
        [
            canStable,
            navigate,
            handleRerun,
            setQuickViewExecutionId,
            setAbortConfirm,
            setDeleteConfirm,
            isGlobalScope,
        ]
    );

    return (
        <div className="orchestration-root orchestration-page space-y-4 bg-surface">
            {/* Workflow-scoped board (navigated from a workflow): trail back to the Workflows list and
                that workflow, so the filtered view says what it is filtered to. */}
            {scope.kind === "workflow" && (
                <Breadcrumb
                    items={[
                        { label: "Workflows", to: "/workflows" },
                        {
                            label: scopedWorkflowName,
                            to: `/databases/${scope.databaseId}/workflows/${scope.workflowId}`,
                        },
                        { label: "Executions" },
                    ]}
                />
            )}
            <div className="flex items-center justify-between">
                <h1 className="text-text-primary">Executions</h1>
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
                        {/* Values are the stored trigger vocabulary ("Manual"/"File-Upload"), which
                            is what the server-side triggerType filter compares against. */}
                        <option value="">All triggers</option>
                        <option value="Manual">Manual</option>
                        <option value="File-Upload">File upload</option>
                    </select>
                    {isGlobalScope && (
                        <select
                            aria-label="Filter by workflow database"
                            value={workflowDatabaseFilter}
                            onChange={(e) => {
                                setWorkflowDatabaseFilter(e.target.value);
                                // The workflow list is scoped to the chosen database, so a stale
                                // selection from another database would filter to nothing.
                                setWorkflowFilter("");
                            }}
                            className={control}
                        >
                            <option value="">All workflow databases</option>
                            {databaseOptions.map((id) => (
                                <option key={id} value={id}>
                                    {id}
                                </option>
                            ))}
                        </select>
                    )}
                    {isGlobalScope && (
                        <select
                            aria-label="Filter by workflow"
                            value={workflowFilter}
                            onChange={(e) => setWorkflowFilter(e.target.value)}
                            className={control}
                        >
                            <option value="">All workflows</option>
                            {workflowOptions.map((w) => (
                                <option
                                    key={`${w.databaseId}:${w.workflowId}`}
                                    value={w.workflowId}
                                >
                                    {w.workflowName || w.workflowId}
                                </option>
                            ))}
                        </select>
                    )}
                    {/* Asset tab: one dropdown selecting the whole composite key, listing only the
                        workflows this asset has actually run. Rendered when there is more than one
                        to choose between — with a single workflow in an asset's history the control
                        can only reproduce the list already shown. It stays rendered once a filter is
                        active, so narrowing to one workflow cannot remove the control that undoes
                        it. */}
                    {isAssetScope && (assetWorkflowOptions.length > 1 || !!assetWorkflowFilter) && (
                        <select
                            aria-label="Filter by workflow"
                            value={assetWorkflowFilter}
                            onChange={(e) => setAssetWorkflowFilter(e.target.value)}
                            className={control}
                        >
                            <option value="">All workflows</option>
                            {assetWorkflowOptions.map(([key, label]) => (
                                <option key={key} value={key}>
                                    {label}
                                </option>
                            ))}
                        </select>
                    )}
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
                        workflowDatabaseFilter ||
                        workflowFilter ||
                        assetWorkflowFilter ||
                        dateWindow !== "90" ||
                        startDateFilter ||
                        endDateFilter) && (
                        <button
                            onClick={() => {
                                setStatusFilter("");
                                setTriggerFilter("");
                                setWorkflowDatabaseFilter("");
                                setWorkflowFilter("");
                                setAssetWorkflowFilter("");
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

            {/* Rerun has no confirmation dialog, so its failures surface here. Abort / permanent
                delete render the same message inside their own dialog. */}
            {actionError && !abortConfirm && !deleteConfirm && (
                <div
                    role="alert"
                    className="p-3 rounded bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400"
                >
                    {actionError}
                </div>
            )}

            {/* Notices from the list response itself, above the rows they qualify — the count on
                screen is not the whole answer when one of these is present. */}
            {listWarnings.length > 0 && (
                <div
                    role="status"
                    className="p-3 rounded bg-yellow-100 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300"
                >
                    {listWarnings.map((warning) => (
                        <div key={warning}>{warning}</div>
                    ))}
                </div>
            )}

            {isLoading && <div className="text-text-secondary">Loading executions...</div>}

            {/* A failed fetch is reported explicitly: falling through to the empty state below made a
                load failure indistinguishable from a genuinely empty board. */}
            {!isLoading && loadError && (
                <div role="alert" className="p-4 text-vams-error">
                    Error loading executions: {toastErrorMessage(loadError)}
                </div>
            )}

            {!isLoading && !loadError && visibleExecutions.length === 0 && (
                <div className="text-text-secondary">No executions found.</div>
            )}

            {!isLoading && visibleExecutions.length > 0 && (
                <DataTable
                    columns={columns}
                    rows={visibleExecutions}
                    paginate={false}
                    // The board owns the search box (in the filter row); the table's own search
                    // is disabled so it doesn't render a second, redundant search bar.
                    filtering={false}
                    onRowClick={(row) => setQuickViewExecutionId(row.workflowExecutionId)}
                />
            )}

            {/* Pagination sits outside the rows branch: a server page can drop every row it returned
                (server-side filters, per-object visibility) or the client search can match nothing on
                the loaded pages while more pages remain, so the control must stay reachable. */}
            {!isLoading && hasNextPage && (
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
                    onOpenChange={(open) => {
                        if (!open) {
                            setAbortConfirm(null);
                            setActionError(null);
                        }
                    }}
                    title={abortConfirm.isGroup ? "Abort Execution Group" : "Abort Execution"}
                    footer={
                        <>
                            <button
                                onClick={() => {
                                    setAbortConfirm(null);
                                    setActionError(null);
                                }}
                                className={btnSecondary}
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() =>
                                    handleAbort(
                                        abortConfirm.executionId,
                                        abortConfirm.isGroup
                                            ? abortConfirm.executionGroupId
                                            : undefined
                                    )
                                }
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
                    {actionError && (
                        <div
                            role="alert"
                            className="mt-4 p-3 rounded bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400"
                        >
                            {actionError}
                        </div>
                    )}
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
                            setActionError(null);
                        }
                    }}
                    title="Permanent Delete"
                    footer={
                        <>
                            <button
                                onClick={() => {
                                    setDeleteConfirm(null);
                                    setDeleteTypedValue("");
                                    setActionError(null);
                                }}
                                className={btnSecondary}
                            >
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
                    <p className="font-semibold mt-2">
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
                    {actionError && (
                        <div
                            role="alert"
                            className="mt-4 p-3 rounded bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400"
                        >
                            {actionError}
                        </div>
                    )}
                </Dialog>
            )}
        </div>
    );
};

export default ExecutionsBoard;
