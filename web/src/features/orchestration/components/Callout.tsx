/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

export type CalloutTone = "info" | "warning" | "error" | "success";

interface CalloutProps {
    tone: CalloutTone;
    /** Bold first line. */
    title?: React.ReactNode;
    children?: React.ReactNode;
    className?: string;
    /** Only the launch-error banner and the picker's incompatibility block are alerts. */
    role?: "alert";
    /** For lists that update as the user works (readiness, review blockers). */
    "aria-live"?: "polite";
}

const TONE_CLASSES: Record<CalloutTone, string> = {
    info: "border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200",
    warning:
        "border-yellow-300 bg-yellow-50 text-yellow-900 dark:border-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-200",
    error: "border-red-300 bg-red-50 text-red-900 dark:border-red-700 dark:bg-red-900/20 dark:text-red-200",
    success:
        "border-green-300 bg-green-50 text-green-900 dark:border-green-700 dark:bg-green-900/20 dark:text-green-200",
};

/**
 * A bordered notice box. Carries no role of its own: the caller decides whether it is an alert, a
 * live region, or plain text, so a step never ends up with two `role="alert"` regions.
 */
const Callout: React.FC<CalloutProps> = ({
    tone,
    title,
    children,
    className = "",
    role,
    "aria-live": ariaLive,
}) => (
    <div
        role={role}
        aria-live={ariaLive}
        className={`orch-outline rounded-md border p-3 text-sm ${TONE_CLASSES[tone]} ${className}`}
    >
        {title && <p className="font-semibold">{title}</p>}
        {children}
    </div>
);

export default Callout;
