/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { ExecutionStatus } from "../types";

interface StatusBadgeProps {
    status: ExecutionStatus;
}

interface StatusStyle {
    label: string;
    bgColor: string;
    textColor: string;
    icon?: string;
}

const statusConfig: Record<ExecutionStatus, StatusStyle> = {
    SUCCEEDED: {
        label: "Succeeded",
        bgColor: "bg-green-100 dark:bg-green-900/30",
        textColor: "text-green-800 dark:text-green-300",
        icon: "✓",
    },
    RUNNING: {
        label: "Running",
        bgColor: "bg-blue-100 dark:bg-blue-900/30",
        textColor: "text-blue-800 dark:text-blue-300",
    },
    NEW: {
        label: "Queued",
        bgColor: "bg-blue-50 dark:bg-blue-900/20",
        textColor: "text-blue-700 dark:text-blue-300",
    },
    FAILED: {
        label: "Failed",
        bgColor: "bg-red-100 dark:bg-red-900/30",
        textColor: "text-red-800 dark:text-red-300",
        icon: "✕",
    },
    ABORTED: {
        label: "Aborted",
        bgColor: "bg-orange-100 dark:bg-orange-900/30",
        textColor: "text-orange-800 dark:text-orange-300",
        icon: "⊘",
    },
    TIMED_OUT: {
        label: "Timed Out",
        bgColor: "bg-red-100 dark:bg-red-900/30",
        textColor: "text-red-800 dark:text-red-300",
        icon: "⏱",
    },
    COMPLETE: {
        label: "Complete",
        bgColor: "bg-green-100 dark:bg-green-900/30",
        textColor: "text-green-800 dark:text-green-300",
        icon: "✓",
    },
};

// Statuses are passed through from Step Functions, so a value outside the mapped set is possible.
// An unmapped status renders as a neutral badge carrying the raw value instead of failing the row.
const unknownStatusStyle = (status: string): StatusStyle => ({
    label: status || "Unknown",
    bgColor: "bg-surface-secondary",
    textColor: "text-text-secondary",
});

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
    const config = statusConfig[status] || unknownStatusStyle(status as string);
    // Non-terminal states get a moving indicator: RUNNING a spinner, NEW (queued) a pulsing dot.
    const isRunning = status === "RUNNING";
    const isQueued = status === "NEW";

    return (
        <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium ${config.bgColor} ${config.textColor}`}
        >
            {isRunning ? (
                <span
                    aria-hidden="true"
                    className="orch-outline inline-block h-3 w-3 rounded-full border-2 border-current border-r-transparent animate-spin"
                />
            ) : isQueued ? (
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

export default StatusBadge;
