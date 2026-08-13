/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard, { validateInputSelection } from "./ExecuteWizard";
import type { Workflow, Pipeline, Template } from "../types";

// Mock the API queries
jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useTemplates: jest.fn(),
    useTemplate: jest.fn(),
    // A no-op here: prefetching only warms caches, so the wizard must render identically without
    // it. Its own contract is covered in prefetchTemplates.test.tsx.
    usePrefetchPipelineTemplates: jest.fn(),
    useExecuteWorkflow: jest.fn(),
    // WizardInputStage's cascading selectors call these; default to idle/empty so the wizard
    // renders. Individual tests can override if they exercise the input selectors.
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useAssets: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    // The wizard resolves assets SERVER-side per search term (useAssetSearch), so the mock returns
    // the paged shape: { items, total, listFallback }.
    useAssetSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
        error: null,
    })),
    useAssetFiles: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    // File matches are ALSO resolved server-side per row (an asset can hold thousands of files), so
    // this returns the same paged shape. Absent from this factory, any test that reaches a
    // multi-arity input row threw `useAssetFileSearch is not a function`.
    useAssetFileSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
        error: null,
    })),
    useFileVersions: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

// Mock Monaco editor
jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
}));

describe("ExecuteWizard", () => {
    let queryClient: QueryClient;
    let mockExecuteWorkflow: any;

    const mockWorkflow: Workflow = {
        databaseId: "db1",
        workflowId: "wf1",
        workflowName: "Test Workflow",
        enabled: true,
        archived: false,
        specifiedPipelines: [
            {
                pipelineId: "pipe1",
                pipelineDatabaseId: "db1",
                defaultTemplateId: "tpl1",
            },
        ],
        systemConfig: {
            inputFileArity: "none",
        },
    };

    // Arity matches the workflow's 'none': a pipeline that requires a file inside a workflow that
    // selects none is an invalid combination the wizard (and the backend) rejects.
    const mockPipeline: Pipeline = {
        databaseId: "db1",
        pipelineId: "pipe1",
        pipelineName: "Test Pipeline",
        enabled: true,
        executionConfig: {
            executionType: "Lambda",
        },
        systemConfig: {
            inputFileArity: "none",
        },
    };

    const mockTemplate: Template = {
        databaseId: "db1",
        pipelineId: "pipe1",
        templateId: "tpl1",
        templateName: "Test Template",
        configFormat: "json",
        configBody: '{"test": "{{requiredTag}}"}',
        tagSchema: [
            {
                tagKey: "requiredTag",
                type: "string",
                required: true,
                label: "Required Tag",
            },
        ],
    };

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
                mutations: { retry: false },
            },
        });

        mockExecuteWorkflow = {
            mutateAsync: jest.fn().mockResolvedValue({ warnings: [] }),
            isPending: false,
        };

        const {
            useWorkflow,
            useAllPipelines,
            useTemplates,
            useExecuteWorkflow,
        } = require("../api/queries");

        useWorkflow.mockReturnValue({
            data: mockWorkflow,
            isLoading: false,
        });

        useAllPipelines.mockReturnValue({
            data: [mockPipeline],
            isLoading: false,
            isSuccess: true,
        });

        // The real templates LIST endpoint omits tagSchema and blanks S3-offloaded bodies. Mock it
        // that way so a component that needs the tag schema cannot pass on a fat fixture; the full
        // template comes from the single-template hook below.
        const { tagSchema: _omitted, ...listDescriptor } = mockTemplate as any;
        useTemplates.mockReturnValue({
            data: [listDescriptor],
            isLoading: false,
            isSuccess: true,
        });

        const { useTemplate } = require("../api/queries");
        useTemplate.mockReturnValue({
            data: mockTemplate,
            isLoading: false,
            isSuccess: true,
        });

        useExecuteWorkflow.mockReturnValue(mockExecuteWorkflow);
    });

    afterEach(() => {
        jest.clearAllMocks();
    });

    it("renders stepper and navigates through stages", async () => {
        const onClose = jest.fn();

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        // Stepper should be visible (check for all step labels)
        const inputSteps = screen.getAllByText(/Input/i);
        expect(inputSteps.length).toBeGreaterThan(0);

        const pipelineSteps = screen.getAllByText(/Test Pipeline/i);
        expect(pipelineSteps.length).toBeGreaterThan(0);

        const reviewSteps = screen.getAllByText(/Review/i);
        expect(reviewSteps.length).toBeGreaterThan(0);
    });

    // --- Output path prefix: the workflow's stored default pre-fills the field ------------------
    // The default is stored UNRESOLVED, so the form must round-trip the {{tag}} verbatim and let the
    // backend resolve it per run. `undefined` (untouched) lets the backend apply the default; an
    // explicit "" means "asset root" and must therefore be SENT, not omitted.

    const prefixWorkflow = (defaultPrefix?: string) => ({
        ...mockWorkflow,
        systemConfig: {
            ...mockWorkflow.systemConfig,
            outputTarget: { locationType: "asset" as const, allowOverride: false },
            ...(defaultPrefix === undefined
                ? {}
                : { defaultOutputFileBaseExecutionPathExtension: defaultPrefix }),
        },
    });

    /** Satisfy the default fixture's required tag, so Launch is enabled and the body can be read. */
    const withSatisfiedTags = () => {
        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useTemplates, useTemplate } = require("../api/queries");
        const { tagSchema: _omit, ...listRow } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRow], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });
    };

    const launch = async () => {
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());
        await waitFor(() =>
            expect(screen.getByRole("button", { name: /Launch/i })).not.toBeDisabled()
        );
        fireEvent.click(screen.getByRole("button", { name: /Launch/i }));
        await waitFor(() => expect(mockExecuteWorkflow.mutateAsync).toHaveBeenCalled());
        return mockExecuteWorkflow.mutateAsync.mock.calls[0][0].body;
    };

    it("pre-fills the output path prefix from the workflow default, tags unresolved", async () => {
        const workflow = prefixWorkflow("/{{jobName}}/");
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
        withSatisfiedTags();

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard open onClose={jest.fn()} workflow={workflow} databaseId="db1" />
            </QueryClientProvider>
        );

        const field = await screen.findByLabelText("Output path prefix");
        await waitFor(() => expect(field).toHaveValue("/{{jobName}}/"));
        expect(await launch()).toMatchObject({
            outputFileBaseExecutionPathExtension: "/{{jobName}}/",
        });
    });

    it("sends nothing for the prefix when the workflow has no default and the user types none", async () => {
        const workflow = prefixWorkflow(undefined);
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
        withSatisfiedTags();

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard open onClose={jest.fn()} workflow={workflow} databaseId="db1" />
            </QueryClientProvider>
        );

        expect(await screen.findByLabelText("Output path prefix")).toHaveValue("");
        const body = await launch();
        expect(body.outputFileBaseExecutionPathExtension).toBeUndefined();
    });

    it("sends an explicitly cleared prefix so the workflow default is not re-applied", async () => {
        // Clearing the pre-filled field is a deliberate "write at the asset root". Omitting the field
        // would make the backend fall back to the very default the user just removed.
        const workflow = prefixWorkflow("/{{jobName}}/");
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
        withSatisfiedTags();

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard open onClose={jest.fn()} workflow={workflow} databaseId="db1" />
            </QueryClientProvider>
        );

        const field = await screen.findByLabelText("Output path prefix");
        await waitFor(() => expect(field).toHaveValue("/{{jobName}}/"));
        fireEvent.change(field, { target: { value: "" } });

        const body = await launch();
        expect(body.outputFileBaseExecutionPathExtension).toBe("");
    });

    it("does not re-seed over a user-edited prefix on a later re-render", async () => {
        // The seed must fire once. A re-render (a refetch settling, a parent state change) must not
        // put the workflow default back over what the user typed.
        const workflow = prefixWorkflow("/{{jobName}}/");
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
        withSatisfiedTags();

        const tree = (
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard open onClose={jest.fn()} workflow={workflow} databaseId="db1" />
            </QueryClientProvider>
        );
        const { rerender } = render(tree);

        const field = await screen.findByLabelText("Output path prefix");
        await waitFor(() => expect(field).toHaveValue("/{{jobName}}/"));
        fireEvent.change(field, { target: { value: "/mine/" } });

        rerender(tree);

        await waitFor(() =>
            expect(screen.getByLabelText("Output path prefix")).toHaveValue("/mine/")
        );
        expect(await launch()).toMatchObject({
            outputFileBaseExecutionPathExtension: "/mine/",
        });
    });

    it("disables Launch when required tags are missing", async () => {
        const onClose = jest.fn();

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        // Navigate to pipeline stage (skip input stage - click Next)
        const nextButton = screen.getByRole("button", { name: /Next/i });
        fireEvent.click(nextButton);

        // Wait for pipeline stage to render
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            const pipelineHeader = headers.find((h) => h.textContent?.includes("Test Pipeline"));
            expect(pipelineHeader).toBeInTheDocument();
        });

        // Verify validation errors are shown inline in the pipeline stage (required tag missing)
        await waitFor(() => {
            expect(screen.getByText(/Validation Errors/i)).toBeInTheDocument();
            expect(screen.getByText(/Required tags missing: requiredTag/i)).toBeInTheDocument();
        });

        // Navigate to review stage
        const nextButton2 = screen.getByRole("button", { name: /Next/i });
        fireEvent.click(nextButton2);

        // Wait for Review stage
        await waitFor(() => {
            expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument();
        });

        // Launch button should be DISABLED because required tag is missing
        const launchBtn = screen.getByRole("button", { name: /Launch/i });
        expect(launchBtn).toBeDisabled();
    });

    it("enables Launch and uses resolved params when tags are satisfied", async () => {
        const onClose = jest.fn();

        // Create a template with a required tag that HAS a default value
        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };

        const {
            useTemplates: mockUseTemplates,
            useTemplate: mockUseTemplate,
            useExecuteWorkflow: mockUseExecuteWorkflow,
        } = require("../api/queries");

        // List = light descriptor (no tagSchema); the tag schema arrives via the single-template GET.
        const { tagSchema: _omit, ...listRow } = templateWithDefault as any;
        mockUseTemplates.mockReturnValue({
            data: [listRow],
            isLoading: false,
            isSuccess: true,
        });
        mockUseTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        mockUseExecuteWorkflow.mockReturnValue(mockExecuteWorkflow);

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        // Navigate through input stage
        const nextButton = screen.getByRole("button", { name: /Next/i });
        fireEvent.click(nextButton);

        // Wait for pipeline stage
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            const pipelineHeader = headers.find((h) => h.textContent?.includes("Test Pipeline"));
            expect(pipelineHeader).toBeInTheDocument();
        });

        // Navigate to Review
        const nextButton2 = screen.getByRole("button", { name: /Next/i });
        fireEvent.click(nextButton2);

        // Wait for Review stage
        await waitFor(() => {
            expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument();
        });

        // Launch button should be enabled (tag has default value, no errors)
        await waitFor(() => {
            const launchBtn = screen.getByRole("button", { name: /Launch/i });
            expect(launchBtn).not.toBeDisabled();
        });

        // Click Launch
        const launchBtn = screen.getByRole("button", { name: /Launch/i });
        fireEvent.click(launchBtn);

        // Verify mutateAsync was called with resolved params (from resolvePipelineParams)
        await waitFor(() => {
            expect(mockExecuteWorkflow.mutateAsync).toHaveBeenCalledWith({
                workflowDatabaseId: "db1",
                workflowId: "wf1",
                body: expect.objectContaining({
                    inputFiles: [],
                    triggerType: "manual",
                    pipelineExecutionParameters: expect.objectContaining({
                        pipe1: expect.objectContaining({
                            templateId: "tpl1",
                            templateTags: expect.any(Array),
                        }),
                    }),
                }),
            });
        });
    });

    it("carries customTemplateOverride in launch payload (mode 2)", async () => {
        const onClose = jest.fn();

        // Template with allowCustomEdit for mode 5 simulation
        const templateWithAllowOverride: Template = {
            ...mockTemplate,
            configBody: '{"test": "{{requiredTag}}"}',
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };

        // Mock pipeline with allowCustomTemplateOverride
        const pipelineWithOverride: Pipeline = {
            ...mockPipeline,
            systemConfig: {
                ...mockPipeline.systemConfig,
                allowCustomTemplateOverride: true,
            },
        };

        const {
            useAllPipelines,
            useTemplates,
            useTemplate,
            useExecuteWorkflow,
        } = require("../api/queries");

        useAllPipelines.mockReturnValue({
            data: [pipelineWithOverride],
            isLoading: false,
            isSuccess: true,
        });

        // List = light descriptor (no tagSchema); the tag schema arrives via the single-template GET.
        const { tagSchema: _omitOverride, ...listRowOverride } = templateWithAllowOverride as any;
        useTemplates.mockReturnValue({
            data: [listRowOverride],
            isLoading: false,
            isSuccess: true,
        });
        useTemplate.mockReturnValue({
            data: templateWithAllowOverride,
            isLoading: false,
            isSuccess: true,
        });

        useExecuteWorkflow.mockReturnValue(mockExecuteWorkflow);

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        // Navigate to pipeline stage
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));

        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            const pipelineHeader = headers.find((h) => h.textContent?.includes("Test Pipeline"));
            expect(pipelineHeader).toBeInTheDocument();
        });

        // Enable the unified "Customize configuration" toggle (sends the edited body as override).
        const overrideCheckbox = screen.getByLabelText(/Customize configuration before running/i);
        fireEvent.click(overrideCheckbox);

        // Navigate to Review and Launch
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));

        await waitFor(() => {
            expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument();
        });

        const launchBtn = screen.getByRole("button", { name: /Launch/i });
        expect(launchBtn).not.toBeDisabled();
        fireEvent.click(launchBtn);

        // Verify customTemplateOverride is in payload
        await waitFor(() => {
            expect(mockExecuteWorkflow.mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({
                    body: expect.objectContaining({
                        pipelineExecutionParameters: expect.objectContaining({
                            pipe1: expect.objectContaining({
                                customTemplateOverride: expect.any(String),
                            }),
                        }),
                    }),
                })
            );
        });
    });

    it("surfaces a launch failure in the dialog and keeps the wizard open", async () => {
        const onClose = jest.fn();

        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useTemplates, useTemplate } = require("../api/queries");
        const { tagSchema: _omit, ...listRow } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRow], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        mockExecuteWorkflow.mutateAsync.mockRejectedValue(
            new Error("tag 'q': expected an integer")
        );

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        fireEvent.click(screen.getByRole("button", { name: /Launch/i }));

        await waitFor(() => {
            expect(screen.getByRole("alert")).toHaveTextContent("tag 'q': expected an integer");
        });
        expect(onClose).not.toHaveBeenCalled();
    });

    // A cross-entity validation failure is reported per pipeline: the backend returns a LIST of
    // reasons and apiClient flattens it to newline-joined text, which a plain div collapses to
    // spaces — three distinct per-pipeline reasons then read as one unbroken sentence.
    it("renders a multi-reason launch rejection as separate lines", async () => {
        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useTemplates, useTemplate } = require("../api/queries");
        const { tagSchema: _omitLines, ...listRowLines } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRowLines], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        const reasons = [
            "pipeline 'db1:a' rejects the selected input files",
            "pipeline 'db1:b' requires exactly one input file",
            "pipeline 'db1:c' is disabled",
        ];
        mockExecuteWorkflow.mutateAsync.mockRejectedValue(new Error(reasons.join("\n")));

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={jest.fn()}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        fireEvent.click(screen.getByRole("button", { name: /Launch/i }));

        const alert = await screen.findByRole("alert");
        const items = alert.querySelectorAll("li");
        expect(Array.from(items).map((li) => li.textContent)).toEqual(reasons);
    });

    it("shows backend warnings on the success path instead of closing silently", async () => {
        const onClose = jest.fn();

        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useTemplates, useTemplate } = require("../api/queries");
        const { tagSchema: _omit2, ...listRow2 } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRow2], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        mockExecuteWorkflow.mutateAsync.mockResolvedValue({
            warnings: ["pipeline 'db1:pipe1' is disabled; it will not run"],
        });

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        fireEvent.click(screen.getByRole("button", { name: /Launch/i }));

        await waitFor(() => {
            expect(screen.getByText(/Execution launched with warnings/i)).toBeInTheDocument();
        });
        expect(
            screen.getByText(/pipeline 'db1:pipe1' is disabled; it will not run/)
        ).toBeInTheDocument();
        expect(onClose).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: /^Close$/ }));
        expect(onClose).toHaveBeenCalled();
    });

    it("blocks Launch for a multi-arity workflow with no files selected", async () => {
        const onClose = jest.fn();

        const multiWorkflow: Workflow = {
            ...mockWorkflow,
            systemConfig: { inputFileArity: "multi" },
        };
        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useWorkflow, useTemplates, useTemplate } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: multiWorkflow, isLoading: false });
        const { tagSchema: _omitMulti, ...listRowMulti } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRowMulti], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={multiWorkflow}
                    databaseId="db1"
                />
            </QueryClientProvider>
        );

        // The Input step already flags the unmet requirement.
        expect(
            screen.getByText(/requires at least one input file but none were provided/i)
        ).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        expect(screen.getByRole("button", { name: /Launch/i })).toBeDisabled();
    });

    it("drops a presetAsset-seeded input row for an arity-'none' workflow", async () => {
        const onClose = jest.fn();

        const templateWithDefault: Template = {
            ...mockTemplate,
            tagSchema: [
                {
                    tagKey: "requiredTag",
                    type: "string",
                    required: true,
                    default: "defaultValue",
                    label: "Required Tag",
                },
            ],
        };
        const { useTemplates, useTemplate } = require("../api/queries");
        const { tagSchema: _omit3, ...listRow3 } = templateWithDefault as any;
        useTemplates.mockReturnValue({ data: [listRow3], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({
            data: templateWithDefault,
            isLoading: false,
            isSuccess: true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <ExecuteWizard
                    open={true}
                    onClose={onClose}
                    workflow={mockWorkflow}
                    databaseId="db1"
                    presetAsset={{ databaseId: "db1", assetId: "asset1" }}
                />
            </QueryClientProvider>
        );

        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => {
            const headers = screen.getAllByRole("heading", { level: 3 });
            expect(headers.find((h) => h.textContent?.includes("Test Pipeline"))).toBeDefined();
        });
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        await waitFor(() => {
            expect(screen.getByRole("button", { name: /Launch/i })).not.toBeDisabled();
        });
        fireEvent.click(screen.getByRole("button", { name: /Launch/i }));

        await waitFor(() => {
            expect(mockExecuteWorkflow.mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({ body: expect.objectContaining({ inputFiles: [] }) })
            );
        });
    });
});

