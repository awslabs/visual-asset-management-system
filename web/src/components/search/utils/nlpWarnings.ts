/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Synonyms from "../../../synonyms";
import type { NlpSearchWarning } from "../types";

/**
 * Natural-language warnings that only say a search window filled. The page states them itself (the
 * count's "+" and the line under the results), so they raise no toast; every other code is rare and
 * is toasted with the text nlpWarningText gives it.
 */
export const NLP_WINDOW_WARNING_CODES = new Set(["truncated:window", "segments:window_full"]);

/**
 * What the search page tells the user for each rare warning code. The API's `message` is written for
 * API and CLI callers, so the page shows this text instead. Each text is generic: none names a database,
 * a field or a number from the response, so it can't say whether a database the user named exists.
 */
const RARE_WARNING_TEXT: Record<string, () => string> = {
    "databases:none_accessible": () =>
        `You don't have access to any ${Synonyms.database} this search covers. Ask your administrator for access.`,
    "opensearch:enrichment_failed": () =>
        "Some result details, such as dates and metadata, couldn't be loaded, and metadata, tag and location filters weren't applied. The matches are still shown.",
    "opensearch:fields_ignored": () =>
        "Some filters aren't available for natural-language search in this deployment and weren't applied.",
    "truncated:targets": () =>
        `Not every ${Synonyms.database} you can access was searched. Choose a ${Synonyms.database} in the filters to search it fully.`,
};

/** The text the page shows for one warning; a code it does not know shows the API's message. */
export function nlpWarningText(warning: NlpSearchWarning): string {
    const text = RARE_WARNING_TEXT[warning.code];
    return text ? text() : (warning.message ?? "").trim();
}

/**
 * The text of the "Search notice" toast for a natural-language answer: each rare warning's text, in
 * the order the API sent them, once each. Empty when there is nothing to toast (no rare warning, or
 * only unknown codes without a message).
 */
export function rareNlpWarningText(warnings: NlpSearchWarning[] | null | undefined): string {
    const texts = (warnings ?? [])
        .filter((w) => !NLP_WINDOW_WARNING_CODES.has(w.code))
        .map(nlpWarningText)
        .filter((text) => text.length > 0);
    return Array.from(new Set(texts)).join(" ");
}
