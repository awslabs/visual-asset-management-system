/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Find-in-log matching rules. Tested directly rather than through the viewer because the subtle
 * parts — repeated matches on one line, wrap-around, case handling — are where a search silently
 * undercounts, and an operator counting occurrences of "error" acts on that number.
 */

import { findMatches, stepIndex, matchingLines, filterToMatches } from "./logSearch";

const LOG = [
    "INFO starting run",
    "ERROR failed to open file",
    "INFO retrying",
    "error again, error twice on one line",
    "INFO done",
].join("\n");

describe("findMatches", () => {
    it("finds matches case-insensitively by default", () => {
        // A log is machine-generated; an operator should not have to guess ERROR vs Error vs error.
        const m = findMatches(LOG, "error");
        expect(m.map((x) => x.line)).toEqual([2, 4, 4]);
    });

    it("reports EVERY occurrence on a line, not one per line", () => {
        // Line 4 contains "error" twice; collapsing to one would undercount.
        const m = findMatches(LOG, "error");
        expect(m.filter((x) => x.line === 4)).toHaveLength(2);
    });

    it("reports 1-based line and column so they match the editor gutter", () => {
        const m = findMatches("abc\nxxErrorxx", "error");
        expect(m).toEqual([{ line: 2, column: 3, text: "xxErrorxx" }]);
    });

    it("honors case sensitivity when asked", () => {
        const m = findMatches(LOG, "ERROR", true);
        expect(m.map((x) => x.line)).toEqual([2]);
    });

    it("returns nothing for an empty query rather than matching everything", () => {
        expect(findMatches(LOG, "")).toEqual([]);
    });

    it("returns nothing for empty text", () => {
        expect(findMatches("", "error")).toEqual([]);
    });

    it("does not double-count overlapping starts", () => {
        // "aaaa" contains "aa" at offsets 0 and 2 when advancing past each match — not 3 times.
        expect(findMatches("aaaa", "aa")).toHaveLength(2);
    });

    it("matches text containing regex metacharacters literally", () => {
        // The query is a plain substring, not a pattern: "[" must not throw or be interpreted.
        const m = findMatches("a [warn] b\nc", "[warn]");
        expect(m).toHaveLength(1);
        expect(m[0].line).toBe(1);
    });
});

describe("stepIndex", () => {
    it("advances forward", () => {
        expect(stepIndex(0, 3, 1)).toBe(1);
    });

    it("wraps past the end rather than stopping", () => {
        // Clamping at the last match reads as "no more matches" when earlier ones exist.
        expect(stepIndex(2, 3, 1)).toBe(0);
    });

    it("wraps backwards from the first match", () => {
        expect(stepIndex(0, 3, -1)).toBe(2);
    });

    it("returns 0 when there are no matches", () => {
        expect(stepIndex(5, 0, 1)).toBe(0);
    });
});

describe("matchingLines", () => {
    it("deduplicates multiple matches on the same line", () => {
        const lines = matchingLines(findMatches(LOG, "error"));
        expect(lines.map((l) => l.line)).toEqual([2, 4]);
    });
});

describe("filterToMatches", () => {
    it("keeps only matching lines, prefixed with the original line number", () => {
        const out = filterToMatches(LOG, "error");
        // The original numbers are what let a filtered view be related back to the full log.
        expect(out).toContain("2: ERROR failed to open file");
        expect(out).toContain("4: error again");
        expect(out).not.toContain("starting run");
    });

    it("returns empty text when nothing matches", () => {
        expect(filterToMatches(LOG, "no-such-token")).toBe("");
    });

    it("right-aligns line numbers so the output stays readable", () => {
        const many = Array.from({ length: 12 }, (_, i) => (i === 0 || i === 11 ? "hit" : "x")).join(
            "\n"
        );
        const out = filterToMatches(many, "hit").split("\n");
        // Line 1 is padded to the width of line 12.
        expect(out[0]).toBe(" 1: hit");
        expect(out[1]).toBe("12: hit");
    });
});
