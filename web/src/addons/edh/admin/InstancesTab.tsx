/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import SearchInput from "../../../features/orchestration/components/SearchInput";
import { control } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import {
    useEdhAdminInstances,
    useEdhAdminMutations,
    useEdhStaleInstances,
} from "../api/adminQueries";
import type { EdhAdminInstance } from "../api/admin";
import { unconfirmedNotice } from "../api/outcome";
import WorkstationStatusBadge, {
    EdhFailureDetail,
    describeLastError,
} from "../components/WorkstationStatusBadge";
import { useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import type { WorkstationState } from "../types";
import { edhStateLabel } from "../labels";
import ConfirmActionDialog from "./ConfirmActionDialog";

interface InstancesTabProps {
    can: (method: string, pathTemplate: string) => boolean;
}

type InstanceAction = "stop" | "deactivate";

// Matches the handler's STALE_IDLE_MINUTES_DEFAULT.
const DEFAULT_IDLE_MINUTES = 120;

// The rows shown before the first answer; one instance, so the search memo's dependency holds.
const NO_INSTANCES: EdhAdminInstance[] = [];

const formatDate = (iso: string | null) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

/** The id the admin action routes address: the EDH session id, or the bootstrap id of a launch that never received one. */
const actionTarget = (instance: EdhAdminInstance) => instance.sessionId ?? instance.bootstrapId;

const instanceLabel = (instance: EdhAdminInstance) =>
    instance.name || actionTarget(instance) || "—";

const searchableText = (instance: EdhAdminInstance) =>
    [
        instance.name,
        instance.sessionId,
        instance.bootstrapId,
        instance.userId,
        instance.owner,
        instance.edhState,
        instance.state,
        instance.instanceType,
    ]
        .filter(Boolean)
        .join(" ");

/** The admin's view of EDH sessions: every session on the cluster, or the stale ones. */
const InstancesTab: React.FC<InstancesTabProps> = ({ can }) => {
    const toast = useToast();
    const [view, setView] = useState<"all" | "stale">("all");
    const [idleMinutes, setIdleMinutes] = useState(DEFAULT_IDLE_MINUTES);
    // The typed threshold reaches the query key once the admin settles on it (blur or Enter), so a
    // value typed digit by digit costs one request rather than one per keystroke.
    const [draftMinutes, setDraftMinutes] = useState(String(DEFAULT_IDLE_MINUTES));
    const commitMinutes = () => {
        // An emptied or unreadable field falls back to the default; a number below the floor clamps to it.
        const typed = Math.floor(Number(draftMinutes));
        const parsed =
            draftMinutes.trim() === "" || Number.isNaN(typed)
                ? DEFAULT_IDLE_MINUTES
                : Math.max(1, typed);
        setDraftMinutes(String(parsed));
        if (parsed !== idleMinutes) setIdleMinutes(parsed);
    };
    // Templates are the addonApiRoutes.py path strings verbatim.
    const canStale = can("GET", "/addon/edh/admin/instances/stale");
    const canStop = can("POST", "/addon/edh/admin/instances/{sessionId}/stop");
    const canDeactivate = can("POST", "/addon/edh/admin/instances/{sessionId}/deactivate");
    const all = useEdhAdminInstances(view === "all");
    const stale = useEdhStaleInstances(idleMinutes, view === "stale" && canStale);
    const active = view === "all" ? all : stale;
    const rows = active.data ?? NO_INSTANCES;
    // The toolbar owns the search box, so the table's own is off.
    const { searchText, setSearchText, visibleRows } = useTextFilter(rows, searchableText);
    const { stopInstance, deactivateInstance } = useEdhAdminMutations();
    const { claim, release } = useInFlight();
    const [pending, setPending] = useState<{
        action: InstanceAction;
        instance: EdhAdminInstance;
    } | null>(null);

    // Stable across a poll: a fresh `cell` function would remount the cell and close an open row
    // menu. The action cell reaches only `setPending`, which never changes identity.
    const columns = useMemo<ColumnDef<EdhAdminInstance>[]>(() => {
        const rowActions = (instance: EdhAdminInstance) => {
            if (!actionTarget(instance) || (!canStop && !canDeactivate)) return null;
            const items: ContextMenuItem[] = [
                {
                    label: "Stop",
                    onSelect: () => setPending({ action: "stop", instance }),
                    // Stop addresses the EDH session: one EDH no longer lists, or one that never
                    // received a session id, can only be deactivated.
                    hidden: !canStop || instance.edhState === null || !instance.sessionId,
                },
                {
                    label: "Deactivate",
                    danger: true,
                    onSelect: () => setPending({ action: "deactivate", instance }),
                    hidden: !canDeactivate,
                },
            ];
            return (
                <ContextMenu
                    items={items}
                    trigger={
                        <button
                            aria-label={`Actions for ${instanceLabel(instance)}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        >
                            ⋮
                        </button>
                    }
                />
            );
        };
        return [
            {
                accessorKey: "name",
                header: "Name",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">
                        {row.original.name || "—"}
                    </span>
                ),
            },
            {
                accessorKey: "sessionId",
                header: "Session",
                sortingFn: "text",
                cell: ({ row }) => (
                    <span className="font-mono text-sm">{row.original.sessionId || "—"}</span>
                ),
            },
            {
                accessorKey: "edhState",
                header: "EDH state",
                cell: ({ row }) => edhStateLabel(row.original.edhState),
            },
            {
                accessorKey: "userId",
                header: "Owner",
                cell: ({ row }) => (
                    <div className="text-sm">
                        <div>{row.original.userId || "—"}</div>
                        <div className="text-text-secondary font-mono">
                            {row.original.owner || "—"}
                        </div>
                    </div>
                ),
            },
            {
                accessorKey: "state",
                header: "Status",
                cell: ({ row }) =>
                    row.original.state ? (
                        <div className="space-y-1">
                            <WorkstationStatusBadge
                                state={row.original.state as WorkstationState}
                                edhState={row.original.edhState}
                                lastError={row.original.lastError}
                                sessionId={row.original.sessionId}
                            />
                            {row.original.state === "ORPHANED" && (
                                <span className="block text-xs text-text-secondary">
                                    {describeLastError(row.original.lastError)}
                                </span>
                            )}
                            <EdhFailureDetail failure={row.original.edhFailure} />
                        </div>
                    ) : (
                        "—"
                    ),
            },
            {
                accessorKey: "instanceType",
                header: "Instance type",
                cell: ({ row }) => row.original.instanceType || "—",
            },
            {
                accessorKey: "createdAt",
                header: "Created",
                cell: ({ row }) => (
                    <span className="text-sm">{formatDate(row.original.createdAt)}</span>
                ),
            },
            ...(view === "stale"
                ? [
                      {
                          accessorKey: "staleReason",
                          header: "Why stale",
                          cell: ({ row }) =>
                              row.original.staleReason === "missingInEdh"
                                  ? "Not listed by EDH"
                                  : "Stopped in EDH",
                      } as ColumnDef<EdhAdminInstance>,
                  ]
                : []),
            // A display column: the table renders the cell as given and offers no sort on it.
            ...(canStop || canDeactivate
                ? [
                      {
                          id: "actions",
                          header: "Actions",
                          cell: ({ row }) => rowActions(row.original),
                      } as ColumnDef<EdhAdminInstance>,
                  ]
                : []),
        ];
    }, [view, canStop, canDeactivate]);

    const runAction = async () => {
        if (!pending) return;
        const { action, instance } = pending;
        const mutation = action === "stop" ? stopInstance : deactivateInstance;
        const target = actionTarget(instance);
        // One stop or deactivate per session at a time, the second click of a double-click included.
        if (!target || !claim(target)) return;
        const label = instanceLabel(instance);
        try {
            await mutation.mutateAsync(target);
            setPending(null);
            toast.success(action === "stop" ? "Session stopped" : "Session deactivated", {
                description: label,
            });
        } catch (err) {
            setPending(null);
            const unconfirmed = unconfirmedNotice(action === "stop" ? "Stop" : "Deactivate", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${label}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error(action === "stop" ? "Stop failed" : "Deactivate failed", {
                description: `${label}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release(target);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        ariaLabel="Search sessions"
                    />
                    <RefreshButton
                        onClick={() => active.refetch()}
                        busy={active.isFetching}
                        ariaLabel="Refresh sessions"
                    />
                    <span className="text-sm text-text-secondary">{rows.length} sessions</span>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <label className="flex items-center gap-2 text-sm text-text-primary whitespace-nowrap">
                        View
                        <select
                            aria-label="Session view"
                            value={view}
                            onChange={(e) => setView(e.target.value as "all" | "stale")}
                            className={control}
                        >
                            <option value="all">All sessions</option>
                            {canStale && <option value="stale">Stale sessions</option>}
                        </select>
                    </label>
                    {view === "stale" && (
                        <label className="flex items-center gap-2 text-sm text-text-primary whitespace-nowrap">
                            Stopped for at least (minutes)
                            <input
                                aria-label="Stopped minutes"
                                type="number"
                                min={1}
                                className={`${control} w-24`}
                                value={draftMinutes}
                                onChange={(e) => setDraftMinutes(e.target.value)}
                                onBlur={commitMinutes}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") commitMinutes();
                                }}
                            />
                        </label>
                    )}
                </div>
            </div>

            {active.isLoading ? (
                <div className="p-8 text-center text-text-secondary">Loading sessions...</div>
            ) : active.error ? (
                <div className="p-4 text-vams-error">
                    Error loading sessions: {toastErrorMessage(active.error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">
                    {view === "stale" ? "No stale sessions." : "No sessions are running."}
                </div>
            ) : (
                <DataTable
                    columns={columns}
                    rows={visibleRows}
                    ariaLabel={view === "stale" ? "Stale EDH sessions" : "EDH sessions"}
                    getRowId={(instance, index) => actionTarget(instance) || `row-${index}`}
                    filtering={false}
                />
            )}

            {pending && (
                <ConfirmActionDialog
                    open
                    title={`${pending.action === "stop" ? "Stop" : "Deactivate"} ${instanceLabel(
                        pending.instance
                    )}`}
                    confirmLabel={pending.action === "stop" ? "Stop" : "Deactivate"}
                    // A stop is reversible from the Workstations page; only Deactivate destroys.
                    destructive={pending.action !== "stop"}
                    busy={stopInstance.isPending || deactivateInstance.isPending}
                    onCancel={() => setPending(null)}
                    onConfirm={runAction}
                    message={
                        pending.action === "stop" ? (
                            <p>
                                Stop this session in EDH? The owner can start it again from the
                                Workstations page.
                            </p>
                        ) : (
                            <p>
                                Deactivate this session? EDH deletes the desktop and its
                                CloudFormation stack; the owner&apos;s work on its root disk is
                                lost. Its VAMS session key, if any, is revoked.
                            </p>
                        )
                    }
                />
            )}
        </div>
    );
};

export default InstancesTab;
