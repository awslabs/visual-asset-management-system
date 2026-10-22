/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { useMatch, useNavigate } from "react-router-dom";
import { type ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import SearchInput from "../../../features/orchestration/components/SearchInput";
import { btnPrimary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useAllowedRoutes } from "../../../features/orchestration/permissions/useAllowedRoutes";
import { addonFeaturesEnabled } from "../../addonFeatures";
import { useFeaturesEnabled } from "../../../context/RuntimeConfigContext";
import {
    WORKSTATION_POLL_STABLE_MS,
    WORKSTATION_POLL_TRANSITIONAL_MS,
    useEdhCatalogImages,
    useEdhCatalogLease,
    useWorkstations,
    useWorkstationMutations,
} from "../api/queries";
import { unconfirmedNotice } from "../api/outcome";
import WorkstationStatusBadge, {
    EdhFailureDetail,
    describeLastError,
} from "../components/WorkstationStatusBadge";
import DeleteWorkstationDialog from "../components/DeleteWorkstationDialog";
import NotEnabledPanel from "../components/NotEnabledPanel";
import { useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import { hoursWord, leaseRemaining, type LeaseRemainingTone, osFamilyLabel } from "../labels";
import ConnectLink, { connectUnavailableReason } from "./ConnectLink";
import WindowsSignInDialog from "./WindowsSignInDialog";
import LaunchWorkstationWizard, {
    SESSION_BOOTSTRAP_NOTICE_TITLE,
    sessionBootstrapNotice,
} from "./LaunchWorkstationWizard";
import { EDH_SIMULATIONS_PATH, EDH_WORKSTATIONS_PATH } from "./simulationsTab";
import EdhFeatureTabs, {
    EDH_FEATURE_PANEL_ID,
    edhFeatureTabId,
    type EdhFeatureTabKey,
} from "../components/EdhFeatureTabs";
import SimulationsTab from "../containers/SimulationsTab";
import type { WorkstationRecord } from "../types";

// Route templates as `backend/backend/common/addonApiRoutes.py` declares them; `can()` matches them
// segment by segment against the caller's allowed routes.
export const WORKSTATIONS_ROUTE = "/addon/edh/workstations";
export const WORKSTATION_ROUTE = "/addon/edh/workstations/{sessionId}";
export const WORKSTATION_STOP_ROUTE = "/addon/edh/workstations/{sessionId}/stop";
export const WORKSTATION_START_ROUTE = "/addon/edh/workstations/{sessionId}/start";
export const WORKSTATION_CONNECT_ROUTE = "/addon/edh/workstations/{sessionId}/connect";
export const WORKSTATION_RENEW_ROUTE = "/addon/edh/workstations/{sessionId}/renew";
export const WORKSTATION_WINDOWS_SIGN_IN_ROUTE =
    "/addon/edh/workstations/{sessionId}/windows-sign-in";
export const CATALOG_IMAGES_ROUTE = "/addon/edh/catalog/images";
export const CATALOG_LEASE_ROUTE = "/addon/edh/catalog/lease";

// How long a launch may sit in CREATING without a session before this page offers Delete for it —
// until then the launch worker may still be on the row, so the menu keeps Delete disabled.
const CREATING_DELETE_GRACE_MS = 15 * 60 * 1000;
const CREATING_DELETE_HINT = `Available once the launch has been creating for ${
    CREATING_DELETE_GRACE_MS / 60000
} minutes.`;

// The poll cadence as the page states it; the numbers are the query module's own.
const REFRESH_CAPTION = `Refreshes every ${WORKSTATION_POLL_STABLE_MS / 1000} seconds, and every ${
    WORKSTATION_POLL_TRANSITIONAL_MS / 1000
} seconds while a workstation is being created or started.`;

// The rows shown before the first answer; one instance, so the search memo's dependency holds.
const NO_WORKSTATIONS: WorkstationRecord[] = [];

/** The row text the toolbar search matches: the words the table shows, and the ids a row is known by. */
const searchableText = (row: WorkstationRecord, imageName: string | undefined) =>
    [
        row.name,
        imageName ?? row.imageId,
        osFamilyLabel(row.osFamily),
        row.instanceType,
        row.state,
        row.edhState,
        row.sessionId,
        row.bootstrapId,
    ]
        .filter(Boolean)
        .join(" ");

const formatDate = (iso: string) => {
    const date = new Date(iso);
    return isNaN(date.getTime()) ? "—" : date.toLocaleString();
};

/** Whether Delete is offered: any settled row, or a session-less launch past the grace window. */
const isDeletable = (row: WorkstationRecord): boolean => {
    if (row.state !== "CREATING") return true;
    if (row.sessionId) return false;
    const updatedAt = new Date(row.updatedAt).getTime();
    return !isNaN(updatedAt) && Date.now() - updatedAt > CREATING_DELETE_GRACE_MS;
};

// The badge's own caution and failure tones; the ordinary countdown reads as body text.
const LEASE_TONE_CLASS: Record<LeaseRemainingTone, string> = {
    normal: "text-text-primary",
    warning: "text-orange-800 dark:text-orange-300",
    expired: "text-red-800 dark:text-red-300",
};

/**
 * What a lease has left, recomputed on each render; the list's own poll is what moves it on. A row
 * no lease covers carries the placeholder the actions cell uses.
 */
const LeaseRemainingCell: React.FC<{ row: WorkstationRecord; graceHours: number }> = ({
    row,
    graceHours,
}) => {
    const remaining = leaseRemaining(row, { graceHours }, Date.now());
    if (!remaining) {
        return <span className="text-text-disabled">—</span>;
    }
    return (
        <span
            className={`text-sm ${LEASE_TONE_CLASS[remaining.tone]}`}
            title={row.leaseExpiresAt ? formatDate(row.leaseExpiresAt) : undefined}
        >
            {remaining.text}
        </span>
    );
};

interface RowActionsProps {
    row: WorkstationRecord;
    canStop: boolean;
    canStart: boolean;
    canDelete: boolean;
    canRenew: boolean;
    /** Whether leases apply at all, and the window a renewal grants. */
    leaseEnabled: boolean;
    leaseHours: number;
    onStop: (row: WorkstationRecord) => void;
    onStart: (row: WorkstationRecord) => void;
    onDelete: (row: WorkstationRecord) => void;
    onRenew: (row: WorkstationRecord) => void;
    canShowWindowsPassword: boolean;
    onShowWindowsPassword: (row: WorkstationRecord) => void;
}

type SessionAction = "start" | "stop" | "renew" | "delete";

// The id a workstation's requests address: its session id, or the bootstrap id of a launch that has
// none.
const workstationKey = (row: WorkstationRecord) => row.sessionId ?? row.bootstrapId;

// The start, stop, renew or delete in flight per workstation key. The menus read it through
// context, so a request settling does not rebuild the columns and close an open menu.
const SessionActionsInFlight = createContext<ReadonlyMap<string, SessionAction | undefined>>(
    new Map()
);

const RowActions: React.FC<RowActionsProps> = ({
    row,
    canStop,
    canStart,
    canDelete,
    canRenew,
    leaseEnabled,
    leaseHours,
    onStop,
    onStart,
    onDelete,
    onRenew,
    canShowWindowsPassword,
    onShowWindowsPassword,
}) => {
    // Stop and Start address the EDH session, so a row that never received one offers neither.
    // Delete addresses the row — by session id or, for a launch without one, by bootstrap id — and
    // stays disabled while a launch may still be in progress. A disabled item says why.
    const hasSession = !!row.sessionId;
    // The rows Connect opens a desktop for (ConnectLink's own condition).
    const connectable = row.state === "ACTIVE" && row.connectionAvailable && hasSession;
    // A second start sent before the first answers would revoke the credential the first minted and
    // could have EDH start the desktop twice, a second renew would find the first's credential
    // claim and report no sign-in, and a delete then would remove the desktop while the start binds
    // its key, so every action waits for the workstation's request.
    const inFlight = useContext(SessionActionsInFlight).get(workstationKey(row));
    const inFlightHint = inFlight && `A ${inFlight} of this workstation is in progress.`;
    // A desktop EDH reports stopping or interrupted reads STOPPED, but EDH starts it only once it
    // reports it stopped; a row without EDH's state leaves the start to EDH.
    const edhNotStoppedHint =
        row.state === "STOPPED" && row.edhState && row.edhState !== "stopped"
            ? `The workstation is still ${row.edhState}; it can be started once it has stopped.`
            : undefined;
    const items: ContextMenuItem[] = [
        {
            label: "Stop",
            onSelect: () => onStop(row),
            hidden: !canStop || !hasSession,
            disabled: row.state !== "ACTIVE" || !!inFlight,
            hint:
                row.state !== "ACTIVE"
                    ? "Only an active workstation can be stopped."
                    : inFlightHint,
        },
        {
            label: "Start",
            onSelect: () => onStart(row),
            hidden: !canStart || !hasSession,
            disabled: row.state !== "STOPPED" || !!edhNotStoppedHint || !!inFlight,
            hint:
                row.state !== "STOPPED"
                    ? "Only a stopped workstation can be started."
                    : edhNotStoppedHint ?? inFlightHint,
        },
        // Renew addresses the lease, so only a leased row under a live policy offers it.
        {
            label: "Renew lease",
            onSelect: () => onRenew(row),
            hidden: !canRenew || !hasSession || !row.leaseExpiresAt || !leaseEnabled,
            disabled: row.state === "DELETED" || !!inFlight,
            hint: inFlightHint || `Extends the lease to ${hoursWord(leaseHours)} from now`,
        },
        // The Windows sign-in is offered where Connect is, on Windows rows only.
        {
            label: "Show Windows password",
            onSelect: () => onShowWindowsPassword(row),
            hidden: !canShowWindowsPassword || row.osFamily !== "windows",
            disabled: !connectable,
            hint: connectable ? undefined : connectUnavailableReason(row),
        },
        // Delete is destructive — last in the menu, after every other action.
        {
            label: "Delete",
            onSelect: () => onDelete(row),
            danger: true,
            hidden: !canDelete,
            disabled: !isDeletable(row) || !!inFlight,
            hint: isDeletable(row) ? inFlightHint : CREATING_DELETE_HINT,
        },
    ];
    if (items.every((item) => item.hidden)) {
        return <span className="text-text-disabled">—</span>;
    }
    return (
        <ContextMenu
            items={items}
            trigger={
                <button
                    aria-label={`Actions for ${row.name}`}
                    className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                >
                    ⋮
                </button>
            }
        />
    );
};

interface WorkstationsBoardProps {
    /** Rendered as a tab panel under the page's own heading rather than as the whole page. */
    embedded?: boolean;
}

const WorkstationsBoard: React.FC<WorkstationsBoardProps> = ({ embedded = false }) => {
    const toast = useToast();
    const { data, isLoading, error, refetch, isFetching } = useWorkstations();
    const rows = data ?? NO_WORKSTATIONS;
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const { stopWorkstation, startWorkstation, deleteWorkstation, renewWorkstation } =
        useWorkstationMutations();
    const [launchOpen, setLaunchOpen] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState<WorkstationRecord | null>(null);
    const [signInTarget, setSignInTarget] = useState<WorkstationRecord | null>(null);
    // Message shown when a row action is rejected (e.g. no Tier-2 access, EDH unreachable).
    const [actionError, setActionError] = useState<string | null>(null);
    // The workstations with a start, stop, renew or delete in flight. The claim guards the
    // handlers; the menus render from the same claims.
    const {
        inFlight: actionsInFlight,
        claim: claimSession,
        release: releaseSession,
    } = useInFlight<SessionAction>();

    const canLaunch = can("POST", WORKSTATIONS_ROUTE);
    const canStop = can("POST", WORKSTATION_STOP_ROUTE);
    const canStart = can("POST", WORKSTATION_START_ROUTE);
    const canDelete = can("DELETE", WORKSTATION_ROUTE);
    const canConnect = can("GET", WORKSTATION_CONNECT_ROUTE);
    const canRenew = can("POST", WORKSTATION_RENEW_ROUTE);
    const canShowWindowsPassword = can("GET", WORKSTATION_WINDOWS_SIGN_IN_ROUTE);
    const canReadCatalog = can("GET", CATALOG_IMAGES_ROUTE);
    const canReadLease = can("GET", CATALOG_LEASE_ROUTE);

    // The lease terms the deployment applies. Absent until the policy answers, and read as no
    // leases until then, so nothing lease-related appears on a row it does not govern.
    const { data: leasePolicy } = useEdhCatalogLease(canReadLease);
    const leaseEnabled = !!leasePolicy?.enabled;
    const leaseHours = leasePolicy?.leaseHours ?? 0;
    const leaseGraceHours = leasePolicy?.graceHours ?? 0;

    // Names for the image ids the rows carry; a row stores the id only. Absent when the caller may
    // not read the catalog, and an image since deleted from it falls back to its id.
    const { data: catalogImages } = useEdhCatalogImages(canReadCatalog);
    const imageNamesById = useMemo(() => {
        const names = new Map<string, string>();
        (catalogImages ?? []).forEach((image) => names.set(image.imageId, image.name));
        return names;
    }, [catalogImages]);
    const rowText = useCallback(
        (row: WorkstationRecord) => searchableText(row, imageNamesById.get(row.imageId)),
        [imageNamesById]
    );
    const { searchText, setSearchText, visibleRows } = useTextFilter(rows, rowText);

    // A start, and a renew that re-issued the sign-in, report what became of the credential.
    const warnIfSignInMissing = (record: WorkstationRecord, action: "start" | "renew") => {
        const notice = sessionBootstrapNotice(record.sessionBootstrap, action);
        if (notice) {
            // Persists until dismissed: the user has to act on it (retry, or ask an admin).
            toast.warning(SESSION_BOOTSTRAP_NOTICE_TITLE, { description: notice, duration: 0 });
        }
    };

    const handleStop = async (row: WorkstationRecord) => {
        if (!row.sessionId || !claimSession(row.sessionId, "stop")) return;
        setActionError(null);
        try {
            await stopWorkstation.mutateAsync({ sessionId: row.sessionId });
            toast.success("Workstation stop requested", { description: row.name });
        } catch (err) {
            const unconfirmed = unconfirmedNotice("Stop", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${row.name}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Stop failed", { description: `${row.name}: ${toastErrorMessage(err)}` });
            setActionError(`Failed to stop ${row.name}: ${toastErrorMessage(err)}`);
        } finally {
            releaseSession(row.sessionId);
        }
    };

    const handleStart = async (row: WorkstationRecord) => {
        if (!row.sessionId || !claimSession(row.sessionId, "start")) return;
        setActionError(null);
        try {
            const record = await startWorkstation.mutateAsync({ sessionId: row.sessionId });
            toast.success("Workstation start requested", { description: row.name });
            warnIfSignInMissing(record, "start");
        } catch (err) {
            const unconfirmed = unconfirmedNotice("Start", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${row.name}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Start failed", { description: `${row.name}: ${toastErrorMessage(err)}` });
            setActionError(`Failed to start ${row.name}: ${toastErrorMessage(err)}`);
        } finally {
            releaseSession(row.sessionId);
        }
    };

    const handleRenew = async (row: WorkstationRecord) => {
        if (!row.sessionId || !claimSession(row.sessionId, "renew")) return;
        setActionError(null);
        try {
            const record = await renewWorkstation.mutateAsync({ sessionId: row.sessionId });
            toast.success("Workstation lease renewed", { description: row.name });
            warnIfSignInMissing(record, "renew");
        } catch (err) {
            toast.error("Renew failed", { description: `${row.name}: ${toastErrorMessage(err)}` });
            setActionError(`Failed to renew ${row.name}: ${toastErrorMessage(err)}`);
        } finally {
            releaseSession(row.sessionId);
        }
    };

    const handleDelete = async (row: WorkstationRecord) => {
        const key = workstationKey(row);
        if (!claimSession(key, "delete")) return;
        setActionError(null);
        try {
            await deleteWorkstation.mutateAsync({ sessionId: key });
            setDeleteTarget(null);
            toast.success("Workstation deleted", { description: row.name });
        } catch (err) {
            // Close the confirmation and report both as a banner and a toast — the dialog has no
            // error slot of its own.
            setDeleteTarget(null);
            const unconfirmed = unconfirmedNotice("Delete", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${row.name}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Delete failed", { description: `${row.name}: ${toastErrorMessage(err)}` });
            setActionError(`Failed to delete ${row.name}: ${toastErrorMessage(err)}`);
        } finally {
            releaseSession(key);
        }
    };

    // The row handlers close over the mutations and the toast, both fresh on many renders; the menu
    // reads them through a ref so the column definitions can stay stable across a poll. A fresh
    // `cell` function is a new element type, which remounts the cell and closes an open menu.
    const handlersRef = useRef({ stop: handleStop, start: handleStart, renew: handleRenew });
    handlersRef.current = { stop: handleStop, start: handleStart, renew: handleRenew };
    const onStop = useCallback((row: WorkstationRecord) => handlersRef.current.stop(row), []);
    const onStart = useCallback((row: WorkstationRecord) => handlersRef.current.start(row), []);
    const onRenew = useCallback((row: WorkstationRecord) => handlersRef.current.renew(row), []);

    const columns = useMemo<ColumnDef<WorkstationRecord>[]>(
        () => [
            {
                accessorKey: "name",
                header: "Name",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">{row.original.name}</span>
                ),
            },
            {
                accessorKey: "state",
                header: "Status",
                cell: ({ row }) => (
                    <div className="space-y-1">
                        <WorkstationStatusBadge
                            state={row.original.state}
                            edhState={row.original.edhState}
                            lastError={row.original.lastError}
                            connectionAvailable={row.original.connectionAvailable}
                            sessionId={row.original.sessionId}
                        />
                        {(row.original.state === "ORPHANED" ||
                            row.original.lastError === "LEASE_EXPIRED") && (
                            <span className="block text-xs text-text-secondary">
                                {describeLastError(row.original.lastError)}
                            </span>
                        )}
                        <EdhFailureDetail failure={row.original.edhFailure} />
                    </div>
                ),
            },
            {
                accessorKey: "imageId",
                header: "Image",
                cell: ({ row }) => {
                    const name = imageNamesById.get(row.original.imageId);
                    return name ? (
                        <div className="text-sm">
                            <div>{name}</div>
                            <div className="text-text-secondary font-mono text-xs">
                                {row.original.imageId}
                            </div>
                        </div>
                    ) : (
                        <span className="font-mono text-xs">{row.original.imageId}</span>
                    );
                },
            },
            {
                accessorKey: "osFamily",
                header: "OS",
                cell: ({ row }) => osFamilyLabel(row.original.osFamily),
            },
            { accessorKey: "instanceType", header: "Instance type" },
            {
                accessorKey: "createdAt",
                header: "Created",
                cell: ({ row }) => (
                    <span className="text-sm">{formatDate(row.original.createdAt)}</span>
                ),
            },
            ...(leaseEnabled
                ? ([
                      {
                          id: "remaining",
                          header: "Remaining",
                          cell: ({ row }) => (
                              <LeaseRemainingCell row={row.original} graceHours={leaseGraceHours} />
                          ),
                      },
                  ] as ColumnDef<WorkstationRecord>[])
                : []),
            ...(canConnect
                ? ([
                      {
                          id: "connect",
                          header: "Connect",
                          cell: ({ row }) => <ConnectLink row={row.original} />,
                      },
                  ] as ColumnDef<WorkstationRecord>[])
                : []),
            {
                id: "actions",
                header: "Actions",
                cell: ({ row }) => (
                    <RowActions
                        row={row.original}
                        canStop={canStop}
                        canStart={canStart}
                        canDelete={canDelete}
                        canRenew={canRenew}
                        leaseEnabled={leaseEnabled}
                        leaseHours={leaseHours}
                        onStop={onStop}
                        onStart={onStart}
                        onDelete={setDeleteTarget}
                        onRenew={onRenew}
                        canShowWindowsPassword={canShowWindowsPassword}
                        onShowWindowsPassword={setSignInTarget}
                    />
                ),
            },
        ],
        [
            canConnect,
            canStop,
            canStart,
            canDelete,
            canRenew,
            canShowWindowsPassword,
            leaseEnabled,
            leaseHours,
            leaseGraceHours,
            onStop,
            onStart,
            onRenew,
            imageNamesById,
        ]
    );

    const loading = isLoading || permissionsLoading;

    const launchButton = canLaunch && (
        <button onClick={() => setLaunchOpen(true)} className={btnPrimary}>
            Launch Workstation
        </button>
    );
    // The toolbar owns the search box, as the admin tabs' do, so the table's own is off.
    const refreshControls = (
        <div className="flex items-center gap-2 flex-wrap">
            <SearchInput
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                ariaLabel="Search workstations"
            />
            <RefreshButton onClick={() => refetch()} busy={isFetching} />
            <span className="text-sm text-text-secondary">{REFRESH_CAPTION}</span>
        </div>
    );

    return (
        <div
            className={
                embedded
                    ? "space-y-4"
                    : "orchestration-root orchestration-page space-y-4 bg-surface min-h-full"
            }
        >
            {embedded ? (
                // Under the page heading the primary action sits on the toolbar row.
                <div className="flex items-center justify-between gap-2 flex-wrap">
                    {refreshControls}
                    {launchButton}
                </div>
            ) : (
                <>
                    <div className="flex items-center justify-between">
                        <h1 className="text-text-primary">Workstations</h1>
                        {launchButton}
                    </div>
                    {refreshControls}
                </>
            )}

            {actionError && (
                <div
                    role="alert"
                    className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded flex items-start justify-between gap-3"
                >
                    <span>{actionError}</span>
                    <button
                        onClick={() => setActionError(null)}
                        aria-label="Dismiss action error"
                        className="bg-transparent border-0 text-lg leading-none cursor-pointer"
                    >
                        ×
                    </button>
                </div>
            )}

            {loading ? (
                <div role="status" className="p-8 text-center text-text-secondary">
                    Loading workstations...
                </div>
            ) : error ? (
                <div role="alert" className="p-4 text-vams-error">
                    Error loading workstations: {toastErrorMessage(error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary space-y-3">
                    <p>
                        {canLaunch
                            ? "You have no workstations yet. Launch one to get started."
                            : "You have no workstations yet."}
                    </p>
                    {launchButton}
                </div>
            ) : (
                <SessionActionsInFlight.Provider value={actionsInFlight}>
                    <DataTable
                        columns={columns}
                        rows={visibleRows}
                        ariaLabel="Workstations"
                        // The list re-reads on every poll, so rows are keyed by their own
                        // identity — otherwise an open action menu follows the table index, not
                        // the workstation.
                        getRowId={(row) => row.bootstrapId}
                        filtering={false}
                    />
                </SessionActionsInFlight.Provider>
            )}

            {launchOpen && (
                <LaunchWorkstationWizard open={true} onClose={() => setLaunchOpen(false)} />
            )}

            {deleteTarget && (
                <DeleteWorkstationDialog
                    workstationName={deleteTarget.name}
                    hasSession={!!deleteTarget.sessionId}
                    open={true}
                    busy={deleteWorkstation.isPending}
                    onConfirm={() => handleDelete(deleteTarget)}
                    onCancel={() => setDeleteTarget(null)}
                />
            )}

            {signInTarget?.sessionId && (
                <WindowsSignInDialog
                    key={signInTarget.sessionId}
                    sessionId={signInTarget.sessionId}
                    workstationName={signInTarget.name}
                    onClose={() => setSignInTarget(null)}
                />
            )}
        </div>
    );
};

/**
 * The Workstations board and the Simulations tab under one heading, for deployments with containers
 * on. The Simulations tab has a route of its own beneath the page's, so the side navigation can link
 * to either and a tab can be deep-linked. The tab is offered to every caller; the Simulations panel
 * itself says so when the caller may not list container jobs.
 */
const WorkstationsAndSimulations: React.FC = () => {
    const navigate = useNavigate();
    const tab: EdhFeatureTabKey = useMatch(EDH_SIMULATIONS_PATH) ? "simulations" : "workstations";
    const selectTab = (key: EdhFeatureTabKey) =>
        navigate(key === "simulations" ? EDH_SIMULATIONS_PATH : EDH_WORKSTATIONS_PATH, {
            replace: true,
        });

    return (
        <div className="orchestration-root orchestration-page space-y-4 bg-surface min-h-full">
            <h1 className="text-text-primary">Workstations</h1>
            <EdhFeatureTabs active={tab} onChange={selectTab} />
            <div
                id={EDH_FEATURE_PANEL_ID}
                role="tabpanel"
                aria-labelledby={edhFeatureTabId(tab)}
                tabIndex={0}
            >
                {tab === "workstations" ? <WorkstationsBoard embedded /> : <SimulationsTab />}
            </div>
        </div>
    );
};

/**
 * The web route is visible to anyone whose `web` constraints allow it (the seeded admin role allows
 * every route), while the API routes exist only where the add-on stack is deployed — so the page
 * gates itself on the feature flag and says so rather than failing every request.
 */
const WorkstationsPage: React.FC = () => {
    const enabledFeatures = useFeaturesEnabled();
    if (!enabledFeatures.includes(addonFeaturesEnabled.EDH_WORKSTATIONS)) {
        return (
            <div className="orchestration-root orchestration-page space-y-4 bg-surface min-h-full">
                <h1 className="text-text-primary">Workstations</h1>
                <NotEnabledPanel />
            </div>
        );
    }
    if (!enabledFeatures.includes(addonFeaturesEnabled.EDH_CONTAINERS)) {
        return <WorkstationsBoard />;
    }
    return <WorkstationsAndSimulations />;
};

export default WorkstationsPage;
