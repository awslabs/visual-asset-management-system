/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Text from an audit record is untrusted: it holds what users and clients sent. The page renders it
 * as React text nodes only (never as HTML) and, as a second line after the server's own output
 * encoding, shows control, bidi and zero-width characters as visible `\uXXXX` so a record cannot
 * reorder or hide what a reviewer reads.
 */

// C0 and C1 controls (tab and newline included: a record value is one line), bidi controls,
// zero-width characters, the line and paragraph separators, and the byte-order mark.
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARS = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g;

const escapeChar = (char: string): string =>
    `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`;

/** `value` as one line of safe text. Not text (null, undefined) gives the placeholder. */
export const visibleText = (value: unknown, placeholder = "-"): string => {
    if (value === null || value === undefined || value === "") return placeholder;
    const text = typeof value === "string" ? value : String(value);
    return text.replace(UNSAFE_CHARS, escapeChar);
};

/** Upper bound on one string shown in the table or the detail panel, before an ellipsis. */
export const MAX_SHOWN_CHARS = 2048;

export const clipText = (text: string, max: number = MAX_SHOWN_CHARS): string =>
    text.length > max ? `${text.slice(0, max)}…` : text;

/** The marker the server leaves where it cut a long value. */
export interface TruncatedMarker {
    truncated: true;
    sha256?: string;
    bytes?: number;
    preview?: string;
}

export const isTruncatedMarker = (value: unknown): value is TruncatedMarker =>
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    (value as { truncated?: unknown }).truncated === true &&
    ("sha256" in value || "preview" in value || "bytes" in value);

/** Whether `value` holds anything worth a section of the detail panel. */
export const hasContent = (value: unknown): boolean => {
    if (value === null || value === undefined || value === "") return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value as object).length > 0;
    return true;
};
