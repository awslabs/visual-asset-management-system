/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import StageTimeline, { durationBetween } from "./StageTimeline";
import type { SubExecutionStage } from "../types";

const stage = (over: Partial<SubExecutionStage>): SubExecutionStage => ({
    stageName: "Stage",
    stateType: "Task",
    status: "SUCCEEDED",
    ...over,
});

describe("StageTimeline", () => {
    it("lists every stage in the order given, with its state type and status", () => {
        render(
            <StageTimeline
                stages={[
                    stage({ stageName: "PipelineStartTask", status: "SUCCEEDED" }),
                    stage({ stageName: "Preview3dThumbnailBatchJob", status: "RUNNING" }),
                    stage({ stageName: "PipelineEndTask", status: "NOT_STARTED" }),
                ]}
            />
        );
        const list = screen.getByRole("list", { name: "Stage timeline" });
        const items = within(list).getAllByRole("listitem");
        expect(items.map((li) => li.textContent)).toEqual([
            expect.stringContaining("PipelineStartTask"),
            expect.stringContaining("Preview3dThumbnailBatchJob"),
            expect.stringContaining("PipelineEndTask"),
        ]);
        expect(items[1]).toHaveTextContent("Running");
        // A stage the run never reached reads as text, not as the raw enum value.
        expect(items[2]).toHaveTextContent("Not started");
        expect(items[2]).not.toHaveTextContent("NOT_STARTED");
        expect(screen.getAllByText("Task")).toHaveLength(3);
    });

    it("marks a caught failure as failed AND caught, with the recorded error", () => {
        render(
            <StageTimeline
                stages={[
                    stage({
                        stageName: "BatchJob",
                        status: "FAILED",
                        caught: true,
                        error: "States.TaskFailed",
                        cause: "Essential container in task exited",
                    }),
                ]}
            />
        );
        const row = screen.getByRole("listitem");
        expect(row).toHaveTextContent("Failed");
        expect(row).toHaveTextContent("caught");
        expect(row).toHaveTextContent("States.TaskFailed: Essential container in task exited");
    });

    it("shows duration, retries, Map iterations and the Batch job id when present", () => {
        render(
            <StageTimeline
                stages={[
                    stage({
                        stageName: "Retrying",
                        startDate: "2026-09-11T10:00:00Z",
                        stopDate: "2026-09-11T10:02:30Z",
                        attempts: 3,
                    }),
                    stage({
                        stageName: "Fan-out",
                        stateType: "Map",
                        iterations: { started: 4, succeeded: 3, failed: 1, aborted: 0 },
                    }),
                    stage({
                        stageName: "Job",
                        batch: { jobId: "1234abcd", logStreamName: "def/default/xyz" },
                    }),
                ]}
            />
        );
        const rows = screen.getAllByRole("listitem");
        expect(rows[0]).toHaveTextContent("2m 30s");
        expect(rows[0]).toHaveTextContent("3 attempts");
        expect(rows[1]).toHaveTextContent("iterations: 4 started, 3 succeeded, 1 failed");
        expect(rows[1]).not.toHaveTextContent("aborted");
        expect(rows[2]).toHaveTextContent("job 1234abcd");
        // The stream name is a log locator, not a stage fact; it is read through the Logs tab.
        expect(rows[2]).not.toHaveTextContent("def/default/xyz");
    });

    it("omits retries for a single attempt and the duration for a stage still running", () => {
        render(
            <StageTimeline
                stages={[
                    stage({
                        stageName: "Once",
                        status: "RUNNING",
                        attempts: 1,
                        startDate: "2026-09-11T10:00:00Z",
                    }),
                ]}
            />
        );
        const row = screen.getByRole("listitem");
        expect(row).not.toHaveTextContent("attempt");
        expect(row).not.toHaveTextContent(/\d+s/);
    });

    it("says so when there are no stages", () => {
        render(<StageTimeline stages={[]} />);
        expect(screen.getByText("No stages reported.")).toBeInTheDocument();
        expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });
});

describe("durationBetween", () => {
    it("formats hours, minutes and seconds like the page header", () => {
        expect(durationBetween("2026-09-11T10:00:00Z", "2026-09-11T11:02:00Z")).toBe("1h 2m");
        expect(durationBetween("2026-09-11T10:00:00Z", "2026-09-11T10:03:04Z")).toBe("3m 4s");
        expect(durationBetween("2026-09-11T10:00:00Z", "2026-09-11T10:00:05Z")).toBe("5s");
    });

    it("returns null when a bound is missing or unreadable", () => {
        expect(durationBetween("2026-09-11T10:00:00Z", undefined)).toBeNull();
        expect(durationBetween("not a date", "2026-09-11T10:00:00Z")).toBeNull();
    });
});
