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
    usePipelines: jest.fn(),
    useWorkflow: jest.fn(),
    useWorkflowMutations: jest.fn(),
    useTriggers: jest.fn(),
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

        const { usePipelines, useWorkflow, useWorkflowMutations, useTriggers } = require("../api/queries");

        usePipelines.mockReturnValue({ data: [] });
        useWorkflow.mockReturnValue({ data: undefined });
        useTriggers.mockReturnValue({ data: [] });
        useWorkflowMutations.mockReturnValue({
            createWorkflow: { mutateAsync: mockCreate },
            updateWorkflow: { mutateAsync: mockUpdate },
            archiveWorkflow: { mutateAsync: jest.fn() },
        });
    });

    it("enforces locationType=none requires inputFileArity=none (coupling)", async () => {
        const { validateWorkflow } = require("./workflowValidation");

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Wait for form to render
        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });

        // Initially, validation should have error for no pipelines
        validateWorkflow.mockReturnValue({
            errors: ["At least one pipeline is required"],
            warnings: [],
        });

        // Set up a scenario with locationType=none and arity=one (coupling violation)
        validateWorkflow.mockReturnValue({
            errors: [
                "At least one pipeline is required",
                "Workflows with no output location (results-only) must have inputFileArity set to 'none'",
            ],
            warnings: [],
        });

        // Add a pipeline
        const addPipelineButton = screen.getByText(/add pipeline/i);
        await userEvent.click(addPipelineButton);

        // Mock validation after adding pipeline but with coupling violation
        validateWorkflow.mockReturnValue({
            errors: ["Workflows with no output location (results-only) must have inputFileArity set to 'none'"],
            warnings: [],
        });

        // Wait for validation to run
        await waitFor(() => {
            const saveButton = screen.getByRole("button", { name: /save/i });
            expect(saveButton).toBeDisabled();
        });

        // Expect coupling error message to be shown
        await waitFor(() => {
            expect(
                screen.getByText(/workflows with no output location.*must have inputFileArity.*none/i)
            ).toBeInTheDocument();
        });

        // Fix the coupling: mock validation with no errors
        validateWorkflow.mockReturnValue({
            errors: [],
            warnings: [],
        });

        // Trigger re-render by typing in a field
        const nameInput = screen.getByLabelText(/workflow name/i);
        await userEvent.clear(nameInput);
        await userEvent.type(nameInput, "Test Workflow");

        // Now Save button should be ENABLED
        await waitFor(() => {
            const saveButton = screen.getByRole("button", { name: /save/i });
            expect(saveButton).toBeEnabled();
        });
    });
});
