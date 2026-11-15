/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
import ExecutionResultSurface from "./ExecutionResultSurface";

interface ExecutionOutputTypeBadgesProps {
    executionId: string;
    outputTypes?: string[];
}

const BADGE_BASE = "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium";

// One class per known type so File / Metadata / Result read as distinct at a glance, with a
// light/dark pair each. An unknown value (forward compatibility) falls back to a neutral style.
const BADGE_STYLE: Record<string, string> = {
    File: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200",
    Metadata: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-200",
    Result: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-200",
};
const BADGE_NEUTRAL = "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200";

/**
 * The Output Types cell for an execution row, on BOTH the global board and the per-asset Workflows
 * tab (the backend threads outputTypes onto both list paths). File and Metadata render as static
 * badges; Result renders as a button that opens the lazy-loaded full-result surface. A run that
 * produced nothing — or a legacy run whose configuration row predates the attribute — shows a dash.
 */
const ExecutionOutputTypeBadges: React.FC<ExecutionOutputTypeBadgesProps> = ({
    executionId,
    outputTypes,
}) => {
    const [resultOpen, setResultOpen] = useState(false);
    const resultBadgeRef = useRef<HTMLButtonElement>(null);
    const types = outputTypes ?? [];

    if (types.length === 0) {
        return <span className="text-sm text-text-secondary">—</span>;
    }

    return (
        <div className="flex flex-wrap items-center gap-1">
            {types.map((type) => {
                const style = BADGE_STYLE[type] ?? BADGE_NEUTRAL;
                if (type === "Result") {
                    // Interactive: opens the full result surface. Stop propagation so a click here
                    // does not also fire the row-click quick-view.
                    return (
                        <button
                            key={type}
                            ref={resultBadgeRef}
                            type="button"
                            className={`${BADGE_BASE} ${style} cursor-pointer underline-offset-2 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500`}
                            onClick={(e) => {
                                e.stopPropagation();
                                setResultOpen(true);
                            }}
                            aria-label="View execution result"
                            data-testid="output-type-badge-result"
                        >
                            {type}
                        </button>
                    );
                }
                return (
                    <span
                        key={type}
                        className={`${BADGE_BASE} ${style}`}
                        data-testid={`output-type-badge-${type.toLowerCase()}`}
                    >
                        {type}
                    </span>
                );
            })}
            {types.includes("Result") && (
                <div onClick={(e) => e.stopPropagation()}>
                    <ExecutionResultSurface
                        executionId={executionId}
                        open={resultOpen}
                        onOpenChange={setResultOpen}
                        returnFocusRef={resultBadgeRef}
                    />
                </div>
            )}
        </div>
    );
};

export default ExecutionOutputTypeBadges;
