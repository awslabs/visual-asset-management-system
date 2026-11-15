/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { BusyButton } from "./BusyControls";

interface RefreshButtonProps {
    onClick: () => void;
    /** Spins the icon while a refetch is in flight. */
    busy?: boolean;
    ariaLabel?: string;
}

/**
 * Icon-only refresh control for the orchestration list toolbars, sitting next to the search box —
 * the Cloudscape-free counterpart of the `iconName="refresh"` button the rest of the app uses. Force
 * a refetch of the current list's data (auto-refresh via TanStack Query still applies independently).
 * While a read is in flight it keeps focus and its name, and says it is busy through `aria-busy`
 * alongside `aria-disabled` (see `BusyButton`); a press then does nothing.
 */
const RefreshButton: React.FC<RefreshButtonProps> = ({
    onClick,
    busy = false,
    ariaLabel = "Refresh",
}) => (
    <BusyButton
        busy={busy}
        onClick={onClick}
        aria-busy={busy || undefined}
        aria-label={ariaLabel}
        title={ariaLabel}
        className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border-input bg-surface text-text-primary hover:bg-surface-hover"
    >
        <svg
            className={`h-4 w-4 ${busy ? "animate-spin" : ""}`}
            viewBox="0 0 16 16"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
        >
            {/* Circular-arrow refresh glyph. */}
            <path
                d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
            />
            <path
                d="M13.5 2.5V5H11"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    </BusyButton>
);

export default RefreshButton;
