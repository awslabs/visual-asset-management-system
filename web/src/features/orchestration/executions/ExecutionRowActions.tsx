/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ContextMenu, { type ContextMenuItem } from "../components/ContextMenu";
import type { Execution } from "../types";

interface ExecutionRowActionsProps {
    execution: Execution;
    can: (method: string, pathTemplate: string) => boolean;
    onView: () => void;
    onAbort: () => void;
    onAbortGroup?: () => void;
    onRerun: () => void;
    onLogs: () => void;
    onPermanentDelete: () => void;
    onOpenDetails: () => void;
}

const ExecutionRowActions: React.FC<ExecutionRowActionsProps> = ({
    execution,
    can,
    onView,
    onAbort,
    onAbortGroup,
    onRerun,
    onLogs,
    onPermanentDelete,
    onOpenDetails,
}) => {
    const isTerminal = ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT", "COMPLETE"].includes(
        execution.executionStatus
    );
    const isNonTerminal = ["NEW", "RUNNING"].includes(execution.executionStatus);
    const hasGroup = !!execution.executionGroupId;

    const menuItems: ContextMenuItem[] = [
        {
            label: "View results",
            onSelect: onView,
        },
        {
            label: "Abort",
            onSelect: onAbort,
            disabled: !isNonTerminal,
            hidden: !can("DELETE", "/workflows/executions/{executionId}"),
        },
        {
            label: "Abort group",
            onSelect: onAbortGroup || (() => {}),
            disabled: !isNonTerminal || !hasGroup,
            hidden: !can("DELETE", "/workflows/executions/{executionId}") || !hasGroup,
        },
        {
            label: "Rerun",
            onSelect: onRerun,
            hidden: !can("POST", "/workflows/executions/{executionId}/rerun"),
        },
        {
            label: "Logs",
            onSelect: onLogs,
            hidden: !can("GET", "/workflows/executions/{executionId}/logs"),
        },
        {
            label: "Open full details",
            onSelect: onOpenDetails,
        },
        // Permanent delete is destructive — keep it last in the menu, after every other action.
        {
            label: "Permanent delete",
            onSelect: onPermanentDelete,
            danger: true,
            hidden: !can("DELETE", "/workflows/executions/{executionId}/permanent"),
        },
    ];

    return (
        <ContextMenu
            items={menuItems}
            trigger={
                <button
                    aria-label="Execution actions"
                    className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-900 hover:bg-gray-100 dark:hover:text-gray-100 dark:hover:bg-gray-700 cursor-pointer"
                >
                    ⋯
                </button>
            }
        />
    );
};

export default ExecutionRowActions;
