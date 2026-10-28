/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Dialog from "../components/Dialog";
import { useExecutionResults } from "../api/queries";
import { btnSecondary } from "../components/controlStyles";

interface ExecutionResultSurfaceProps {
    executionId: string;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** The control that opened the surface; focus returns to it on close. */
    returnFocusRef?: React.RefObject<HTMLElement>;
}

/** Human-readable byte count, so the caption reads "1.4 MB" rather than "1468006 bytes". */
function formatBytes(bytes: number): string {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, i);
    return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

/**
 * The lazy-loaded Result surface: a scrollable, selectable, copyable view of a run's result text. The
 * query is enabled only while the modal is open, so opening a Result badge is what pays for the read
 * — a row that merely carries the badge costs nothing until clicked.
 *
 * The service pages the result by its 5 MiB response budget; "Load more" appends the next page, so
 * the whole result is reachable here. A banner marks a result a row of which was itself cut.
 */
const ExecutionResultSurface: React.FC<ExecutionResultSurfaceProps> = ({
    executionId,
    open,
    onOpenChange,
    returnFocusRef,
}) => {
    const [copied, setCopied] = useState(false);
    const { data, isLoading, isError, hasNextPage, isFetchingNextPage, fetchNextPage } =
        useExecutionResults(executionId, open);

    const content = data?.resultContent ?? "";
    const truncated = Boolean(data?.resultContentTruncated);
    const rowCount = data?.resultRowCount ?? 0;
    const byteLen = data?.resultContentBytes ?? 0;

    const onCopy = async () => {
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
        } catch {
            // Clipboard denied (insecure context / permission); the text is still selectable.
            setCopied(false);
        }
    };

    return (
        <Dialog
            open={open}
            onOpenChange={onOpenChange}
            title="Execution result"
            size="lg"
            returnFocusRef={returnFocusRef}
        >
            {isLoading && (
                <div
                    className="flex items-center gap-2 py-8 text-text-secondary"
                    role="status"
                    aria-live="polite"
                    data-testid="result-surface-loading"
                >
                    {/* A spinner the UX bar can assert resolves to content. */}
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-text-secondary border-t-transparent" />
                    Loading result…
                </div>
            )}

            {isError && (
                <div className="py-8 text-text-error" role="alert">
                    The result could not be loaded.
                </div>
            )}

            {!isLoading && !isError && (
                <div className="flex flex-col gap-3">
                    <div className="flex items-center justify-between gap-3 text-sm text-text-secondary">
                        <span data-testid="result-surface-caption">
                            {rowCount} row{rowCount === 1 ? "" : "s"} · {formatBytes(byteLen)}
                            {hasNextPage ? " loaded" : ""}
                        </span>
                        <button
                            type="button"
                            className={btnSecondary}
                            onClick={onCopy}
                            disabled={!content}
                            data-testid="result-surface-copy"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    </div>

                    {truncated && (
                        <div
                            className="rounded border border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:bg-amber-950 dark:text-amber-200"
                            role="status"
                            data-testid="result-surface-truncation"
                        >
                            Part of this result was cut: a result row was stored truncated or
                            exceeded the 5&nbsp;MiB response limit. The cut text is not retrievable.
                        </div>
                    )}

                    {content ? (
                        <pre
                            // Monospace, pre-wrapped, scrollable, and selectable — a result is arbitrary
                            // text, so it must not reflow or clip. The fixed max height keeps the modal
                            // bounded; the body scrolls within it.
                            className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded border border-border-default bg-surface-secondary p-3 font-mono text-xs text-text-primary"
                            tabIndex={0}
                            data-testid="result-surface-content"
                        >
                            {content}
                        </pre>
                    ) : (
                        <div
                            className="py-6 text-text-secondary"
                            data-testid="result-surface-empty"
                        >
                            This execution produced no result content.
                        </div>
                    )}

                    {hasNextPage && (
                        <div className="flex items-center gap-3 text-sm">
                            <button
                                type="button"
                                className={btnSecondary}
                                onClick={() => fetchNextPage()}
                                disabled={isFetchingNextPage}
                                data-testid="result-surface-load-more"
                            >
                                {isFetchingNextPage ? "Loading…" : "Load more"}
                            </button>
                            <span className="text-text-secondary" aria-live="polite">
                                More result text remains.
                            </span>
                        </div>
                    )}
                </div>
            )}
        </Dialog>
    );
};

export default ExecutionResultSurface;
