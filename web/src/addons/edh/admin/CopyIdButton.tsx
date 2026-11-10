/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useToast } from "../../../features/orchestration/components/ToastProvider";

interface CopyIdButtonProps {
    /** The id copied. */
    value: string;
    /** What the id is, as the button names it and the toast reports it: `Session id`. */
    what: string;
}

/**
 * A copy-to-clipboard button for an id a table shows short or not at all. Its accessible name and
 * tooltip carry the whole id, so it can be read without copying it.
 */
const CopyIdButton: React.FC<CopyIdButtonProps> = ({ value, what }) => {
    const toast = useToast();
    const name = `Copy ${what}: ${value}`;
    const copy = async () => {
        try {
            if (!navigator.clipboard) throw new Error("clipboard unavailable");
            await navigator.clipboard.writeText(value);
            toast.success(`${what} copied`, { description: value });
        } catch {
            toast.error(`${what} could not be copied`, {
                description: "The browser refused clipboard access.",
            });
        }
    };
    return (
        <button
            type="button"
            aria-label={name}
            title={name}
            onClick={copy}
            className="inline-flex items-center justify-center w-6 h-6 bg-transparent border-0 rounded text-text-secondary cursor-pointer hover:text-text-primary hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
            <svg
                aria-hidden="true"
                viewBox="0 0 16 16"
                className="w-3.5 h-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
            >
                <rect x="5" y="5" width="9" height="9" rx="1.5" />
                <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" />
            </svg>
        </button>
    );
};

export default CopyIdButton;
