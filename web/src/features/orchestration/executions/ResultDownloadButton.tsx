/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";

/** File name for a downloaded result: the execution id, made safe for a file system. */
export function resultFileName(executionId: string): string {
    const safe = (executionId || "execution").replace(/[^A-Za-z0-9._-]/g, "_");
    return `execution-${safe}-result.txt`;
}

/**
 * Save result text as a UTF-8 `.txt` file through a Blob, so a result of any size reaches the reader
 * without being rendered on the page.
 */
export function saveResultText(executionId: string, text: string): void {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = resultFileName(executionId);
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // Release the object URL after the click has been dispatched.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

interface ResultDownloadButtonProps {
    executionId: string;
    /** Returns the whole result text, fetching any pages not yet loaded. */
    readFullText: () => Promise<string>;
    className: string;
    testId: string;
}

/**
 * "Download" for a result: reads every remaining page and saves the full text as a `.txt` file,
 * without rendering it — the route to a result too large to read comfortably on the page.
 */
export const ResultDownloadButton: React.FC<ResultDownloadButtonProps> = ({
    executionId,
    readFullText,
    className,
    testId,
}) => {
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState(false);
    const onDownload = async () => {
        setBusy(true);
        setFailed(false);
        try {
            saveResultText(executionId, await readFullText());
        } catch {
            setFailed(true);
        } finally {
            setBusy(false);
        }
    };
    return (
        <>
            <button
                type="button"
                className={className}
                onClick={onDownload}
                disabled={busy}
                data-testid={testId}
            >
                {busy ? "Downloading…" : "Download"}
            </button>
            {failed && (
                <span role="alert" className="text-sm text-vams-error">
                    The download failed.
                </span>
            )}
        </>
    );
};
