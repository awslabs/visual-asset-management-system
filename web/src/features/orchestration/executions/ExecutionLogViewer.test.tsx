/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionLogViewer, { extractLogText, groupByLogId } from "./ExecutionLogViewer";

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
 * Out-of-order responses. The default `full` source is a live CloudWatch search taking seconds while a
 * stored read returns at once, so the earlier request routinely lands last — and every write in
 * fetchLogs is unconditional, so it would repaint the log, the Source badge and the spinner for a
 * scope the controls no longer name.
 */
describe("ExecutionLogViewer superseded responses", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    const LIVE = [
        true,
        { mode: "full", events: [{ message: "LIVE TEXT" }], logsSource: "live" },
    ] as const;
    const STORED = [
        true,
        { mode: "truncated", executionLog: "STORED TEXT", logsSource: "stored" },
    ] as const;

    it("renders a live response that IS the current one", async () => {
        // Control for the test below: this payload does reach the editor when it is not superseded, so
        // its absence there is evidence of the guard rather than of a mock that renders nothing.
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue(LIVE);

        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);

        expect(await screen.findByTestId("editor")).toHaveTextContent("LIVE TEXT");
    });

    it("discards a slow earlier response instead of overwriting the newer scope's log", async () => {
        const { getExecutionLogs } = require("../api/executions");
        let landLiveResponse: (value: unknown) => void = () => undefined;
        getExecutionLogs
            .mockImplementationOnce(
                () =>
                    new Promise((resolve) => {
                        landLiveResponse = resolve;
                    })
            )
            .mockImplementationOnce(() => Promise.resolve(STORED));

        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);

        // Switch away while the live read is still in flight.
        await userEvent.selectOptions(screen.getByLabelText("Log source"), "truncated");
        expect(await screen.findByTestId("editor")).toHaveTextContent("STORED TEXT");
        expect(screen.getByText("Source: Stored")).toBeInTheDocument();

        // The live read now lands. It belongs to a selection the user has left.
        await act(async () => {
            landLiveResponse(LIVE);
        });
        await waitFor(() => expect(getExecutionLogs).toHaveBeenCalledTimes(2));

        expect(screen.getByTestId("editor")).toHaveTextContent("STORED TEXT");
        expect(screen.getByTestId("editor")).not.toHaveTextContent("LIVE TEXT");
        // The badge is taken from the payload, so a stale write mislabels the text as well as
        // replacing it.
        expect(screen.getByText("Source: Stored")).toBeInTheDocument();
        expect(screen.queryByText("Source: Live (CloudWatch)")).not.toBeInTheDocument();
    });

    it("does not clear the spinner for a request that is no longer current", async () => {
        const { getExecutionLogs } = require("../api/executions");
        let landFirst: (value: unknown) => void = () => undefined;
        getExecutionLogs
            .mockImplementationOnce(
                () =>
                    new Promise((resolve) => {
                        landFirst = resolve;
                    })
            )
            // The second request never settles, so the viewer must still be reported as loading.
            .mockImplementationOnce(() => new Promise(() => undefined));

        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);
        await userEvent.selectOptions(screen.getByLabelText("Log source"), "truncated");

        await act(async () => {
            landFirst(LIVE);
        });

        expect(screen.getByText("Loading logs…")).toBeInTheDocument();
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

/**
 * Narrowing a step's logs to one registered source. The `logId` parameter is accepted by the logs API
 * only in Live (full) mode with a pipeline scope, so the control exists only there, and a change of
 * scope or mode returns to all sources rather than carrying a source from another step.
 */
describe("ExecutionLogViewer log source", () => {
    const LOG = {
        kind: "registered",
        logStreamName: "",
        logStreamPrefix: "",
    };
    const PIPELINES = [
        {
            pipelineExecutionId: "pe1",
            name: "thumbnail",
            availableLogs: [
                {
                    ...LOG,
                    logId: "aaaa000000000001",
                    label: "3D thumbnail state machine",
                    sourceType: "stateMachine",
                    stageName: "",
                    logGroupName: "/aws/vendedlogs/states/x",
                },
                {
                    ...LOG,
                    logId: "aaaa000000000002",
                    label: "Preview3dThumbnailBatchJob container",
                    sourceType: "batch",
                    stageName: "Preview3dThumbnailBatchJob",
                    logGroupName: "/aws/batch/job",
                    logStreamPrefix: "jobdef/default/",
                },
            ],
        },
        { pipelineExecutionId: "pe2", name: "second", availableLogs: [] },
    ];
    const FULL = [true, { mode: "full", events: [{ message: "x" }], logsSource: "live" }] as const;

    beforeEach(() => {
        jest.clearAllMocks();
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue(FULL);
    });

    it("offers no source control for the whole execution", async () => {
        render(<ExecutionLogViewer executionId="e1" pipelines={PIPELINES} />);
        await screen.findByTestId("editor");
        expect(screen.queryByLabelText("Available log source")).not.toBeInTheDocument();
    });

    it("lists All sources plus each available log of the selected step, labelled by kind and stage", async () => {
        render(<ExecutionLogViewer executionId="e1" pipelines={PIPELINES} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");

        const select = screen.getByLabelText("Available log source") as HTMLSelectElement;
        const labels = Array.from(select.options).map((o) => o.textContent);
        expect(labels).toEqual([
            "All sources",
            "3D thumbnail state machine · stateMachine",
            "Preview3dThumbnailBatchJob container · batch · Preview3dThumbnailBatchJob",
        ]);
    });

    it("sends the chosen source as logId, alongside the pipeline scope and full mode", async () => {
        const { getExecutionLogs } = require("../api/executions");
        render(<ExecutionLogViewer executionId="e1" pipelines={PIPELINES} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");
        await userEvent.selectOptions(
            screen.getByLabelText("Available log source"),
            "aaaa000000000002"
        );

        await waitFor(() =>
            expect(getExecutionLogs).toHaveBeenLastCalledWith("e1", {
                mode: "full",
                pipelineExecutionId: "pe1",
                logId: "aaaa000000000002",
            })
        );
        // The select already narrows to one source; stage filtering is a CLI affordance the web
        // never sends.
        expect(getExecutionLogs.mock.calls.at(-1)[1]).not.toHaveProperty("stageName");
    });

    it("returns to all sources when the scope changes, so a source never crosses steps", async () => {
        const { getExecutionLogs } = require("../api/executions");
        render(<ExecutionLogViewer executionId="e1" pipelines={PIPELINES} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");
        await userEvent.selectOptions(
            screen.getByLabelText("Available log source"),
            "aaaa000000000001"
        );
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe2");

        await waitFor(() =>
            expect(getExecutionLogs).toHaveBeenLastCalledWith("e1", {
                mode: "full",
                pipelineExecutionId: "pe2",
            })
        );
        // No request in between carried the old source under the new scope.
        const crossed = getExecutionLogs.mock.calls.filter(
            ([, params]: any[]) => params.pipelineExecutionId === "pe2" && params.logId
        );
        expect(crossed).toHaveLength(0);
        // The second step registered no logs, so no source control is offered for it.
        expect(screen.queryByLabelText("Available log source")).not.toBeInTheDocument();
    });

    it("drops the source and the control in Stored mode, which the logs API does not narrow", async () => {
        const { getExecutionLogs } = require("../api/executions");
        render(<ExecutionLogViewer executionId="e1" pipelines={PIPELINES} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");
        await userEvent.selectOptions(
            screen.getByLabelText("Available log source"),
            "aaaa000000000001"
        );
        await userEvent.selectOptions(screen.getByLabelText("Log source"), "truncated");

        await waitFor(() =>
            expect(getExecutionLogs).toHaveBeenLastCalledWith("e1", {
                mode: "truncated",
                pipelineExecutionId: "pe1",
            })
        );
        expect(screen.queryByLabelText("Available log source")).not.toBeInTheDocument();
    });
});

/**
 * The Sources row. A Live read consults several logs per step and reports how each went; a source that
 * was denied, missing, empty or skipped is otherwise indistinguishable from one that simply had no
 * lines in the window.
 */
describe("ExecutionLogViewer sources row", () => {
    const SOURCE = {
        kind: "registered",
        sourceType: "batch",
        stageName: "Preview3dThumbnailBatchJob",
        logGroupName: "/aws/batch/job",
        logStreamName: "",
        logStreamPrefix: "jobdef/default/",
    };
    // The logs API reports sources only for a single step's Live read; the whole-execution read on
    // mount carries none, so every fixture here scopes to this step before expecting a row.
    const STEP = [{ pipelineExecutionId: "pe1", name: "thumbnail" }];
    const WHOLE = [true, { mode: "full", events: [{ message: "x" }], logsSource: "live" }] as const;

    beforeEach(() => jest.clearAllMocks());

    it("renders one chip per source with the outcome of reading it", async () => {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValueOnce(WHOLE).mockResolvedValue([
            true,
            {
                mode: "full",
                events: [{ message: "x" }],
                logsSource: "live",
                logSources: [
                    { ...SOURCE, logId: "a1", label: "container", status: "read", eventCount: 12 },
                    {
                        ...SOURCE,
                        logId: "a2",
                        label: "state machine",
                        sourceType: "stateMachine",
                        status: "denied",
                    },
                    { ...SOURCE, logId: "a3", label: "invocation", status: "notFound" },
                    { ...SOURCE, logId: "a4", label: "quiet", status: "empty" },
                    { ...SOURCE, logId: "a5", label: "late", status: "skipped" },
                    { ...SOURCE, logId: "a6", label: "prefix only", status: "unscoped" },
                    { ...SOURCE, logId: "a7", label: "throttled", status: "error" },
                ],
            },
        ]);
        render(<ExecutionLogViewer executionId="e1" pipelines={STEP} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");

        const list = await screen.findByRole("list", { name: "Log sources read" });
        const items = within(list).getAllByRole("listitem");
        expect(items.map((li) => li.textContent)).toEqual([
            "container · read 12",
            "state machine · denied",
            "invocation · not found",
            "quiet · empty",
            "late · skipped",
            "prefix only · unscoped",
            "throttled · error",
        ]);
        // A read that failed for another reason (throttling, a bad token, an unparseable location)
        // is neither a permission problem nor a missing group, so its chip is toned apart from both
        // and from a read that went through.
        const byLabel = (label: string) =>
            items.find((li) => li.textContent?.startsWith(label)) as HTMLElement;
        expect(byLabel("throttled").className).not.toBe(byLabel("state machine").className);
        expect(byLabel("throttled").className).not.toBe(byLabel("invocation").className);
        expect(byLabel("throttled").className).not.toBe(byLabel("container").className);
        expect(byLabel("throttled").className).not.toBe(byLabel("quiet").className);
    });

    it("shows no sources row for a Stored read, which reports none", async () => {
        const { getExecutionLogs } = require("../api/executions");
        getExecutionLogs.mockResolvedValue([
            true,
            { mode: "truncated", executionLog: "STORED", logsSource: "stored" },
        ]);
        render(<ExecutionLogViewer executionId="e1" pipelines={[]} />);
        await screen.findByTestId("editor");
        expect(screen.queryByTestId("log-sources")).not.toBeInTheDocument();
    });

    it("clears the previous read's sources while the next one loads", async () => {
        const { getExecutionLogs } = require("../api/executions");
        // Mount (whole execution) → the step's read with sources → a Refresh that never resolves.
        getExecutionLogs
            .mockResolvedValueOnce(WHOLE)
            .mockResolvedValueOnce([
                true,
                {
                    mode: "full",
                    events: [{ message: "x" }],
                    logsSource: "live",
                    logSources: [
                        {
                            ...SOURCE,
                            logId: "a1",
                            label: "container",
                            status: "read",
                            eventCount: 1,
                        },
                    ],
                },
            ])
            .mockImplementationOnce(() => new Promise(() => undefined));
        render(<ExecutionLogViewer executionId="e1" pipelines={STEP} />);
        await userEvent.selectOptions(screen.getByLabelText("Log scope"), "pe1");
        await screen.findByTestId("log-sources");

        await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

        expect(screen.queryByTestId("log-sources")).not.toBeInTheDocument();
    });
});

/**
 * Sub-process lines come from several sources — a step's state machine, each container job — and are
 * already in timestamp order from the server. They are grouped under a heading per source so a reader
 * can tell a container's stdout from the state machine's transitions.
 */
describe("extractLogText sub-process grouping", () => {
    const at = (ts: number, message: string, logId?: string) => ({ timestamp: ts, message, logId });

    it("groups sub-process events under a heading per source, in order of first appearance", () => {
        const text = extractLogText({
            events: [at(1000, "main")],
            subProcessEvents: [
                at(2000, "sm one", "s1"),
                at(3000, "job one", "b1"),
                at(4000, "sm two", "s1"),
            ],
            logSources: [
                { logId: "s1", label: "3D thumbnail state machine", status: "read", eventCount: 2 },
                {
                    logId: "b1",
                    label: "Preview3dThumbnailBatchJob container",
                    status: "read",
                    eventCount: 1,
                },
            ],
        });
        const lines = text.split("\n");
        const headings = lines.filter((l) => l.startsWith("──── sub-process logs"));
        expect(headings).toEqual([
            "──── sub-process logs: 3D thumbnail state machine ────",
            "──── sub-process logs: Preview3dThumbnailBatchJob container ────",
        ]);
        // Both state-machine lines sit under their heading, and the job line under its own.
        const line = (suffix: string) => lines.findIndex((l) => l.endsWith(suffix));
        expect(lines.indexOf(headings[0])).toBeLessThan(line("sm one"));
        expect(line("sm two")).toBeLessThan(lines.indexOf(headings[1]));
        expect(lines.indexOf(headings[1])).toBeLessThan(line("job one"));
    });

    it("keeps the plain heading for events that carry no logId", () => {
        const text = extractLogText({
            events: [],
            subProcessEvents: [{ timestamp: 1000, message: "legacy line" }],
        });
        expect(text).toContain("──── sub-process logs ────");
        expect(text).not.toContain("sub-process logs:");
    });

    it("falls back to the logId when the response names no source for it", () => {
        const text = extractLogText({
            events: [],
            subProcessEvents: [{ timestamp: 1000, message: "line", logId: "deadbeef00000000" }],
        });
        expect(text).toContain("──── sub-process logs: deadbeef00000000 ────");
    });
});

describe("groupByLogId", () => {
    it("returns one group per logId in first-appearance order, keeping each group's event order", () => {
        const groups = groupByLogId([
            { logId: "b", message: "1" },
            { logId: "a", message: "2" },
            { logId: "b", message: "3" },
            { message: "4" },
        ]);
        expect(groups.map((g) => g.logId)).toEqual(["b", "a", ""]);
        expect(groups[0].events.map((e) => e.message)).toEqual(["1", "3"]);
        expect(groups[2].events.map((e) => e.message)).toEqual(["4"]);
    });
});
