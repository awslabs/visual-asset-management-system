/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/**
 * The narrowest viewport, in pixels, that shows a list as a table; below it each row is a card, so
 * the status and the actions are not off-screen. My Workstations and the admin Sessions tab share it.
 */
export const WORKSTATION_TABLE_MIN_WIDTH_PX = 640;

/** A list shown as cards, one `RecordCard` per row. */
export const RecordCardList: React.FC<{ ariaLabel: string; children: React.ReactNode }> = ({
    ariaLabel,
    children,
}) => (
    <ul aria-label={ariaLabel} className="space-y-3 list-none p-0 m-0">
        {children}
    </ul>
);

interface RecordCardProps {
    /** The card's accessible name, the row's name as the list reads it. */
    label: string;
    title: React.ReactNode;
    actions: React.ReactNode;
    children: React.ReactNode;
}

/**
 * One row as a card: its name and actions menu on the top line, the rest beneath. Tailwind's preflight
 * is off, so the border paints only with `orch-outline`.
 */
export const RecordCard: React.FC<RecordCardProps> = ({ label, title, actions, children }) => (
    <li
        aria-label={label}
        className="orch-outline rounded-lg border border-border-default bg-surface-container p-4 space-y-3"
    >
        <div className="flex items-start justify-between gap-2">
            <span className="font-semibold text-text-primary break-words min-w-0">{title}</span>
            {actions}
        </div>
        {children}
    </li>
);

/** A card's labelled fields: each `dt` label beside its `dd` value. */
export const RecordCardFields: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm m-0">{children}</dl>
);

/** The rows a page of cards holds, the page size DataTable uses for the table. */
export const CARD_PAGE_SIZE = 10;

interface CardPagerProps {
    pageIndex: number;
    pageCount: number;
    onPageChange: (pageIndex: number) => void;
}

/** Previous, `Page <n> of <m>` and Next beneath a list of cards, as DataTable shows them beneath a table. */
export const CardPager: React.FC<CardPagerProps> = ({ pageIndex, pageCount, onPageChange }) =>
    pageCount > 1 ? (
        <div className="flex items-center justify-end gap-3 mt-3 text-sm">
            <button
                type="button"
                onClick={() => onPageChange(pageIndex - 1)}
                disabled={pageIndex === 0}
                className="px-3 py-1.5 border border-border-input rounded text-text-primary hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed"
            >
                Previous
            </button>
            <span className="text-text-secondary">
                Page {pageIndex + 1} of {pageCount}
            </span>
            <button
                type="button"
                onClick={() => onPageChange(pageIndex + 1)}
                disabled={pageIndex >= pageCount - 1}
                className="px-3 py-1.5 border border-border-input rounded text-text-primary hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed"
            >
                Next
            </button>
        </div>
    ) : null;
