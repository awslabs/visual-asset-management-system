/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { ExecutionStatus } from "../types";

interface StatusBadgeProps {
    status: ExecutionStatus;
}

const statusConfig: Record<
    ExecutionStatus,
    { label: string; bgColor: string; textColor: string; icon?: string }
> = {
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
        icon: "●",
    },
    NEW: {
        label: "New",
        bgColor: "bg-gray-100 dark:bg-gray-800/50",
        textColor: "text-gray-700 dark:text-gray-400",
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

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
    const config = statusConfig[status];
    const isRunning = status === "RUNNING";

    return (
        <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${config.bgColor} ${config.textColor}`}
        >
            {config.icon && (
                <span className={isRunning ? "animate-pulse" : ""}>{config.icon}</span>
            )}
            <span>{config.label}</span>
        </span>
    );
};

export default StatusBadge;
