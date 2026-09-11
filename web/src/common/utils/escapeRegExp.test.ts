/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { escapeRegExp } from "./escapeRegExp";

/**
 * S5-WEB-001: the table's text filter compiled raw filter text into a RegExp inside a cell
 * renderer. The failure is not "the filter matches nothing" — `new RegExp("(")` THROWS, and a throw
 * during render blanks the whole page. Every character below is ordinary to type into a search box.
 */
describe("escapeRegExp", () => {
    // Characters that make an UNESCAPED pattern throw rather than simply not match. `{` is
    // deliberately absent: V8 accepts a lone `{` as a literal under Annex B web compatibility, so it
    // is a metacharacter that must still be escaped for correct MATCHING but is not a crash vector.
    const THROWS_UNESCAPED = ["(", "[", "*", "+", "?", "\\", "(?<"];
    // Every metacharacter, crash vector or not — all must match literally once escaped.
    const METACHARACTERS = [...THROWS_UNESCAPED, "{", "}", ")", "]", ".", "^", "$", "|"];

    it.each(THROWS_UNESCAPED)("makes %p a constructible pattern", (input) => {
        // Positive control: unescaped really does throw, so the assertion below is not vacuous.
        expect(() => new RegExp(input, "ig")).toThrow();
        expect(() => new RegExp(escapeRegExp(input), "ig")).not.toThrow();
    });

    it.each(METACHARACTERS)("matches %p literally once escaped", (input) => {
        const haystack = `before${input}after`;
        expect(new RegExp(escapeRegExp(input), "ig").test(haystack)).toBe(true);
    });

    it("does not match a metacharacter's wildcard meaning", () => {
        // "." escaped must not match an arbitrary character, or highlighting would mark text the user
        // did not search for.
        expect(new RegExp(escapeRegExp("a.c")).test("abc")).toBe(false);
        expect(new RegExp(escapeRegExp("a.c")).test("a.c")).toBe(true);
    });

    it("leaves ordinary text unchanged", () => {
        expect(escapeRegExp("BoomBox glb")).toBe("BoomBox glb");
        expect(escapeRegExp("")).toBe("");
    });

    it("survives a string of only metacharacters", () => {
        const nasty = "([{*+?^$|\\.})]";
        expect(() => new RegExp(escapeRegExp(nasty), "ig")).not.toThrow();
        expect(new RegExp(escapeRegExp(nasty), "ig").test(`x${nasty}y`)).toBe(true);
    });
});
