/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WorkflowBuilder from "./WorkflowBuilder";
import type { Workflow } from "../types";

const mockUpdate = jest.fn();
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));
// Query results are shared objects: a fresh `{ data: [] }` per call re-runs the builder's
// validation effect on every render (the pipelines memo sees a new array each time).
jest.mock("../api/queries", () => {
    const noPipelines = { data: [], isSuccess: true };
    const noTemplates = { data: [] };
    return {
        useAllPipelines: jest.fn(() => noPipelines),
        useWorkflow: jest.fn(),
        useWorkflowMutations: jest.fn(() => ({
            createWorkflow: { mutateAsync: jest.fn() },
            updateWorkflow: { mutateAsync: mockUpdate },
        })),
        useTemplates: jest.fn(() => noTemplates),
        usePrefetchPipelineTemplates: jest.fn(),
    };
});
// The builder gates the create-mode Triggers step on the trigger PUT route; the real hook fetches.
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(() => ({ loading: false, can: jest.fn(() => true) })),
}));
jest.mock("./DagPreview", () => ({ __esModule: true, default: () => null }));
jest.mock("./TriggersEditor", () => ({ __esModule: true, default: () => null }));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const systemWorkflow: Workflow = {
    databaseId: "GLOBAL",
    workflowId: "system-genai-metadata",
    workflowName: "System GenAI Metadata",
    category: "SYSTEM - GenAI",
    description: "Shipped workflow",
    enabled: true,
    archived: false,
    isSystem: true,
    specifiedPipelines: [{ pipelineId: "system-genai-metadata", pipelineDatabaseId: "GLOBAL" }],
    systemConfig: { inputFileArity: "one", concurrencyRestriction: "perInputFileVersion" },
};

const renderBuilder = (workflow: Workflow) => {
    const { useWorkflow } = require("../api/queries");
    useWorkflow.mockReturnValue({ data: workflow });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <WorkflowBuilder mode="edit" databaseId="GLOBAL" workflowId={workflow.workflowId} />
            </MemoryRouter>
        </QueryClientProvider>
    );
};

describe("WorkflowBuilder for a system workflow", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUpdate.mockResolvedValue({});
    });

    it("locks every field except Enabled and says why", async () => {
        renderBuilder(systemWorkflow);
        await waitFor(() =>
            expect(screen.getByLabelText("Workflow Name")).toHaveValue("System GenAI Metadata")
        );
        expect(screen.getByText(/System workflow:/)).toBeInTheDocument();
        expect(screen.getByLabelText("Workflow Name")).toBeDisabled();
        expect(screen.getByLabelText("Category (optional)")).toBeDisabled();
        expect(screen.getByLabelText("Enabled")).not.toBeDisabled();
    });

    it("saves only the enabled flag from the Review step", async () => {
        const user = userEvent.setup();
        renderBuilder(systemWorkflow);
        await waitFor(() => expect(screen.getByLabelText("Enabled")).toBeChecked());
        await user.click(screen.getByLabelText("Enabled"));
        // Basic -> Execution -> Pipelines -> Triggers -> Review
        for (let i = 0; i < 4; i++) {
            await user.click(screen.getByRole("button", { name: "Next" }));
        }
        await user.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        expect(mockUpdate).toHaveBeenCalledWith({
            databaseId: "GLOBAL",
            workflowId: "system-genai-metadata",
            body: { enabled: false },
        });
        expect(mockNavigate).toHaveBeenCalledWith("/databases/GLOBAL/workflows");
    });

    it("keeps an ordinary workflow editable", async () => {
        renderBuilder({ ...systemWorkflow, workflowId: "mine", isSystem: false });
        await waitFor(() =>
            expect(screen.getByLabelText("Workflow Name")).toHaveValue("System GenAI Metadata")
        );
        expect(screen.queryByText(/System workflow:/)).toBeNull();
        expect(screen.getByLabelText("Workflow Name")).not.toBeDisabled();
    });
});
