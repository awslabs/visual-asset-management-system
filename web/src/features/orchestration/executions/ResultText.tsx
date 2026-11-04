/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo } from "react";

/**
 * Upper bound, in UTF-16 code units, of one display chunk. A result page can carry up to 5 MiB of
 * text; painting that as one text node makes the browser line-break the whole run on every change
 * (minutes for unbroken CJK text). Bounded chunks keep each text node small.
 */
export const RESULT_CHUNK_CHARS = 64 * 1024;

/**
 * Split one page of result text into display chunks of at most `limit` code units. A chunk ends at
 * the last line break inside the window when there is one, so ordinary multi-line text shows no
 * extra breaks; an unbroken run is cut at the limit, never inside a surrogate pair. Concatenating
 * the chunks gives back the page exactly.
 */
export function splitResultText(text: string, limit: number = RESULT_CHUNK_CHARS): string[] {
    if (!text) return [];
    if (text.length <= limit) return [text];
    const chunks: string[] = [];
    let start = 0;
    while (start < text.length) {
        let end = Math.min(start + limit, text.length);
        if (end < text.length) {
            const newline = text.lastIndexOf("\n", end - 1);
            if (newline >= start) {
                end = newline + 1;
            } else {
                // Do not separate a high surrogate from its low surrogate.
                const code = text.charCodeAt(end - 1);
                if (code >= 0xd800 && code <= 0xdbff) end -= 1;
            }
        }
        chunks.push(text.slice(start, end));
        start = end;
    }
    return chunks;
}

/**
 * One chunk. Memoized on its text so appending a page never re-renders a chunk already shown, and
 * `content-visibility: auto` lets the browser skip laying out chunks scrolled out of view. Long
 * unbroken lines wrap anywhere instead of forcing a horizontal layout pass.
 */
export const ResultChunk = React.memo(function ResultChunk({ text }: { text: string }) {
    return (
        <div
            className="whitespace-pre-wrap"
            style={{
                overflowWrap: "anywhere",
                contentVisibility: "auto",
                containIntrinsicSize: "auto 20rem",
            }}
            data-testid="result-text-chunk"
        >
            {text}
        </div>
    );
});

interface ResultTextProps {
    /** The loaded pages' text, in order. A new page is appended; earlier entries stay as they are. */
    pages: string[];
    className?: string;
    testId?: string;
}

/**
 * Result text rendered page by page and chunk by chunk, so a page arriving appends elements rather
 * than replacing one ever-growing string. Keys are page and chunk positions, which never change for
 * text already shown.
 */
const ResultText: React.FC<ResultTextProps> = ({ pages, className, testId }) => {
    const chunked = useMemo(() => pages.map((page) => splitResultText(page)), [pages]);
    return (
        <div
            className={className}
            // Focusable so a keyboard user can scroll the result; labelled as the region it is.
            tabIndex={0}
            role="region"
            aria-label="Execution result text"
            data-testid={testId}
        >
            {chunked.map((chunks, pageIndex) =>
                chunks.map((text, chunkIndex) => (
                    <ResultChunk key={`${pageIndex}:${chunkIndex}`} text={text} />
                ))
            )}
        </div>
    );
};

export default ResultText;
