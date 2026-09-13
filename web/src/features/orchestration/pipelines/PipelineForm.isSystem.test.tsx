/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PipelineForm from "./PipelineForm";
import type { Pipeline } from "../types";

const mockUpdate = jest.fn();
jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: mockUpdate, isPending: false })),
}));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const systemPipeline: Pipeline = {
    databaseId: "GLOBAL",
    pipelineId: "system-genai-metadata",
    pipelineName: "System GenAI Metadata",
    category: "SYSTEM - GenAI",
    description: "Shipped pipeline",
    enabled: true,
    archived: false,
    isSystem: true,
    executionConfig: { executionType: "Lambda", waitForCallback: "Enabled", taskTimeout: "900" },
    systemConfig: {
        inputFileArity: "one",
        assetScope: {},
        metadataInputs: {},
        requireTemplate: true,
    },
};

const renderEdit = (initial: Pipeline) =>
    render(<PipelineForm mode="edit" databaseId="GLOBAL" initial={initial} onDone={jest.fn()} />);

describe("PipelineForm for a system pipeline", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUpdate.mockResolvedValue({});
    });

    it("locks every field except Enabled and says why", () => {
        renderEdit(systemPipeline);
        expect(screen.getByText(/System pipeline:/)).toBeInTheDocument();
        expect(screen.getByLabelText("Pipeline Name *")).toBeDisabled();
        expect(screen.getByLabelText("Category")).toBeDisabled();
        expect(screen.getByLabelText("Execution Type *")).toBeDisabled();
        expect(screen.getByLabelText("Enabled")).not.toBeDisabled();
    });

    it("saves only the enabled flag", async () => {
        const user = userEvent.setup();
        renderEdit(systemPipeline);
        await user.click(screen.getByLabelText("Enabled"));
        await user.click(screen.getByRole("button", { name: "Update" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        expect(mockUpdate).toHaveBeenCalledWith({
            databaseId: "GLOBAL",
            pipelineId: "system-genai-metadata",
            body: { enabled: false },
        });
        expect(mockToast.success).toHaveBeenCalled();
    });

    it("keeps an ordinary pipeline fully editable and sends the full body", async () => {
        const user = userEvent.setup();
        renderEdit({ ...systemPipeline, pipelineId: "mine", isSystem: false });
        expect(screen.queryByText(/System pipeline:/)).toBeNull();
        expect(screen.getByLabelText("Pipeline Name *")).not.toBeDisabled();
        await user.click(screen.getByRole("button", { name: "Update" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        expect(mockUpdate.mock.calls[0][0].body.pipelineName).toBe("System GenAI Metadata");
        expect(mockUpdate.mock.calls[0][0].body.executionConfig).toBeDefined();
    });
});
