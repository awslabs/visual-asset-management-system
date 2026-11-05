/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useMemo, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import RowActionsButton from "../../../features/orchestration/components/RowActionsButton";
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
    StartFailureNotice,
    describeLastError,
} from "../components/WorkstationStatusBadge";
import CapacityDetail from "../components/CapacityDetail";
import {
    CARD_PAGE_SIZE,
    CardPager,
    RecordCard,
    RecordCardFields,
    RecordCardList,
    WORKSTATION_TABLE_MIN_WIDTH_PX,
} from "../components/RecordCards";
import { useMinWidth } from "../components/useMinWidth";
import { countText, useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import { useRemovedRowRef } from "../../../features/orchestration/components/useReturnFocus";
import NoMatches, { noMatchesText } from "../components/NoMatches";
import type { WorkstationState } from "../types";
import { edhStateLabel } from "../labels";
import ConfirmActionDialog, {
    controlNamed,
    rowActionsFor,
    rowActionsTrigger,
} from "./ConfirmActionDialog";
import CopyIdButton from "./CopyIdButton";

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

/** The characters of a session id the table shows; the rest sits behind the copy button. */
const SHORT_ID_LENGTH = 8;

/** An id's first characters with an ellipsis, or the whole id when it is that short. */
export const shortId = (id: string) =>
    id.length > SHORT_ID_LENGTH ? `${id.slice(0, SHORT_ID_LENGTH)}…` : id;

/** The id the admin action routes address: the EDH session id, or the bootstrap id of a launch that never received one. */
const actionTarget = (instance: EdhAdminInstance) => instance.sessionId ?? instance.bootstrapId;

const instanceLabel = (instance: EdhAdminInstance) =>
    instance.name || actionTarget(instance) || "—";

// The search box's accessible name, which a deactivation also returns focus to.
const SEARCH_LABEL = "Search sessions";

const staleReasonLabel = (instance: EdhAdminInstance) =>
    instance.staleReason === "missingInEdh" ? "Not listed by EDH" : "Stopped in EDH";

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

/**
 * The session id's start with its copy button; the title, the screen-reader text and the copy
 * button carry all of it.
 */
const SessionIdCell: React.FC<{ id: string | null }> = ({ id }) =>
    id ? (
        <span className="inline-flex items-center gap-1 whitespace-nowrap">
            <span className="font-mono text-sm" title={id}>
                <span aria-hidden="true">{shortId(id)}</span>
                <span className="sr-only">{id}</span>
            </span>
            <CopyIdButton value={id} what="Session id" />
        </span>
    ) : (
        <>—</>
    );

/** The owner's VAMS user id, with the EDH user id behind the copy button and in the tooltip. */
const OwnerCell: React.FC<{ instance: EdhAdminInstance }> = ({ instance }) => {
    const edhUid = instance.owner;
    return (
        <span className="inline-flex items-center gap-1 text-sm">
            <span title={edhUid ? `EDH user id: ${edhUid}` : undefined}>
                {instance.userId || "—"}
            </span>
            {edhUid && <CopyIdButton value={edhUid} what="EDH user id" />}
        </span>
    );
};

/** The status badge with the cluster's own state beneath it, and why a launch or a start failed. */
const SessionStatusCell: React.FC<{ instance: EdhAdminInstance }> = ({ instance }) => (
    <div className="space-y-1">
        {instance.state ? (
            <WorkstationStatusBadge
                state={instance.state as WorkstationState}
                edhState={instance.edhState}
                lastError={instance.lastError}
                activity={instance.activity}
                sessionId={instance.sessionId}
                instanceType={instance.instanceType}
                startFailure={instance.startFailure}
            />
        ) : (
            <span className="block">—</span>
        )}
        <span className="block text-xs text-text-secondary">
            EDH state: {edhStateLabel(instance.edhState)}
        </span>
        {instance.state === "ORPHANED" && (
            <span className="block text-xs text-text-secondary">
                {describeLastError(instance.lastError)}
            </span>
        )}
        <StartFailureNotice row={instance} />
        <EdhFailureDetail failure={instance.edhFailure} />
    </div>
);

/** The instance type, with the capacity reservation it launched into beneath it. */
const InstanceTypeCell: React.FC<{ instance: EdhAdminInstance }> = ({ instance }) => (
    <div className="text-sm">
        <div>{instance.instanceType || "—"}</div>
        <CapacityDetail row={instance} />
    </div>
);

interface SessionCardsProps {
    rows: EdhAdminInstance[];
    ariaLabel: string;
    stale: boolean;
    renderActions: (instance: EdhAdminInstance) => React.ReactNode;
}

/**
 * The sessions below `WORKSTATION_TABLE_MIN_WIDTH_PX`: one card per session with its name and
 * actions, status, owner, instance type, created time, session id and, in the stale view, why it is
 * stale, so nothing scrolls sideways. The cards page as the table does; a poll that shortens the
 * list keeps the page in range.
 */
const SessionCards: React.FC<SessionCardsProps> = ({ rows, ariaLabel, stale, renderActions }) => {
    const [pageIndex, setPageIndex] = useState(0);
    const pageCount = Math.ceil(rows.length / CARD_PAGE_SIZE);
    const page = Math.min(pageIndex, Math.max(pageCount - 1, 0));
    const first = page * CARD_PAGE_SIZE;
    return (
        <div>
            <RecordCardList ariaLabel={ariaLabel}>
                {rows.slice(first, first + CARD_PAGE_SIZE).map((instance, index) => (
                    <RecordCard
                        key={actionTarget(instance) || `row-${first + index}`}
                        label={instanceLabel(instance)}
                        title={instanceLabel(instance)}
                        actions={renderActions(instance)}
                    >
                        <SessionStatusCell instance={instance} />
                        <RecordCardFields>
                            <dt className="text-text-secondary">Owner</dt>
                            <dd className="m-0 min-w-0">
                                <OwnerCell instance={instance} />
                            </dd>
                            <dt className="text-text-secondary">Instance type</dt>
                            <dd className="m-0 min-w-0">
                                <InstanceTypeCell instance={instance} />
                            </dd>
                            <dt className="text-text-secondary">Created</dt>
                            <dd className="m-0">{formatDate(instance.createdAt)}</dd>
                            <dt className="text-text-secondary">Session</dt>
                            <dd className="m-0 min-w-0">
                                <SessionIdCell id={instance.sessionId} />
                            </dd>
                            {stale && (
                                <>
                                    <dt className="text-text-secondary">Why stale</dt>
                                    <dd className="m-0">{staleReasonLabel(instance)}</dd>
                                </>
                            )}
                        </RecordCardFields>
                    </RecordCard>
                ))}
            </RecordCardList>
            <CardPager pageIndex={page} pageCount={pageCount} onPageChange={setPageIndex} />
        </div>
    );
};

/** The admin's view of EDH sessions: every session on the cluster, or the stale ones. */
const InstancesTab: React.FC<InstancesTabProps> = ({ can }) => {
    const toast = useToast();
    const wide = useMinWidth(WORKSTATION_TABLE_MIN_WIDTH_PX);
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
    // The tab's controls, where a confirmation finds the element it returns focus to.
    const panelRef = useRef<HTMLDivElement>(null);
    // The session the deactivation removed, or may have removed when the service did not confirm
    // it: EDH deletes the desktop and the row leaves the list, so its confirmation returns focus to
    // the search box rather than to the row's trigger. A stopped session keeps its row.
    const deactivatedRef = useRemovedRowRef<string>(pending);

    // One menu per session, the same in the table and the cards. It reaches only `setPending`, which
    // never changes identity.
    const renderActions = useCallback(
        (instance: EdhAdminInstance) => {
            const target = actionTarget(instance);
            if (!target || (!canStop && !canDeactivate)) return null;
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
                        <RowActionsButton
                            {...rowActionsFor(target)}
                            aria-label={`Actions for ${instanceLabel(instance)}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        />
                    }
                />
            );
        },
        [canStop, canDeactivate]
    );

    // Stable across a poll: a fresh `cell` function would remount the cell and close an open row
    // menu.
    const columns = useMemo<ColumnDef<EdhAdminInstance>[]>(
        () => [
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
                cell: ({ row }) => <SessionIdCell id={row.original.sessionId} />,
            },
            {
                accessorKey: "userId",
                header: "Owner",
                cell: ({ row }) => <OwnerCell instance={row.original} />,
            },
            {
                accessorKey: "state",
                header: "Status",
                cell: ({ row }) => <SessionStatusCell instance={row.original} />,
            },
            {
                accessorKey: "instanceType",
                header: "Instance type",
                cell: ({ row }) => <InstanceTypeCell instance={row.original} />,
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
                          cell: ({ row }) => staleReasonLabel(row.original),
                      } as ColumnDef<EdhAdminInstance>,
                  ]
                : []),
            // A display column: the table renders the cell as given and offers no sort on it.
            ...(canStop || canDeactivate
                ? [
                      {
                          id: "actions",
                          header: "Actions",
                          cell: ({ row }) => renderActions(row.original),
                      } as ColumnDef<EdhAdminInstance>,
                  ]
                : []),
        ],
        [view, canStop, canDeactivate, renderActions]
    );

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
            if (action === "deactivate") deactivatedRef.current = target;
            setPending(null);
            toast.success(action === "stop" ? "Session stopped" : "Session deactivated", {
                description: label,
            });
        } catch (err) {
            setPending(null);
            const unconfirmed = unconfirmedNotice(action === "stop" ? "Stop" : "Deactivate", err);
            if (unconfirmed) {
                if (action === "deactivate") deactivatedRef.current = target;
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
        <div ref={panelRef} className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        ariaLabel={SEARCH_LABEL}
                        placeholder="Search sessions"
                    />
                    <RefreshButton
                        onClick={() => active.refetch()}
                        busy={active.isFetching}
                        ariaLabel="Refresh sessions"
                    />
                    <span className="text-sm text-text-secondary">
                        {countText(visibleRows.length, rows.length, "sessions")}
                    </span>
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
                <>
                    {wide ? (
                        <DataTable
                            columns={columns}
                            rows={visibleRows}
                            ariaLabel={view === "stale" ? "Stale EDH sessions" : "EDH sessions"}
                            getRowId={(instance, index) => actionTarget(instance) || `row-${index}`}
                            filtering={false}
                        />
                    ) : (
                        visibleRows.length > 0 && (
                            <SessionCards
                                // A new search or view starts on the first page, as the table does.
                                key={`${view}:${searchText}`}
                                rows={visibleRows}
                                ariaLabel={view === "stale" ? "Stale EDH sessions" : "EDH sessions"}
                                stale={view === "stale"}
                                renderActions={renderActions}
                            />
                        )
                    )}
                    {visibleRows.length === 0 && (
                        <NoMatches>{noMatchesText("sessions", searchText)}</NoMatches>
                    )}
                </>
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
                    busyLabel={pending.action === "stop" ? "Stopping..." : "Deactivating..."}
                    onCancel={() => setPending(null)}
                    onConfirm={runAction}
                    returnFocusTo={() => {
                        const target = actionTarget(pending.instance) ?? "";
                        return deactivatedRef.current === target
                            ? controlNamed(panelRef.current, SEARCH_LABEL)
                            : rowActionsTrigger(panelRef.current, target);
                    }}
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
