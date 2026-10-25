/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { RESERVATION_FULL_START_MESSAGE } from "../api/outcome";
import { isStartStalled } from "../labels";
import type {
    EdhFailure,
    StartFailure,
    WorkstationActivity,
    WorkstationLastError,
    WorkstationState,
} from "../types";

interface WorkstationStatusBadgeProps {
    state: WorkstationState;
    /** The raw EDH session state, shown as the badge title when EDH has reported one. */
    edhState?: string | null;
    /** The failure code the row carries; titles the badge with its explanation. `START_STALLED` on a
     * CREATING row with no `activity` reads Start stalled. */
    lastError?: string | null;
    /**
     * What the service says the workstation is doing. `starting` reads Starting and `stopping` reads
     * Stopping, whatever `state` says; null reads the state. Absent (a service that predates the
     * field), the badge falls back to telling a start from the state, as below.
     */
    activity?: WorkstationActivity | null;
    /**
     * Whether EDH reports the desktop running, so that Connect can fetch its connection. Read only
     * when `activity` is absent: an active desktop that is not yet connectable is still starting.
     */
    connectionAvailable?: boolean;
    /**
     * The EDH session id the row carries. Read only when `activity` is absent: a creating row with
     * one is a stopped desktop being started again rather than a launch, and reads as starting.
     */
    sessionId?: string | null;
    /** The instance type, named by the sentence of a start refused for capacity. */
    instanceType?: string | null;
    /** Why the last start left the desktop stopped; titles the badge with its sentence. */
    startFailure?: StartFailure | null;
}

/** The sentence shown for each `lastError` code; a code outside the set falls back to the generic one. */
export const LAST_ERROR_COPY: Record<WorkstationLastError, string> = {
    USER_NOT_PROVISIONED:
        "Your EDH user account has not been provisioned. Ask an administrator to enable it.",
    QUOTA_EXCEEDED:
        "EDH refused the launch because a desktop, session or budget quota is used up. Delete a workstation or ask an administrator.",
    IMAGE_UNAVAILABLE: "The chosen image is no longer available in EDH.",
    CAPACITY_UNAVAILABLE:
        "EDH found no available AWS capacity for the chosen instance type. Launch again later or choose another instance type.",
    EDH_REFUSED:
        "EDH refused the launch request. Try another image or instance type, or ask an administrator.",
    LAUNCH_ERROR: "EDH reported an error while starting the workstation.",
    CREATE_TIMED_OUT: "The launch did not complete in time.",
    LEASE_EXPIRED: "The workstation's lease has expired and it was automatically stopped.",
    RESERVATION_FULL:
        "The capacity reservation had no free capacity and does not allow an on-demand launch. Launch again later, choose On-demand, or ask an administrator.",
    START_FAILED:
        "The workstation did not start; it is still stopped with its disk. Start it again, or ask an administrator.",
    START_STALLED:
        "EDH has not finished starting this workstation after 30 minutes. Stop it, then start it again; if it stalls again, ask your administrator.",
};

export const LAST_ERROR_FALLBACK_COPY = "The launch did not complete.";

/** The explanation for a `lastError` code, or the generic sentence for none or an unknown one. */
export function describeLastError(code?: string | null): string {
    return (code && LAST_ERROR_COPY[code as WorkstationLastError]) || LAST_ERROR_FALLBACK_COPY;
}

const present = (value: unknown): value is string => typeof value === "string" && value !== "";

/** The `lastError` codes a start the service could not carry out leaves on a STOPPED row. */
export const START_FAILURE_CODES: ReadonlyArray<WorkstationLastError> = [
    "CAPACITY_UNAVAILABLE",
    "RESERVATION_FULL",
    "START_FAILED",
];

/** The row fields the start-failure sentence and detail line are read from. */
export interface StartFailureRow {
    state: WorkstationState | string | null;
    activity?: WorkstationActivity | null;
    lastError?: string | null;
    instanceType?: string | null;
    startFailure?: StartFailure | null;
}