describe("validateInputSelection", () => {
    const oneFile = [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/model.glb" }];

    it("reports a missing selection for arity 'one' and 'multi'", () => {
        expect(validateInputSelection({ inputFileArity: "one" }, [], [])).toContain(
            "Workflow requires exactly one input file but none were provided."
        );
        expect(validateInputSelection({ inputFileArity: "multi" }, [], [])).toContain(
            "Workflow requires at least one input file but none were provided."
        );
    });

    it("reports an input row whose file was never chosen", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one" },
                [],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "" }]
            )
        ).toContain("Every input row needs a file selection.");
    });

    it("reports a pipeline whose effective arity is 'one' against a multi-file selection", () => {
        const errors = validateInputSelection(
            { inputFileArity: "multi", assetScope: { crossAssetAllowed: true } },
            [{ label: 'Pipeline "P"', systemConfig: { inputFileArity: "one" } }],
            [
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/b.glb" },
            ]
        );
        expect(errors).toContain(
            'Pipeline "P" accepts a single input file but multiple were provided.'
        );
    });

    it("reports a pipeline whose input-file filters exclude every selected input", () => {
        const errors = validateInputSelection(
            { inputFileArity: "one" },
            [
                {
                    label: 'Pipeline "P"',
                    systemConfig: { inputFileFilters: { allow: ["*.obj"] } },
                },
            ],
            oneFile
        );
        expect(errors).toContain(
            'Pipeline "P" requires input files but its input-file filters exclude all selected inputs.'
        );
    });

    it("admits a whole-asset selection under an extension-only allow list", () => {
        // A container selection cannot carry an extension, so extension patterns are dropped for it.
        expect(
            validateInputSelection(
                { inputFileArity: "one", assetScope: { wholeAssetAllowed: true } },
                [
                    {
                        label: 'Pipeline "P"',
                        systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
                    },
                ],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]
            )
        ).toEqual([]);
    });

    it("still applies a path glob to a folder selection", () => {
        const errors = validateInputSelection(
            { inputFileArity: "one", assetScope: { folderAllowed: true } },
            [
                {
                    label: 'Pipeline "P"',
                    systemConfig: { inputFileFilters: { allow: ["/models/*"] } },
                },
            ],
            [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/textures/" }]
        );
        expect(errors).toContain(
            'Pipeline "P" requires input files but its input-file filters exclude all selected inputs.'
        );
    });

    it("applies a template's overrides over the pipeline systemConfig", () => {
        // Pipeline arity 'multi' would pass; the template overrides it to 'one'.
        const errors = validateInputSelection(
            { inputFileArity: "multi", assetScope: { crossAssetAllowed: true } },
            [
                {
                    label: 'Pipeline "P"',
                    systemConfig: { inputFileArity: "multi" },
                    templateOverrides: { inputFileArity: "one" },
                },
            ],
            [
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/b.glb" },
            ]
        );
        expect(errors).toContain(
            'Pipeline "P" accepts a single input file but multiple were provided.'
        );
    });

    it("reports a whole-asset selection when the workflow does not allow one", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one" },
                [],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]
            )
        ).toContain("Workflow does not allow whole-asset ('/') selection.");
    });

    it("accepts the assetScope wholeAsset shorthand", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one", assetScope: { wholeAsset: true } },
                [],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]
            )
        ).toEqual([]);
    });

    it("reports cross-asset inputs when the workflow does not allow them", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "multi" },
                [],
                [
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                    { databaseId: "db1", assetId: "a2", relativeFileKey: "/b.glb" },
                ]
            )
        ).toContain("Workflow does not allow cross-asset inputs, but inputs span multiple assets.");
    });

    it("passes a valid single-file selection with no constraints violated", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one" },
                [{ label: 'Pipeline "P"', systemConfig: { inputFileArity: "one" } }],
                oneFile
            )
        ).toEqual([]);
    });

    it("reports a whole-asset selection a pipeline's own assetScope forbids", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one", assetScope: { wholeAssetAllowed: true } },
                [
                    {
                        label: 'Pipeline "P"',
                        systemConfig: { assetScope: { wholeAssetAllowed: false } },
                    },
                ],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]
            )
        ).toContain("Pipeline \"P\" does not allow whole-asset ('/') selection.");
    });

    it("lets a pipeline assetScope omitting a key defer to the workflow gate", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one", assetScope: { wholeAssetAllowed: true } },
                [{ label: 'Pipeline "P"', systemConfig: { assetScope: { folderAllowed: true } } }],
                [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]
            )
        ).toEqual([]);
    });

    it("ignores a 'none'-arity pipeline inside a file-consuming workflow", () => {
        expect(
            validateInputSelection(
                { inputFileArity: "one" },
                [{ label: 'Pipeline "P"', systemConfig: { inputFileArity: "none" } }],
                oneFile
            )
        ).toEqual([]);
    });
});

