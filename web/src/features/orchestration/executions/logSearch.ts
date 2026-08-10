/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Find-in-log support for the execution log viewer.
 *
 * Pure functions over the already-fetched log text: the search is entirely local, so it costs no
 * extra CloudWatch reads and works identically on stored, live, and state-machine-history text.
 *
 * Kept out of the component so the matching rules — which are the part that can be subtly wrong —
 * are testable without rendering an editor.
 */

export interface LogMatch {
    /** 1-based line number, matching what the editor's gutter shows. */
    line: number;
    /** 1-based column of the match start on that line. */
    column: number;
    /** The full text of the matching line, for the result list. */
    text: string;
}

/**
 * Every occurrence of `query` in `text`, in document order.
 *
 * Case-insensitive by default: a log is machine-generated and an operator looking for "error" should
 * not have to guess whether the writer used "Error" or "ERROR".
 *
 * Multiple matches on ONE line are each reported, because a repeated token on a long line is exactly
 * what someone counting occurrences is looking for — collapsing to one-per-line would undercount.
 */
export function findMatches(text: string, query: string, caseSensitive = false): LogMatch[] {
    if (!text || !query) return [];
    const needle = caseSensitive ? query : query.toLowerCase();
    const matches: LogMatch[] = [];
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i += 1) {
        const raw = lines[i];
        const haystack = caseSensitive ? raw : raw.toLowerCase();
        let from = 0;
        for (;;) {
            const at = haystack.indexOf(needle, from);
            if (at === -1) break;
            matches.push({ line: i + 1, column: at + 1, text: raw });
            // Advance past this match so overlapping starts are not double-counted, while a
            // repeated token later on the same line still registers.
            from = at + needle.length;
        }
    }
    return matches;
}

/**
 * Wrap-around index step. Returns 0 for an empty match set so callers need no special case.
 *
 * Wrapping rather than clamping: an operator stepping through occurrences expects to cycle, and
 * silently stopping at the last match reads as "no more matches" when there are earlier ones.
 */
export function stepIndex(current: number, total: number, delta: number): number {
    if (total <= 0) return 0;
    return (((current + delta) % total) + total) % total;
}

/** Only the lines that contain a match, deduplicated, in document order. */
export function matchingLines(matches: LogMatch[]): LogMatch[] {
    const seen = new Set<number>();
    const out: LogMatch[] = [];
    for (const m of matches) {
        if (seen.has(m.line)) continue;
        seen.add(m.line);
        out.push(m);
    }
    return out;
}

/**
 * `text` reduced to only the lines containing a match.
 *
 * This is the "filter" half of the ask: on a log of thousands of lines, seeing just the hits is
 * often more useful than jumping between them. The original line number is prefixed so a filtered
 * view can still be related back to the full log.
 */
export function filterToMatches(text: string, query: string, caseSensitive = false): string {
    const lines = matchingLines(findMatches(text, query, caseSensitive));
    if (!lines.length) return "";
    const width = String(lines[lines.length - 1].line).length;
    return lines.map((m) => `${String(m.line).padStart(width, " ")}: ${m.text}`).join("\n");
}