/**
 * The sentence a STOPPED row shows when AWS had no on-demand capacity for its start. A stopped
 * desktop keeps its root disk in its instance's Availability Zone, so it can start only there.
 */
export function capacityStartFailureMessage(
    instanceType?: string | null,
    availabilityZone?: string | null
): string {
    const type = present(instanceType) ? instanceType : "its instance type";
    const zone = present(availabilityZone) ? ` (${availabilityZone})` : "";
    return (
        `AWS had no capacity for ${type} in this workstation's Availability Zone${zone} when it ` +
        "was started. A stopped workstation keeps its disk and starts only in that zone, so it " +
        "stays stopped; start it again later."
    );
}

/**
 * Why the last start left a STOPPED row stopped, as a sentence and the AWS detail line beneath it
 * (`AWS: <errorCode> in <Availability Zone>`, or the code alone; null when the start failure names
 * no code). Null for a row whose start did not fail, and while a new start of it runs: the badge then
 * reads Starting, and the earlier failure no longer describes it. A CREATING row whose start the
 * sweep found stalled (`isStartStalled`) gets the `START_STALLED` sentence and no detail line: EDH
 * reported no AWS error. Shared by My Workstations and the admin Sessions tab, and by
 * `vamscli edh workstation get` through workstationStatusCases.json.
 */
export function describeStartFailure(
    row: StartFailureRow
): { message: string; detail: string | null } | null {
    if (isStartStalled(row)) return { message: LAST_ERROR_COPY.START_STALLED, detail: null };
    if (row.state !== "STOPPED" || row.activity) return null;
    const code = row.lastError as WorkstationLastError | null | undefined;
    if (!code || !START_FAILURE_CODES.includes(code)) return null;
    const failure = row.startFailure ?? null;
    const message =
        code === "CAPACITY_UNAVAILABLE"
            ? capacityStartFailureMessage(row.instanceType, failure?.availabilityZone)
            : code === "RESERVATION_FULL"
            ? RESERVATION_FULL_START_MESSAGE
            : LAST_ERROR_COPY.START_FAILED;
    const detail = present(failure?.errorCode)
        ? `AWS: ${failure?.errorCode}${
              present(failure?.availabilityZone) ? ` in ${failure?.availabilityZone}` : ""
          }`
        : null;
    return { message, detail };
}

/**
 * The start failure as two lines of text beneath the row's badge: the sentence, then the AWS detail
 * when one was recorded. Nothing for a row whose start did not fail. Shared by My Workstations and
 * the admin Sessions tab.
 */
export const StartFailureNotice: React.FC<{ row: StartFailureRow }> = ({ row }) => {
    const failure = describeStartFailure(row);
    if (!failure) return null;
    return (
        <>
            <span className="block text-xs text-text-secondary">{failure.message}</span>
            {failure.detail && (
                <span className="block text-xs text-text-secondary font-mono break-all">
                    {failure.detail}
                </span>
            )}
        </>
    );
};

/**
 * The detail line for the launch failure VAMS captured from EDH: the AWS error code (with the
 * operation that returned it when captured), else the checkpoint a desktop setup failed at; null when
 * the row carries no failure or the failure names neither.
 */
export function describeEdhFailure(failure?: EdhFailure | null): string | null {
    if (!failure) return null;
    if (present(failure.errorCode)) {
        return present(failure.operation)
            ? `AWS: ${failure.errorCode} (${failure.operation})`
            : `AWS: ${failure.errorCode}`;
    }
    if (present(failure.checkpoint)) return `Desktop setup failed at ${failure.checkpoint}`;
    return null;
}

/**
 * The captured failure as a line of text beneath the row's reason, or nothing when the row carries
 * none. Shared by My Workstations and the admin Sessions tab.
 */
