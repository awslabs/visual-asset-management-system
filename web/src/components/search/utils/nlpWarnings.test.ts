/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * D76: the search page shows its own text for each rare natural-language warning code, never the
 * API's message, which is written for API and CLI callers. A code it does not know shows the message.
 */
import {
    NLP_WINDOW_WARNING_CODES,
    nlpWarningText,
    rareNlpWarningText,
    rareNlpWarningTexts,
} from "./nlpWarnings";

const API_MESSAGES: Record<string, string> = {
    "databases:none_accessible": "No searchable database is accessible to the caller",
    "opensearch:enrichment_failed": "OpenSearch enrichment failed; hits are returned without it",
    "opensearch:fields_ignored": "OpenSearch is not enabled; ignored: filters, tags",
    "truncated:targets": "Only the first 200 accessible databases were searched",
};

const warning = (code: string) => ({ code, message: API_MESSAGES[code] ?? "" });

describe("nlpWarningText", () => {
    it.each([
        [
            "databases:none_accessible",
            "You don't have access to any database this search covers. Ask your administrator for access.",
        ],
        [
            "opensearch:enrichment_failed",
            "Some result details, such as dates and metadata, couldn't be loaded, and metadata, tag and location filters weren't applied. The matches are still shown.",
        ],
        [
            "opensearch:fields_ignored",
            "Some filters aren't available for natural-language search in this deployment and weren't applied.",
        ],
        [
            "truncated:targets",
            "Not every database you can access was searched. Choose a database in the filters to search it fully.",
        ],
    ])("gives %s its own text", (code, text) => {
        expect(nlpWarningText(warning(code))).toBe(text);
    });

    it("never shows the API's wording, a field list or a number for a known code", () => {
        for (const code of Object.keys(API_MESSAGES)) {
            const text = nlpWarningText(warning(code));
            expect(text).not.toBe(API_MESSAGES[code]);
            expect(text).not.toMatch(/caller|OpenSearch|ignored:|\d/);
        }
    });

    it("falls back to the API's message for a code it does not know", () => {
        expect(nlpWarningText({ code: "future:code", message: "Something new happened" })).toBe(
            "Something new happened"
        );
        expect(nlpWarningText({ code: "future:code", message: "" })).toBe("");
    });
});

describe("rareNlpWarningText", () => {
    it("joins the rare warnings' texts in the API's order and leaves the window-full codes out", () => {
        const text = rareNlpWarningText([
            {
                code: "truncated:window",
                message: "A result window of 100 filled; more files may match",
            },
            warning("opensearch:fields_ignored"),
            { code: "segments:window_full", message: "A segment window of 100 filled" },
            warning("databases:none_accessible"),
        ]);
        expect(text).toBe(
            `${nlpWarningText(warning("opensearch:fields_ignored"))} ${nlpWarningText(
                warning("databases:none_accessible")
            )}`
        );
        expect(text).not.toMatch(/window|caller/);
    });

    it("is empty when only window-full codes or message-less unknown codes arrive", () => {
        expect(rareNlpWarningText([])).toBe("");
        expect(rareNlpWarningText(undefined)).toBe("");
        expect(
            rareNlpWarningText(
                Array.from(NLP_WINDOW_WARNING_CODES).map((code) => ({ code, message: "m" }))
            )
        ).toBe("");
        expect(rareNlpWarningText([{ code: "future:code", message: "  " }])).toBe("");
    });

    it("says a repeated code once", () => {
        expect(
            rareNlpWarningText([warning("truncated:targets"), warning("truncated:targets")])
        ).toBe(nlpWarningText(warning("truncated:targets")));
    });
});

describe("rareNlpWarningTexts", () => {
    it("lists the Search notice's lines: each rare warning's text once, in the API's order, without the window-full codes (D77)", () => {
        expect(
            rareNlpWarningTexts([
                warning("databases:none_accessible"),
                { code: "truncated:window", message: "A result window of 100 filled" },
                { code: "future:code", message: "A new kind of notice" },
                warning("databases:none_accessible"),
                { code: "segments:window_full", message: "A segment window of 100 filled" },
            ])
        ).toEqual([nlpWarningText(warning("databases:none_accessible")), "A new kind of notice"]);
        expect(rareNlpWarningTexts(null)).toEqual([]);
        expect(rareNlpWarningTexts([{ code: "future:code", message: "" }])).toEqual([]);
    });
});
