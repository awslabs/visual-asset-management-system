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

// Mock the dependencies
jest.mock("../api/queries", () => ({
    useAllPipelines: jest.fn(),
    useWorkflow: jest.fn(),
    useWorkflowMutations: jest.fn(),
    useTriggers: jest.fn(),
    useTemplates: jest.fn(),
}));

jest.mock("./PipelineOrderList", () => ({
    __esModule: true,
    default: ({ value, onChange }: any) => (
        <div data-testid="pipeline-order-list">
            <button onClick={() => onChange([{ pipelineId: "p1", pipelineDatabaseId: "db1" }])}>
                Add Pipeline
            </button>
        </div>
    ),
}));

jest.mock("./DagPreview", () => ({
    __esModule: true,
    default: () => <div data-testid="dag-preview">DAG</div>,
}));

jest.mock("./workflowValidation", () => ({
    validateWorkflow: jest.fn(() => ({ errors: [], warnings: [] })),
}));

const createQueryClient = () =>
    new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

describe("WorkflowBuilder", () => {
    let queryClient: QueryClient;
    const mockCreate = jest.fn();
    const mockUpdate = jest.fn();

    beforeEach(() => {
        queryClient = createQueryClient();
        jest.clearAllMocks();

        const {
            useAllPipelines,
            useWorkflow,
            useWorkflowMutations,
            useTriggers,
            useTemplates,
        } = require("../api/queries");

        useAllPipelines.mockReturnValue({ data: [] });
        useWorkflow.mockReturnValue({ data: undefined });
        useTriggers.mockReturnValue({ data: [] });
        useTemplates.mockReturnValue({ data: [] }); // Mock templates for TemplatesFetcher helper
        useWorkflowMutations.mockReturnValue({
            createWorkflow: { mutateAsync: mockCreate },
            updateWorkflow: { mutateAsync: mockUpdate },
            archiveWorkflow: { mutateAsync: jest.fn() },
        });
    });

    it("renders as a wizard: Basic step first, Save only on the Review step", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Basic step shows the name field; there is no Save yet (only Next).
        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });
        expect(screen.queryByRole("button", { name: /^save$/i })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();

        // Basic step requires a name before Next is enabled.
        await userEvent.type(screen.getByLabelText(/workflow name/i), "My Workflow");

        // basic -> execution -> pipelines
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        // Pipelines step requires at least one pipeline before Next is enabled.
        await waitFor(() => {
            expect(screen.getByText("Add Pipeline")).toBeInTheDocument();
        });
        await userEvent.click(screen.getByText("Add Pipeline"));

        // pipelines -> review; Save appears on the final step.
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await waitFor(() => {
            expect(screen.getByRole("button", { name: /^save$/i })).toBeInTheDocument();
        });
    });
});
