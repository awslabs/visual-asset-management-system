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

// The builder gates the create-mode Triggers step on the trigger PUT route; the real hook fetches.
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

// The create flow writes drafts through the service directly (not through a hook), after the POST.
jest.mock("../api/workflows", () => ({
    setTrigger: jest.fn(),
    deleteTrigger: jest.fn(),
}));

jest.mock("./PipelineOrderList", () => ({
    __esModule: true,
    default: ({ onChange }: any) => (
        <div data-testid="pipeline-order-list">
            <button onClick={() => onChange([{ pipelineId: "p1", pipelineDatabaseId: "db1" }])}>
                Add Pipeline
            </button>
            <button onClick={() => onChange([{ pipelineId: "p2", pipelineDatabaseId: "db1" }])}>
                Swap Pipeline
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
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        const { setTrigger } = require("../api/workflows");
        setTrigger.mockResolvedValue([true, {}]);
    });

    const renderBuilder = (
        props: React.ComponentProps<typeof WorkflowBuilder>,
        initialEntries?: Array<{ pathname: string; state?: unknown }>
    ) =>
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter initialEntries={initialEntries}>
                    <WorkflowBuilder {...props} />
                </MemoryRouter>
            </QueryClientProvider>
        );

    /** basic -> execution -> pipelines (one pipeline added) -> triggers. */
    const walkCreateToTriggers = async (name = "My Workflow") => {
        await waitFor(() => {
            expect(screen.getByLabelText(/workflow name/i)).toBeInTheDocument();
        });
        await userEvent.type(screen.getByLabelText(/workflow name/i), name);
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByText("Add Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
    };

    /** On the Triggers step: adds a file-upload draft, named `name` or the bare key when empty. */
    const addDraft = async (name = "") => {
        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        if (name) await userEvent.type(await screen.findByLabelText("Trigger name"), name);
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    };

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

        // pipelines -> triggers -> review; Save appears on the final step.
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
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
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        const body = mockCreate.mock.calls[0][0];
        // The backend auto-generates an id for null but rejects an empty string (min_length=1),
        // so sending "" would make every workflow create fail with a 400.
        expect(body.workflowId).toBeNull();
        expect(body.workflowName).toBe("My Workflow");
    });

    it("hands a warned create to the edit route on the Review step, carrying the warnings, after the drafts are written", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        const { setTrigger } = require("../api/workflows");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({
            workflowId: "wf-new",
            warnings: ["Pipeline p1 uses assetMetadata but the workflow does not supply it"],
        });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await addDraft("nightly");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        // The hop replaces the create entry: Back from the edit route must not reopen a blank
        // create form for a workflow that now exists.
        await waitFor(() =>
            expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows/wf-new", {
                replace: true,
                state: {
                    step: "review",
                    backendWarnings: [
                        "Pipeline p1 uses assetMetadata but the workflow does not supply it",
                    ],
                },
            })
        );
        // The draft was written against the new id BEFORE the hop: warnings do not skip the PUTs.
        expect(setTrigger).toHaveBeenCalledTimes(1);
        expect(setTrigger.mock.calls[0].slice(0, 3)).toEqual([
            "db1",
            "wf-new",
            "fileUpload#nightly",
        ]);
        expect(setTrigger.mock.invocationCallOrder[0]).toBeLessThan(
            mockNavigate.mock.invocationCallOrder[0]
        );
        // The warnings are read on the edit route, not on the list.
        expect(mockNavigate).not.toHaveBeenCalledWith("/databases/db1/workflows");
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });

    it("keeps the acknowledgement on the form when a warned response carries no workflow id", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({ warnings: ["arity mismatch"] });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        expect(await screen.findByText(/Backend Warnings/i)).toBeInTheDocument();
        expect(screen.getByText(/arity mismatch/)).toBeInTheDocument();
        expect(mockNavigate).not.toHaveBeenCalled();
        // Save is withdrawn: a second submit would create a second workflow.
        expect(screen.queryByRole("button", { name: /^save$/i })).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: /continue/i }));
        expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows");
        expect(mockCreate).toHaveBeenCalledTimes(1);
    });

    it("navigates to the list straight away when the create returns no warnings", async () => {
        const { validateWorkflow } = require("./workflowValidation");
        validateWorkflow.mockReturnValue({ errors: [], warnings: [] });
        mockCreate.mockResolvedValue({ workflowId: "wf-new", warnings: [] });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
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

    it("offers the Triggers step in create mode, after Pipelines and before Review", async () => {
        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();

        expect(
            screen.getByRole("button", { name: /add file upload trigger/i })
        ).toBeInTheDocument();
        expect(screen.getByText(/written once the workflow is created/i)).toBeInTheDocument();
        // Not the last step: Next, not Save.
        expect(screen.queryByRole("button", { name: /^save$/i })).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        expect(await screen.findByRole("button", { name: /^save$/i })).toBeInTheDocument();
    });

    it("gates the create-mode Triggers step on the trigger PUT route", async () => {
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        const can = jest.fn(
            (_method: string, path: string) =>
                path !== "/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}"
        );
        useAllowedRoutes.mockReturnValue({ loading: false, can });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();

        expect(can).toHaveBeenCalledWith(
            "PUT",
            "/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}"
        );
        expect(
            screen.getByText(/can create workflows but cannot set triggers/i)
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: /add file upload trigger/i })
        ).not.toBeInTheDocument();
        // The step stays in the stepper and can be passed.
        expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
    });

    it("shows a skeleton — no add controls, no denial — while permissions load", async () => {
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useAllowedRoutes.mockReturnValue({ loading: true, can: jest.fn(() => false) });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();

        expect(screen.getByTestId("triggers-permission-skeleton")).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: /add file upload trigger/i })
        ).not.toBeInTheDocument();
        expect(screen.queryByText(/cannot set triggers/i)).not.toBeInTheDocument();
    });

    // The gate belongs to create mode only: when editing, the live editor renders whatever the PUT
    // route says, because the workflow already exists and the server enforces each write itself.
    it("renders the live editor in edit mode whatever the trigger PUT gate says", async () => {
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        const { useWorkflow } = require("../api/queries");
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => false) });
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-1",
                workflowName: "Existing",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: {},
            },
        });

        renderBuilder({ mode: "edit", databaseId: "db1", workflowId: "wf-1" });
        // basic -> execution -> pipelines -> triggers (the loaded name and pipeline satisfy the gates)
        for (let i = 0; i < 3; i++) {
            await userEvent.click(screen.getByRole("button", { name: /next/i }));
        }

        expect(await screen.findByRole("heading", { name: "Triggers" })).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: /add file upload trigger/i })
        ).toBeInTheDocument();
        expect(screen.queryByText(/cannot set triggers/i)).not.toBeInTheDocument();
        expect(screen.queryByTestId("triggers-permission-skeleton")).not.toBeInTheDocument();
    });

    it("lists the trigger drafts on the Review step", async () => {
        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await addDraft("nightly");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));

        expect(await screen.findByRole("button", { name: /^save$/i })).toBeInTheDocument();
        expect(screen.getByText(/^Triggers:/)).toBeInTheDocument();
        expect(screen.getByRole("list", { name: "Trigger drafts" })).toHaveTextContent(
            "fileUpload#nightly — enabled, 0 allow / 0 exclude, 0 default templates"
        );
    });

    it("writes each draft after the POST, one at a time, against the returned id, and seeds the cache", async () => {
        const { setTrigger } = require("../api/workflows");
        const created = { workflowId: "wf-new", workflowName: "My Workflow", warnings: [] };
        mockCreate.mockResolvedValue(created);
        let resolveFirst!: (value: [boolean, unknown]) => void;
        setTrigger
            .mockImplementationOnce(
                () =>
                    new Promise<[boolean, unknown]>((resolve) => {
                        resolveFirst = resolve;
                    })
            )
            .mockResolvedValueOnce([true, {}]);

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await addDraft();
        await addDraft("nightly");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        // The first PUT waits on the POST and the second PUT waits on the first.
        await waitFor(() => expect(setTrigger).toHaveBeenCalledTimes(1));
        expect(setTrigger.mock.invocationCallOrder[0]).toBeGreaterThan(
            mockCreate.mock.invocationCallOrder[0]
        );
        expect(setTrigger.mock.calls[0].slice(0, 3)).toEqual(["db1", "wf-new", "fileUpload"]);
        expect(setTrigger.mock.calls[0][3]).toEqual(
            expect.objectContaining({ triggerType: "fileUpload", enabled: true })
        );
        await new Promise((r) => setTimeout(r, 20));
        expect(setTrigger).toHaveBeenCalledTimes(1);

        resolveFirst([true, {}]);
        await waitFor(() => expect(setTrigger).toHaveBeenCalledTimes(2));
        expect(setTrigger.mock.calls[1].slice(0, 3)).toEqual([
            "db1",
            "wf-new",
            "fileUpload#nightly",
        ]);
        await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows"));
        expect(mockCreate).toHaveBeenCalledTimes(1);
        // The edit route reads this entry, so a later hop does not wait on the GET.
        expect(queryClient.getQueryData(["workflow", "db1", "wf-new"])).toEqual(created);
    });

    it("sends no trigger request when there are no drafts", async () => {
        const { setTrigger } = require("../api/workflows");
        mockCreate.mockResolvedValue({ workflowId: "wf-new", warnings: [] });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows"));
        expect(setTrigger).not.toHaveBeenCalled();
    });

    it("hands a failed trigger write to the edit route on the Triggers step without a second POST", async () => {
        const { setTrigger } = require("../api/workflows");
        mockCreate.mockResolvedValue({ workflowId: "wf-new", warnings: [] });
        setTrigger
            .mockResolvedValueOnce([true, {}])
            .mockResolvedValueOnce([false, "This workflow restricts concurrency per asset"]);

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await addDraft();
        await addDraft("nightly");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() =>
            expect(mockNavigate).toHaveBeenCalledWith(
                "/databases/db1/workflows/wf-new",
                expect.objectContaining({ state: expect.objectContaining({ step: "triggers" }) })
            )
        );
        const hopOptions = mockNavigate.mock.calls.find(
            (call) => call[0] === "/databases/db1/workflows/wf-new"
        )![1];
        expect(hopOptions.replace).toBe(true);
        const hop = hopOptions.state;
        expect(hop.pendingTriggers).toEqual([
            {
                draft: expect.objectContaining({ triggerType: "fileUpload#nightly" }),
                error: "This workflow restricts concurrency per asset",
            },
        ]);
        expect(hop.backendWarnings).toEqual([]);
        expect(mockCreate).toHaveBeenCalledTimes(1);
        expect(setTrigger).toHaveBeenCalledTimes(2);
        expect(mockNavigate).not.toHaveBeenCalledWith("/databases/db1/workflows");
    });

    it("narrows a draft's default templates to the pipelines in the saved body", async () => {
        const { setTrigger } = require("../api/workflows");
        const { useTemplates } = require("../api/queries");
        useTemplates.mockReturnValue({
            data: [{ templateId: "t1", templateName: "Template One" }],
        });
        mockCreate.mockResolvedValue({ workflowId: "wf-new", warnings: [] });

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.selectOptions(await screen.findByRole("combobox"), "t1");
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

        // Back to Pipelines, replace p1 by p2, then forward through Triggers to Review and save.
        await userEvent.click(screen.getByRole("button", { name: /back/i }));
        await userEvent.click(await screen.findByText("Swap Pipeline"));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        // Review counts what Save will send, not what was picked: the chosen template belongs
        // to the pipeline that was swapped out.
        expect(await screen.findByRole("list", { name: "Trigger drafts" })).toHaveTextContent(
            "fileUpload — enabled, 0 allow / 0 exclude, 0 default templates"
        );
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        // The draft chose db1:p1's template; that pipeline is no longer in the body.
        expect(setTrigger.mock.calls[0][3].defaultTemplateIds).toEqual({});
    });

    it("leaves a failed POST as today: no PUT, error in the panel, still in create mode", async () => {
        const { setTrigger } = require("../api/workflows");
        mockCreate.mockRejectedValue(new Error("Workflow name already exists"));

        renderBuilder({ mode: "create", databaseId: "db1" });
        await walkCreateToTriggers();
        await addDraft("nightly");
        await userEvent.click(screen.getByRole("button", { name: /next/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        expect(await screen.findByText(/Workflow name already exists/)).toBeInTheDocument();
        expect(setTrigger).not.toHaveBeenCalled();
        expect(mockNavigate).not.toHaveBeenCalled();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeInTheDocument();
    });

    it("hands the live editor the route workflow id, not the reducer's, so it queries before the GET resolves", async () => {
        const { useWorkflow, useTriggers } = require("../api/queries");
        // The single-workflow GET has not resolved: the reducer's workflowIdValue is still "".
        useWorkflow.mockReturnValue({ data: undefined });

        renderBuilder({ mode: "edit", databaseId: "db1", workflowId: "wf-1" }, [
            { pathname: "/databases/db1/workflows/wf-1", state: { step: "triggers" } },
        ]);

        expect(await screen.findByRole("heading", { name: "Triggers" })).toBeInTheDocument();
        expect(useTriggers).toHaveBeenCalledWith("db1", "wf-1");
        expect(useTriggers).not.toHaveBeenCalledWith("db1", "");
    });

    it("consumes a recovery hop: shows the step, opens the pending draft, seeds the warnings, clears the state", async () => {
        const { useWorkflow, useTriggers } = require("../api/queries");
        // The GET for the new workflow has not resolved yet; the hop must not depend on it.
        useWorkflow.mockReturnValue({ data: undefined });
        useTriggers.mockReturnValue({ data: [], isLoading: false });

        renderBuilder({ mode: "edit", databaseId: "db1", workflowId: "wf-new" }, [
            {
                pathname: "/databases/db1/workflows/wf-new",
                state: {
                    step: "triggers",
                    pendingTriggers: [
                        {
                            draft: {
                                triggerType: "fileUpload#nightly",
                                enabled: true,
                                inputFileFilters: { allow: [], exclude: [] },
                                defaultTemplateIds: {},
                            },
                            error: "This workflow restricts concurrency per asset",
                        },
                    ],
                    backendWarnings: ["arity mismatch"],
                },
            },
        ]);

        // On the Triggers step, with the failed draft open and the server's reason beside it.
        expect(await screen.findByLabelText("Trigger name")).toHaveValue("nightly");
        expect(screen.getByText(/restricts concurrency per asset/)).toBeInTheDocument();
        // The create warnings travelled with the hop.
        expect(screen.getByText(/Backend Warnings/i)).toBeInTheDocument();
        expect(screen.getByText(/arity mismatch/)).toBeInTheDocument();
        // History no longer carries the hop, so a reload or Back does not replay it.
        expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows/wf-new", {
            replace: true,
            state: null,
        });
        // The edit route has no warned-save banner to acknowledge.
        expect(screen.queryByRole("button", { name: /continue/i })).not.toBeInTheDocument();
    });

    it("lands a warned create on the Review step of the edit route", async () => {
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({
            data: {
                databaseId: "db1",
                workflowId: "wf-new",
                workflowName: "Created",
                specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
                systemConfig: {},
            },
        });

        renderBuilder({ mode: "edit", databaseId: "db1", workflowId: "wf-new" }, [
            {
                pathname: "/databases/db1/workflows/wf-new",
                state: { step: "review", backendWarnings: ["arity mismatch"] },
            },
        ]);

        // Review is the last step: Save (edit mode, idempotent PUT) is offered, no Next.
        expect(await screen.findByRole("button", { name: /^save$/i })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
        expect(screen.getByText(/arity mismatch/)).toBeInTheDocument();
        // The loaded workflow survived the hop's RESET/LOAD sequence: its name reaches the Review
        // card (the breadcrumb shows it too, so the bare text is not unique).
        expect(screen.getByText(/^Name:/).closest("div")).toHaveTextContent("Created");
        expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows/wf-new", {
            replace: true,
            state: null,
        });
    });

    describe("after a recovery hop", () => {
        const pendingDraft = (name: string, error: string) => ({
            draft: {
                triggerType: `fileUpload#${name}`,
                enabled: true,
                inputFileFilters: { allow: [], exclude: [] },
                defaultTemplateIds: {},
            },
            error,
        });
        const storedTrigger = (key: string) => ({
            triggerType: key,
            triggerBaseType: "fileUpload",
            triggerId: key.split("#")[1] || "",
            enabled: true,
        });
        const NIGHTLY_ERROR =
            "Another trigger of this type already uses the same default templates";
        const WEEKLY_ERROR = "This workflow restricts concurrency per asset";
        // The stored workflow has a pipeline, so Next is enabled on the Pipelines step.
        const savedWorkflow = {
            databaseId: "db1",
            workflowId: "wf-new",
            workflowName: "Created",
            specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db1" }],
            systemConfig: {},
        };

        /** Lands the edit route from the create flow's hand-off with `pending` still unwritten. */
        const renderHop = (pending: unknown[]) => {
            const { useWorkflow } = require("../api/queries");
            useWorkflow.mockReturnValue({ data: savedWorkflow });
            return renderBuilder({ mode: "edit", databaseId: "db1", workflowId: "wf-new" }, [
                {
                    pathname: "/databases/db1/workflows/wf-new",
                    state: { step: "triggers", pendingTriggers: pending, backendWarnings: [] },
                },
            ]);
        };

        /** Makes the trigger list reflect each PUT, the way the invalidated refetch does. */
        const storeEachWrite = () => {
            const { useTriggers } = require("../api/queries");
            const { setTrigger } = require("../api/workflows");
            let stored: unknown[] = [];
            useTriggers.mockImplementation(() => ({ data: stored, isLoading: false }));
            setTrigger.mockImplementation(async (_db: string, _wf: string, key: string) => {
                stored = [...stored, storedTrigger(key)];
                return [true, {}];
            });
        };

        /** Saves the draft open in the form and waits for the form to close. */
        const saveOpenDraft = async () => {
            await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
            await waitFor(() =>
                expect(screen.queryByLabelText("Trigger name")).not.toBeInTheDocument()
            );
        };

        /** Back to Pipelines (the editor unmounts) and Next to Triggers (it mounts again). */
        const leaveAndRevisitTriggers = async () => {
            await userEvent.click(screen.getByRole("button", { name: /back/i }));
            expect(await screen.findByText("Add Pipeline")).toBeInTheDocument();
            await userEvent.click(screen.getByRole("button", { name: /next/i }));
            expect(screen.queryByText("Add Pipeline")).not.toBeInTheDocument();
        };

        it("does not reopen a draft once it is written: revisiting Triggers lists only what is still unsaved", async () => {
            const { setTrigger } = require("../api/workflows");
            storeEachWrite();
            renderHop([
                pendingDraft("nightly", NIGHTLY_ERROR),
                pendingDraft("weekly", WEEKLY_ERROR),
            ]);

            expect(await screen.findByLabelText("Trigger name")).toHaveValue("nightly");
            await saveOpenDraft();
            expect(setTrigger).toHaveBeenCalledTimes(1);
            expect(setTrigger.mock.calls[0][2]).toBe("fileUpload#nightly");

            await leaveAndRevisitTriggers();
            expect(screen.queryByLabelText("Trigger name")).not.toBeInTheDocument();
            expect(screen.queryByText(NIGHTLY_ERROR)).not.toBeInTheDocument();
            expect(
                screen.queryByRole("button", { name: "Retry trigger fileUpload#nightly" })
            ).not.toBeInTheDocument();
            expect(
                screen.getByRole("button", { name: "Retry trigger fileUpload#weekly" })
            ).toBeInTheDocument();
        });

        it("opens the handed-off draft once: revisiting Triggers lists it as Not saved instead", async () => {
            renderHop([pendingDraft("nightly", NIGHTLY_ERROR)]);

            expect(await screen.findByLabelText("Trigger name")).toHaveValue("nightly");
            await leaveAndRevisitTriggers();

            expect(screen.queryByLabelText("Trigger name")).not.toBeInTheDocument();
            expect(screen.getByText("Not saved")).toBeInTheDocument();
            expect(screen.getByText(NIGHTLY_ERROR)).toBeInTheDocument();
            expect(
                screen.getByRole("button", { name: "Retry trigger fileUpload#nightly" })
            ).toBeInTheDocument();
        });

        it("confirms before Cancel discards unwritten drafts, and leaves to the list rather than back", async () => {
            const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);
            try {
                renderHop([
                    pendingDraft("nightly", NIGHTLY_ERROR),
                    pendingDraft("weekly", WEEKLY_ERROR),
                ]);
                expect(await screen.findByLabelText("Trigger name")).toHaveValue("nightly");
                // The form's own Cancel comes first in the document; closing it leaves the wizard's.
                await userEvent.click(screen.getAllByRole("button", { name: /^cancel$/i })[0]);
                expect(await screen.findByText("Not saved")).toBeInTheDocument();

                await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
                expect(confirmSpy).toHaveBeenCalledWith(
                    expect.stringMatching(/2 unsaved triggers/)
                );
                expect(mockNavigate).not.toHaveBeenCalledWith(-1);
                expect(mockNavigate).not.toHaveBeenCalledWith("/databases/db1/workflows");

                confirmSpy.mockReturnValue(true);
                await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
                // The create entry the hop came from was replaced, so there is no form to go back to.
                expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows");
                expect(mockNavigate).not.toHaveBeenCalledWith(-1);
            } finally {
                confirmSpy.mockRestore();
            }
        });

        it("does not confirm Cancel once every handed-off draft has been written", async () => {
            const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);
            try {
                storeEachWrite();
                renderHop([pendingDraft("nightly", NIGHTLY_ERROR)]);
                expect(await screen.findByLabelText("Trigger name")).toHaveValue("nightly");
                await saveOpenDraft();

                await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
                expect(confirmSpy).not.toHaveBeenCalled();
                expect(mockNavigate).toHaveBeenCalledWith("/databases/db1/workflows");
                expect(mockNavigate).not.toHaveBeenCalledWith(-1);
            } finally {
                confirmSpy.mockRestore();
            }
        });
    });
});