export const EdhFailureDetail: React.FC<{ failure?: EdhFailure | null }> = ({ failure }) => {
    const detail = describeEdhFailure(failure);
    return detail ? (
        <span className="block text-xs text-text-secondary font-mono break-all">{detail}</span>
    ) : null;
};

interface StatusStyle {
    label: string;
    bgColor: string;
    textColor: string;
    icon?: string;
}

const statusConfig: Record<WorkstationState, StatusStyle> = {
    CREATING: {
        label: "Creating",
        bgColor: "bg-blue-50 dark:bg-blue-900/20",
        textColor: "text-blue-700 dark:text-blue-300",
    },
    ACTIVE: {
        label: "Active",
        bgColor: "bg-green-100 dark:bg-green-900/30",
        textColor: "text-green-800 dark:text-green-300",
        icon: "✓",
    },
    STOPPED: {
        label: "Stopped",
        bgColor: "bg-surface-secondary",
        textColor: "text-text-secondary",
        icon: "■",
    },
    DELETED: {
        label: "Deleted",
        bgColor: "bg-red-100 dark:bg-red-900/30",
        textColor: "text-red-800 dark:text-red-300",
        icon: "✕",
    },
    ORPHANED: {
        label: "Orphaned",
        bgColor: "bg-orange-100 dark:bg-orange-900/30",
        textColor: "text-orange-800 dark:text-orange-300",
        icon: "⊘",
    },
};

// A start EDH accepted and has not finished after 30 minutes: not in progress any more, so no moving
// dot, and the warning colours with the warning sign. The words carry the meaning on their own.
const startStalledStyle: StatusStyle = {
    label: "Start stalled",
    bgColor: "bg-yellow-100 dark:bg-yellow-900/30",
    textColor: "text-yellow-800 dark:text-yellow-300",
    icon: "⚠",
};

// States come from the session row, so a value outside the mapped set is possible after a data
// migration. An unmapped state renders as a neutral badge carrying the raw value.
const unknownStatusStyle = (state: string): StatusStyle => ({
    label: state || "Unknown",
    bgColor: "bg-surface-secondary",
    textColor: "text-text-secondary",
});

const WorkstationStatusBadge: React.FC<WorkstationStatusBadgeProps> = ({
    state,
    edhState,
    lastError,
    activity,
    connectionAvailable,
    sessionId,
    instanceType,
    startFailure,
}) => {
    // A start the sweep found stalled reads Start stalled; otherwise the service's `activity` decides,
    // and a record without the field falls back to the state.
    const isStalled = isStartStalled({ state, activity, lastError });
    const isStarting =
        activity === undefined
            ? (state === "ACTIVE" && connectionAvailable === false) ||
              (state === "CREATING" && !!sessionId)
            : activity === "starting";
    const isStopping = activity === "stopping";
    const config = isStalled
        ? startStalledStyle
        : isStarting
        ? { ...statusConfig.CREATING, label: "Starting" }
        : isStopping
        ? { ...statusConfig.STOPPED, label: "Stopping" }
        : statusConfig[state] || unknownStatusStyle(state as string);
    // The non-terminal phases get a moving indicator, a pulsing dot; a stalled start is not moving.
    const isMoving = !isStalled && (state === "CREATING" || isStarting || isStopping);
    const failedStart = describeStartFailure({
        state,
        activity,
        lastError,
        instanceType,
        startFailure,
    });

    return (
        <span
            title={
                failedStart
                    ? failedStart.message
                    : state === "ORPHANED" || lastError === "LEASE_EXPIRED"
                    ? describeLastError(lastError)
                    : edhState || undefined
            }
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium ${config.bgColor} ${config.textColor}`}
        >
            {isMoving ? (
                <span
                    aria-hidden="true"
                    className="inline-block h-2 w-2 rounded-full bg-current animate-pulse"
                />
            ) : (
                config.icon && <span aria-hidden="true">{config.icon}</span>
            )}
            <span>{config.label}</span>
        </span>
    );
};

export default WorkstationStatusBadge;
