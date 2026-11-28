/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Profiler } from "react";
import { render, screen } from "@testing-library/react";
import ResultText, { ResultChunk, RESULT_CHUNK_CHARS, splitResultText } from "./ResultText";

describe("splitResultText", () => {
    it("returns one chunk for text within the limit and none for empty text", () => {
        expect(splitResultText("short")).toEqual(["short"]);
        expect(splitResultText("")).toEqual([]);
    });

    it("cuts an unbroken run at the limit and loses nothing", () => {
        // CJK with no line breaks — the D13 shape.
        const text = "漢".repeat(RESULT_CHUNK_CHARS * 2 + 10);
        const chunks = splitResultText(text);
        expect(chunks).toHaveLength(3);
        chunks.forEach((chunk) => expect(chunk.length).toBeLessThanOrEqual(RESULT_CHUNK_CHARS));
        expect(chunks.join("")).toBe(text);
    });

    it("ends a chunk at the last line break inside the window", () => {
        const text = "aaaa\nbbbb\ncccc";
        const chunks = splitResultText(text, 8);
        expect(chunks).toEqual(["aaaa\n", "bbbb\n", "cccc"]);
    });

    it("never separates a surrogate pair", () => {
        // Each emoji is two UTF-16 code units; an odd limit would land between them.
        const text = "😀".repeat(10);
        const chunks = splitResultText(text, 5);
        chunks.forEach((chunk) => {
            const last = chunk.charCodeAt(chunk.length - 1);
            expect(last >= 0xd800 && last <= 0xdbff).toBe(false);
        });
        expect(chunks.join("")).toBe(text);
    });
});

describe("ResultText", () => {
    it("renders each page's chunks in order", () => {
        render(<ResultText pages={["first-", "second"]} testId="rt" />);
        expect(screen.getByTestId("rt")).toHaveTextContent("first-second");
        expect(screen.getAllByTestId("result-text-chunk")).toHaveLength(2);
    });

    it("appends a second page without re-rendering or touching the first chunk", () => {
        const first = "漢".repeat(RESULT_CHUNK_CHARS); // exactly one full chunk
        const commits: string[] = [];
        const onRender = (_id: string, phase: string) => commits.push(phase);
        const { rerender } = render(
            <Profiler id="rt" onRender={onRender}>
                <ResultText pages={[first]} testId="rt" />
            </Profiler>
        );
        const firstChunk = screen.getAllByTestId("result-text-chunk")[0];
        const firstTextNode = firstChunk.firstChild;

        const mutations: MutationRecord[] = [];
        const observer = new MutationObserver((records) => mutations.push(...records));
        observer.observe(firstChunk, { childList: true, characterData: true, subtree: true });

        // A new pages array, with the first page's text equal but a distinct string instance, as a
        // re-split would produce — the chunk must still be skipped.
        rerender(
            <Profiler id="rt" onRender={onRender}>
                <ResultText pages={[first.slice(0), "second page"]} testId="rt" />
            </Profiler>
        );
        const records = [...mutations, ...observer.takeRecords()];
        observer.disconnect();

        const chunks = screen.getAllByTestId("result-text-chunk");
        expect(chunks).toHaveLength(2);
        // Same element, same text node, and no DOM mutation inside it.
        expect(chunks[0]).toBe(firstChunk);
        expect(chunks[0].firstChild).toBe(firstTextNode);
        expect(records).toHaveLength(0);
        expect(chunks[1]).toHaveTextContent("second page");
        expect(commits).toEqual(["mount", "update"]);
    });

    it("memoizes the chunk so an unchanged chunk's render is skipped", () => {
        // React.memo skips a chunk whose text is equal; together with the stable keys above this is
        // what keeps a page append from re-rendering text already shown.
        expect((ResultChunk as any).$$typeof).toBe(Symbol.for("react.memo"));
    });
});
