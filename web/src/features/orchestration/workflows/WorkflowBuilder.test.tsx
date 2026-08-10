/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
    // A no-op: prefetching is a latency optimization with no rendered output, so the builder's
    // behaviour must not depend on it. Its own contract is covered in prefetchTemplates.test.tsx.
    usePrefetchPipelineTemplates: jest.fn(),
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
    allPipelineRefsSelected: jest.fn(() => true),
}));

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
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

    it("sends workflowId as null in the create body when the user supplied none", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({ warnings: [] });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });
        await userEvent.type(screen.getByLabelText(/workflow name/i), "My Workflow");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await waitFor(() => {
            expect(screen.getByText("Add Pipeline")).toBeInTheDocument();
        });
        await userEvent.click(screen.getByText("Add Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        const body = mockCreate.mock.calls[0][0];
        // The backend auto-generates an id for null but rejects an empty string (min_length=1),
        // so sending "" would make every workflow create fail with a 400.
        expect(body.workflowId).toBeNull();
        expect(body.workflowName).toBe("My Workflow");
    });

    it("shows backend save warnings and waits for acknowledgement before navigating", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({
            warnings: ["Pipeline p1 uses assetMetadata but the workflow does not supply it"],
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });
        await userEvent.type(screen.getByLabelText(/workflow name/i), "My Workflow");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByText("Add Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        // The warning must be readable — navigating away in the same tick would discard it.
        expect(await screen.findByText(/Backend Warnings/i)).toBeInTheDocument();
        expect(screen.getByText(/uses assetMetadata/)).toBeInTheDocument();
        expect(mockNavigate).not.toHaveBeenCalledWith("/databases/db1/workflows");

        await userEvent.click(screen.getByRole("button", { name: /continue/i }));
        expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows");
        // Continue only leaves the form; it must not re-submit the already-created workflow.
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });

    it("navigates straight away when the save returns no warnings", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({ warnings: [] });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });
        await userEvent.type(screen.getByLabelText(/workflow name/i), "My Workflow");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByText("Add Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows"));
    });

    it("hydrates allowWorkflowTriggerChaining from a saved workflow and shows the loop warning", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { useWorkflow } = require("../api/queries");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-1",
                workflowName: "Existing",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: { allowWorkflowTriggerChaining: true },
            },
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // The setting lives on the Execution step (systemConfig).
        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        const toggle = await screen.findByRole("checkbox", {
            name: /allow workflow trigger chaining/i,
        });
        expect(toggle).toBeChecked();
        // Enabling chaining must warn about mutual triggering between workflows.
        expect(screen.getByRole("alert")).toHaveTextContent(/trigger each other indefinitely/i);
    });

    it("saves allowWorkflowTriggerChaining and warns only once enabled", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({});

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.type(screen.getByLabelText(/workflow name/i), "Chained WF");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        const toggle = await screen.findByRole("checkbox", {
            name: /allow workflow trigger chaining/i,
        });
        // Default is off, and no warning is shown until it is turned on.
        expect(toggle).not.toBeChecked();
        expect(screen.queryByText(/trigger each other indefinitely/i)).not.toBeInTheDocument();

        await userEvent.click(toggle);
        expect(screen.getByRole("alert")).toHaveTextContent(/trigger each other indefinitely/i);
    });

    it("hydrates the default output path prefix from a saved workflow", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { useWorkflow } = require("../api/queries");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-1",
                workflowName: "Existing",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: {
                    outputTarget: { locationType: "asset", allowOverride: false },
                    defaultOutputFileBaseExecutionPathExtension: "/{{jobName}}/",
                },
            },
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        // The stored value is UNRESOLVED — the tag must survive round-tripping through the form, or
        // saving would flatten every future run into one literal folder.
        const field = await screen.findByRole("textbox", {
            name: /default output path prefix/i,
        });
        expect(field).toHaveValue("/{{jobName}}/");
    });

    it("saves the default output path prefix with its template tags unresolved", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({});

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.type(screen.getByLabelText(/workflow name/i), "Prefixed WF");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        const field = await screen.findByRole("textbox", {
            name: /default output path prefix/i,
        });
        expect(field).toHaveValue("");
        // userEvent.type() reads "{{" as an escaped literal "{", which would silently enter
        // "/{jobName}}/" — set the value directly so the tag reaches the form verbatim.
        fireEvent.change(field, { target: { value: "/{{jobName}}/" } });

        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByText("Add Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        const body = mockCreate.mock.calls[0][0];
        expect(body.systemConfig.defaultOutputFileBaseExecutionPathExtension).toBe("/{{jobName}}/");
    });

    it("hides the default output path prefix for a results-only workflow", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { useWorkflow } = require("../api/queries");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-1",
                workflowName: "Existing",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                // Results-only writes no asset files, so an output path prefix has nothing to apply to.
                systemConfig: { outputTarget: { locationType: "none" } },
            },
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await screen.findByRole("checkbox", { name: /allow workflow trigger chaining/i });
        expect(
            screen.queryByRole("textbox", { name: /default output path prefix/i })
        ).not.toBeInTheDocument();
    });

    it("keeps Save available in edit mode after a warned save so later edits are saveable", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { useWorkflow } = require("../api/queries");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-1",
                workflowName: "Existing",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: {},
            },
        });
        mockUpdate.mockResolvedValue({ warnings: ["arity mismatch"] });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // basic -> execution -> pipelines -> triggers -> review
        for (let i = 0; i < 4; i++) {
            await userEvent.click(screen.getByRole("button", { name: /next/i }));
        }
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        expect(mockUpdate.mock.calls[0][0].workflowId).toBe("wf-1");
        // Both affordances are present: Continue leaves, Save re-submits the (idempotent) PUT.
        expect(await screen.findByRole("button", { name: /continue/i })).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(2));
    });

    it("clears the form when the edited workflow id changes before new data arrives", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { useWorkflow } = require("../api/queries");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-A",
                workflowName: "Workflow A",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: {},
            },
        });

        const { rerender } = render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-A" />
                </MemoryRouter>
            </QueryClientProvider>
        );
        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toHaveValue("Workflow A");
        });

        // The new workflow's GET has not resolved yet.
        useWorkflow.mockReturnValue({ data: undefined });
        rerender(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowBuilder mode="edit" databaseId="db1" workflowId="wf-B" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toHaveValue("");
        });
        expect(screen.getByLabelText(/workflow id/i)).toHaveValue("");
    });
});
