/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionLogViewer from "./ExecutionLogViewer";

// ConfigEditor wraps Monaco, which does not render under jsdom. The reveal/selection inputs are
// surfaced as attributes so the tests can assert not just WHICH line the viewer targets but that it
// asks for the matched text to be SELECTED — locating a line without highlighting the hit was the
// original defect.
jest.mock("../components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value, startLine, startColumn, selectionLength }: any) => (
        <pre
            data-testid="editor"
            data-start-line={startLine ?? ""}
            data-start-column={startColumn ?? ""}
            data-selection-length={selectionLength ?? ""}
        >
            {value}
        </pre>
    ),
}));

jest.mock("../api/executions", () => ({
    getExecutionLogs: jest.fn(),
}));

describe("ExecutionLogViewer", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("labels the provenance of the returned text", async () => {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue([
            true,
            {
                mode: "full",
                events: [{ timestamp: 0, message: "hello" }],
                logsSource: "sfnHistory",
            },
        ]);

        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);

        // Stored-mode content can come from a live fallback, so the source must be stated.
        expect(
            await screen.findByText("Source: Execution history (Step Functions)")
        ).toBeInTheDocument();
    });

    it("points at Live mode when Stored returns nothing for a pipeline step", async () => {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue([
            true,
            { mode: "truncated", resultLog: "", errorLog: "", logsSource: "stored" },
        ]);

        render(
            <ExecutionLogViewer
                executionId="e1"
                pipelines={[{ pipelineExecutionId: "pe1", name: "step-one" }]}
            />
        );

        await userEvent.selectOptions(screen.getByLabelText("Log source"), "truncated");

        expect(await screen.findByText(/Switch Source to Live/)).toBeInTheDocument();
    });
});

/**
 * Find-in-log. The matching rules are covered in logSearch.test.ts; these cover the wiring that
 * only exists in the component — the counter, the stepping controls, and the line the editor is
 * told to reveal.
 */
describe("ExecutionLogViewer find-in-log", () => {
    const LOG_LINES = [
        "line one starts",
        "ERROR first failure",
        "line three",
        "error second, error third",
    ];

    async function renderWithLog() {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue([
            true,
            {
                mode: "full",
                // No timestamps, so extractLogText emits the messages verbatim and the line numbers
                // asserted below are the log's own.
                events: LOG_LINES.map((message) => ({ message })),
                logsSource: "live",
            },
        ]);
        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);
        return await screen.findByLabelText("Find in log");
    }

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("reports the total match count, including repeats on one line", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        // 1 on line 2 + 2 on line 4 — a per-line count would read "2 matches" and mislead.
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 3");
    });

    it("says so plainly when nothing matches", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "no-such-token");
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("No matches");
    });

    it("tells the editor to reveal the matched line, and moves it when stepping", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        // First match is on line 2; without this the viewer would count matches it never scrolls to.
        expect(screen.getByTestId("editor")).toHaveAttribute("data-start-line", "2");

        await userEvent.click(screen.getByLabelText("Next match"));
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("2 of 3");
        expect(screen.getByTestId("editor")).toHaveAttribute("data-start-line", "4");
    });

    it("asks for the matched TEXT to be selected, not just the line located", async () => {
        // Scrolling to a line without highlighting the hit is what made stepping look broken: on a
        // long line the operator could not see which occurrence was current.
        const input = await renderWithLog();
        await userEvent.type(input, "error");

        const editor = screen.getByTestId("editor");
        // "ERROR first failure" — the match starts at column 1.
        expect(editor).toHaveAttribute("data-start-column", "1");
        expect(editor).toHaveAttribute("data-selection-length", "5");
    });

    it("moves the selection COLUMN when stepping between two matches on one line", async () => {
        // Line 4 is "error second, error third": both matches share a line, so the line number alone
        // cannot distinguish them — only the column can.
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        const next = screen.getByLabelText("Next match");

        await userEvent.click(next); // 2 of 3 -> line 4, first occurrence
        const first = screen.getByTestId("editor").getAttribute("data-start-column");
        await userEvent.click(next); // 3 of 3 -> line 4, second occurrence
        const second = screen.getByTestId("editor").getAttribute("data-start-column");

        expect(screen.getByTestId("editor")).toHaveAttribute("data-start-line", "4");
        expect(Number(second)).toBeGreaterThan(Number(first));
    });

    it("tracks the selection length as the query grows", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "err");
        expect(screen.getByTestId("editor")).toHaveAttribute("data-selection-length", "3");
        await userEvent.type(input, "or");
        expect(screen.getByTestId("editor")).toHaveAttribute("data-selection-length", "5");
    });

    it("does not ask for a selection in filtered mode", async () => {
        // The filtered view rewrites the text with line-number prefixes, so the original columns no
        // longer address anything meaningful.
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        await userEvent.click(screen.getByLabelText("Only matching lines"));

        const editor = screen.getByTestId("editor");
        expect(editor).toHaveAttribute("data-start-line", "");
        expect(editor).toHaveAttribute("data-start-column", "");
        expect(editor).toHaveAttribute("data-selection-length", "");
    });

    it("wraps from the last match back to the first", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        const next = screen.getByLabelText("Next match");
        await userEvent.click(next);
        await userEvent.click(next);
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("3 of 3");
        await userEvent.click(next);
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 3");
    });

    it("steps backwards from the first match to the last", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        await userEvent.click(screen.getByLabelText("Previous match"));
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("3 of 3");
    });

    it("steps on Enter and back on Shift+Enter", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        await userEvent.type(input, "{Enter}");
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("2 of 3");
        await userEvent.type(input, "{Shift>}{Enter}{/Shift}");
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 3");
    });

    it("narrows to matches only when asked, keeping the original line numbers", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        await userEvent.click(screen.getByLabelText("Only matching lines"));

        const editor = screen.getByTestId("editor");
        expect(editor).toHaveTextContent("2: ERROR first failure");
        expect(editor).not.toHaveTextContent("line one starts");
    });

    it("restricts matches to the exact case when Match case is on", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "ERROR");
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 3");

        await userEvent.click(screen.getByLabelText("Match case"));
        // Only line 2 is uppercase.
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 1");
        expect(screen.getByTestId("editor")).toHaveAttribute("data-start-line", "2");
    });

    it("resets the cursor when the query narrows, so the counter cannot read past the end", async () => {
        const input = await renderWithLog();
        await userEvent.type(input, "error");
        await userEvent.click(screen.getByLabelText("Next match"));
        await userEvent.click(screen.getByLabelText("Next match"));
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("3 of 3");

        // Narrow to a single match while the index sits at 2 — a stale index would read "3 of 1".
        await userEvent.clear(input);
        await userEvent.type(input, "second");
        expect(screen.getByTestId("log-match-count")).toHaveTextContent("1 of 1");
    });

    it("disables the stepping controls when there is nothing to step through", async () => {
        const input = await renderWithLog();
        expect(screen.getByLabelText("Next match")).toBeDisabled();
        await userEvent.type(input, "error");
        expect(screen.getByLabelText("Next match")).toBeEnabled();
    });

    it("does not offer a search when there are no logs at all", async () => {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue([true, { mode: "full", events: [] }]);

        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);

        expect(await screen.findByText(/No log events found/)).toBeInTheDocument();
        expect(screen.queryByLabelText("Find in log")).not.toBeInTheDocument();
    });
});
