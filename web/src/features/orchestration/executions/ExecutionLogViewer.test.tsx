/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionLogViewer from "./ExecutionLogViewer";

// ConfigEditor wraps Monaco, which does not render under jsdom.
jest.mock("../components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value }: any) => <pre>{value}</pre>,
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