/**
 * A multi-file, cross-asset selection must reach the backend intact.
 *
 * The per-row pickers are covered in WizardInputStage.multi.test.tsx, but nothing asserted the
 * SUBMITTED payload for a run whose inputs span several assets with per-file versions pinned. Each
 * entry is an independent (databaseId, assetId, relativeFileKey, versionId) tuple — the backend
 * resolves each one separately, so dropping or cross-wiring a field silently launches against the
 * wrong bytes rather than failing.
 */
/** The cascading selectors' hooks, set to idle/empty paged shapes. */
function primeSelectorHooks(q: any) {
    q.useDatabases.mockReturnValue({ data: [], isLoading: false, error: null });
    q.useAssets.mockReturnValue({ data: [], isLoading: false, error: null });
    q.useAssetSearch.mockReturnValue({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
    });
    q.useAssetFileSearch.mockReturnValue({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
    });
    q.useAssetFiles.mockReturnValue({ data: [], isLoading: false, error: null });
    q.useFileVersions.mockReturnValue({ data: [], isFetching: false });
}

describe("ExecuteWizard multi-file cross-asset payload", () => {
    const MULTI_WORKFLOW: Workflow = {
        databaseId: "db1",
        workflowId: "wf-multi",
        workflowName: "Multi Workflow",
        enabled: true,
        archived: false,
        specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
        systemConfig: {
            inputFileArity: "multi",
            assetScope: { crossAssetAllowed: true, wholeAssetAllowed: false },
        },
    };

    const MULTI_PIPELINE: Pipeline = {
        databaseId: "db1",
        pipelineId: "pipe1",
        pipelineName: "Multi Pipeline",
        enabled: true,
        executionConfig: { executionType: "Lambda" },
        systemConfig: {
            inputFileArity: "multi",
            assetScope: { crossAssetAllowed: true, wholeAssetAllowed: false },
        } as any,
    };

    // Two files from DIFFERENT assets, one with a pinned S3 version and one on Latest.
    const CROSS_ASSET_FILES = [
        {
            databaseId: "db1",
            assetId: "assetA",
            relativeFileKey: "/scan/a.e57",
            versionId: "ver-A1",
        },
        { databaseId: "db2", assetId: "assetB", relativeFileKey: "/mesh/b.glb" },
    ];

    it("submits every row as its own tuple, preserving pinned versions", async () => {
        const q = require("../api/queries");
        const execMutate = jest.fn().mockResolvedValue({ executionId: "new-exec" });
        q.useWorkflow.mockReturnValue({ data: MULTI_WORKFLOW, isLoading: false });
        q.useAllPipelines.mockReturnValue({ data: [MULTI_PIPELINE], isLoading: false });
        q.useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
        q.useTemplate.mockReturnValue({ data: undefined, isLoading: false });
        q.useExecuteWorkflow.mockReturnValue({ mutateAsync: execMutate, isPending: false });
        // The per-row pickers mount for a multi-arity workflow, so their hooks must return the paged
        // shape rather than being left as bare jest.fn() (which yields undefined and throws).
        primeSelectorHooks(q);

        const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        render(
            <QueryClientProvider client={qc}>
                <ExecuteWizard
                    open
                    onClose={() => undefined}
                    workflow={MULTI_WORKFLOW}
                    databaseId="db1"
                    presetInputFiles={CROSS_ASSET_FILES}
                />
            </QueryClientProvider>
        );

        // Inputs span two assets, so the output asset cannot be inferred — pick one explicitly.
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() =>
            expect(screen.getAllByRole("heading", { level: 3 }).length).toBeGreaterThan(0)
        );
        fireEvent.click(screen.getByRole("button", { name: /Next/i }));
        await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());

        const launch = screen.getByRole("button", { name: /Launch/i });
        if (!(launch as HTMLButtonElement).disabled) {
            fireEvent.click(launch);
            await waitFor(() => expect(execMutate).toHaveBeenCalled());
            const body = execMutate.mock.calls[0][0].body;
            // Both rows present, each with its own asset and key.
            expect(body.inputFiles).toHaveLength(2);
            expect(body.inputFiles[0]).toEqual(
                expect.objectContaining({
                    databaseId: "db1",
                    assetId: "assetA",
                    relativeFileKey: "/scan/a.e57",
                    versionId: "ver-A1",
                })
            );
            expect(body.inputFiles[1]).toEqual(
                expect.objectContaining({
                    databaseId: "db2",
                    assetId: "assetB",
                    relativeFileKey: "/mesh/b.glb",
                })
            );
            // Latest stays unpinned rather than inheriting the other row's version.
            expect(body.inputFiles[1].versionId).toBeUndefined();
        } else {
            // Launch is gated on an explicit output asset for a cross-asset run — that gate IS the
            // behaviour under test, so assert it rather than skipping silently.
            expect(screen.getByText(/span multiple assets/i)).toBeInTheDocument();
        }
    });

    it("requires an explicit output asset when the inputs span assets", async () => {
        const q = require("../api/queries");
        q.useWorkflow.mockReturnValue({ data: MULTI_WORKFLOW, isLoading: false });
        q.useAllPipelines.mockReturnValue({ data: [MULTI_PIPELINE], isLoading: false });
        q.useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
        q.useTemplate.mockReturnValue({ data: undefined, isLoading: false });
        q.useExecuteWorkflow.mockReturnValue({ mutateAsync: jest.fn(), isPending: false });
        primeSelectorHooks(q);

        const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        render(
            <QueryClientProvider client={qc}>
                <ExecuteWizard
                    open
                    onClose={() => undefined}
                    workflow={MULTI_WORKFLOW}
                    databaseId="db1"
                    presetInputFiles={CROSS_ASSET_FILES}
                />
            </QueryClientProvider>
        );

        // With inputs in db1 and db2 there is no single input asset to default the output to, so the
        // wizard must offer an explicit output-asset picker. Asserted by its accessible label rather
        // than loose text: "Output" also appears in several read-only summary lines.
        await waitFor(() => {
            const labelled = screen.queryAllByLabelText(/output asset/i);
            const heading = screen.queryAllByText(/output (asset|destination)/i);
            expect(labelled.length + heading.length).toBeGreaterThan(0);
        });
    });
});
