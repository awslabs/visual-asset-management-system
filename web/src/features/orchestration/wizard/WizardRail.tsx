/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

export type RailStatus =
    | "Incomplete"
    | "Needs template"
    | "Ready"
    | "No configuration"
    | "Not found"
    | "Error";

export interface RailStep {
    id: string;
    label: string;
    /** Completed by an enclosing dialog before this rail's own steps (the workflow picker). */
    done?: boolean;
    status?: RailStatus;
}

interface WizardRailProps {
    steps: RailStep[];
    currentId: string;
    /** Called with the id of a visited step; when absent every row is inert. */
    onJumpTo?: (stepId: string) => void;
}

// The same colour pairs StatusBadge uses, so a step's state reads like an execution's.
const STATUS_CLASSES: Record<RailStatus, string> = {
    Ready: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
    "No configuration": "bg-surface-secondary text-text-secondary",
    Incomplete: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
    "Needs template": "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
    "Not found": "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    Error: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
};

/**
 * The execute dialog's step list. One navigation landmark with one label node per step: vertical
 * beside the content on md and up, a horizontal strip above it below md (CSS only, so jsdom and
 * the exact-text step assertions see a single rail). Visited steps are buttons that jump back;
 * the current and future steps are plain rows.
 */
const WizardRail: React.FC<WizardRailProps> = ({ steps, currentId, onJumpTo }) => {
    const currentIndex = steps.findIndex((s) => s.id === currentId);
    return (
        <nav aria-label="Execution steps" className="shrink-0 md:w-48">
            <ol className="flex flex-row gap-1 overflow-x-auto md:flex-col md:gap-0.5">
                {steps.map((step, index) => {
                    const isCurrent = step.id === currentId;
                    const visited = !isCurrent && (step.done || index < currentIndex);
                    const canJump = visited && !!onJumpTo;
                    const rowClass =
                        "flex w-full shrink-0 items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm";
                    const inner = (
                        <>
                            <span
                                aria-hidden="true"
                                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                                    isCurrent
                                        ? "bg-blue-600 text-white"
                                        : visited
                                        ? "bg-green-600 text-white"
                                        : "orch-outline border border-border-default text-text-secondary"
                                }`}
                            >
                                {visited ? "✓" : index + 1}
                            </span>
                            <span
                                className={`truncate ${
                                    isCurrent
                                        ? "font-semibold text-text-primary"
                                        : visited
                                        ? "text-text-primary"
                                        : "text-text-secondary"
                                }`}
                            >
                                {step.label}
                            </span>
                            {step.status && (
                                <span
                                    className={`ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[11px] leading-none ${
                                        STATUS_CLASSES[step.status]
                                    }`}
                                >
                                    {step.status}
                                </span>
                            )}
                        </>
                    );
                    return (
                        <li key={step.id} aria-current={isCurrent ? "step" : undefined}>
                            {canJump ? (
                                <button
                                    type="button"
                                    onClick={() => onJumpTo?.(step.id)}
                                    className={`${rowClass} hover:bg-surface-hover`}
                                >
                                    {inner}
                                </button>
                            ) : (
                                <div
                                    className={`${rowClass} ${
                                        isCurrent ? "bg-surface-secondary" : ""
                                    }`}
                                >
                                    {inner}
                                </div>
                            )}
                        </li>
                    );
                })}
            </ol>
        </nav>
    );
};

export default WizardRail;
