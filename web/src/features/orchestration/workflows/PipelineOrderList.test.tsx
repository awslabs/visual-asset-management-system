/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PipelineOrderList, { moveItem } from "./PipelineOrderList";
import { SpecifiedPipelineRef, Pipeline } from "../types";

describe("PipelineOrderList", () => {
    const mockPipelines: Pipeline[] = [
        {
            databaseId: "db1",
            pipelineId: "p1",
            pipelineName: "Pipeline 1",
            executionConfig: { executionType: "Lambda" },
        },
        {
            databaseId: "db1",
            pipelineId: "p2",
            pipelineName: "Pipeline 2",
            executionConfig: { executionType: "Lambda" },
        },
        {
            databaseId: "db1",
            pipelineId: "p3",
            pipelineName: "Pipeline 3",
            executionConfig: { executionType: "Lambda" },
        },
    ];

    describe("moveItem", () => {
        it("moves item from start to end", () => {
            const result = moveItem(["a", "b", "c"], 0, 2);
            expect(result).toEqual(["b", "c", "a"]);
        });

        it("moves item from end to start", () => {
            const result = moveItem(["a", "b", "c"], 2, 0);
            expect(result).toEqual(["c", "a", "b"]);
        });

        it("does not mutate original array", () => {
            const original = ["a", "b", "c"];
            const result = moveItem(original, 0, 2);
            expect(original).toEqual(["a", "b", "c"]);
            expect(result).not.toBe(original);
        });

        it("moves item within middle", () => {
            const result = moveItem(["a", "b", "c", "d"], 1, 2);
            expect(result).toEqual(["a", "c", "b", "d"]);
        });
    });

    describe("PipelineOrderList component", () => {
        it("renders 3 cards in order", () => {
            const refs: SpecifiedPipelineRef[] = [
                { pipelineId: "p1", pipelineDatabaseId: "db1" },
                { pipelineId: "p2", pipelineDatabaseId: "db1" },
                { pipelineId: "p3", pipelineDatabaseId: "db1" },
            ];

            const onChange = jest.fn();

            render(
                <PipelineOrderList
                    value={refs}
                    pipelineOptions={mockPipelines}
                    templatesByPipeline={{}}
                    onChange={onChange}
                />
            );

            // Should render 3 cards
            const removeButtons = screen.getAllByLabelText(/remove step/i);
            expect(removeButtons).toHaveLength(3);
        });

        it("calls onChange with remaining refs when remove is clicked", async () => {
            const user = userEvent.setup();
            const refs: SpecifiedPipelineRef[] = [
                { pipelineId: "p1", pipelineDatabaseId: "db1" },
                { pipelineId: "p2", pipelineDatabaseId: "db1" },
                { pipelineId: "p3", pipelineDatabaseId: "db1" },
            ];

            const onChange = jest.fn();

            render(
                <PipelineOrderList
                    value={refs}
                    pipelineOptions={mockPipelines}
                    templatesByPipeline={{}}
                    onChange={onChange}
                />
            );

            const removeButtons = screen.getAllByLabelText(/remove step/i);
            // Remove middle card (index 1)
            await user.click(removeButtons[1]);

            expect(onChange).toHaveBeenCalledTimes(1);
            expect(onChange).toHaveBeenCalledWith([
                { pipelineId: "p1", pipelineDatabaseId: "db1" },
                { pipelineId: "p3", pipelineDatabaseId: "db1" },
            ]);
        });

        it("labels the remove control with visible text, not a bare glyph", () => {
            // A muted "x" with no button chrome reads as decoration: the control worked, but users
            // could not find it and believed the only way to undo a mis-added step was to leave the
            // wizard and start over. The visible word is the fix, so it is what the test pins.
            render(
                <PipelineOrderList
                    value={[{ pipelineId: "p1", pipelineDatabaseId: "db1" }]}
                    pipelineOptions={mockPipelines}
                    templatesByPipeline={{}}
                    onChange={jest.fn()}
                />
            );
            const button = screen.getByLabelText(/remove step 1/i);
            expect(button).toHaveTextContent(/remove/i);
            // Inside a form a submit-typed button would save the workflow instead of removing a step.
            expect(button).toHaveAttribute("type", "button");
        });

        it("removing the only step leaves the empty state rather than crashing", async () => {
            const user = userEvent.setup();
            const onChange = jest.fn();
            render(
                <PipelineOrderList
                    value={[{ pipelineId: "p1", pipelineDatabaseId: "db1" }]}
                    pipelineOptions={mockPipelines}
                    templatesByPipeline={{}}
                    onChange={onChange}
                />
            );
            await user.click(screen.getByLabelText(/remove step 1/i));
            expect(onChange).toHaveBeenCalledWith([]);
        });
    });
});
