/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { WorkstationLastError, WorkstationState } from "../types";

interface WorkstationStatusBadgeProps {
    state: WorkstationState;
    /** The raw EDH session state, shown as the badge title when EDH has reported one. */
    edhState?: string | null;
    /** The failure code the row carries; titles the badge with its explanation. */
    lastError?: string | null;
    /**
     * Whether EDH reports the desktop running, so that Connect can fetch its connection. An active
     * desktop that is not yet connectable is still starting (a fresh launch, or a stopped desktop
     * brought back), and reads so.
     */
    connectionAvailable?: boolean;
    /**
     * The EDH session id the row carries. A creating row with one is a stopped desktop being started
     * again rather than a launch, and reads as starting.
     */
    sessionId?: string | null;
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
};

export const LAST_ERROR_FALLBACK_COPY = "The launch did not complete.";

/** The explanation for a `lastError` code, or the generic sentence for none or an unknown one. */
export function describeLastError(code?: string | null): string {
    return (code && LAST_ERROR_COPY[code as WorkstationLastError]) || LAST_ERROR_FALLBACK_COPY;
}

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
    connectionAvailable,
    sessionId,
}) => {
    const isStarting =
        (state === "ACTIVE" && connectionAvailable === false) ||
        (state === "CREATING" && !!sessionId);
    const config = isStarting
        ? { ...statusConfig.CREATING, label: "Starting" }
        : statusConfig[state] || unknownStatusStyle(state as string);
    // The non-terminal phases get a moving indicator, a pulsing dot.
    const isMoving = state === "CREATING" || isStarting;

    return (
        <span
            title={
                state === "ORPHANED" || lastError === "LEASE_EXPIRED"
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
