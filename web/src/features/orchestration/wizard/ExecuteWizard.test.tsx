/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard from "./ExecuteWizard";
import type { Workflow, Pipeline, Template } from "../types";

// Mock the API queries
jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useTemplates: jest.fn(),
    useExecuteWorkflow: jest.fn(),
    // WizardInputStage's cascading selectors call these; default to idle/empty so the wizard
    // renders. Individual tests can override if they exercise the input selectors.
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useAssets: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useAssetFiles: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useAssetVersions: jest.fn(() => ({ data: [], isLoading: false, error: null })),
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

    const mockPipeline: Pipeline = {
        databaseId: "db1",
        pipelineId: "pipe1",
        pipelineName: "Test Pipeline",
        enabled: true,
        executionConfig: {
            executionType: "Lambda",
        },
        systemConfig: {
            inputFileArity: "one",
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

        useTemplates.mockReturnValue({
            data: [mockTemplate],
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
            useExecuteWorkflow: mockUseExecuteWorkflow,
        } = require("../api/queries");

        mockUseTemplates.mockReturnValue({
            data: [templateWithDefault],
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

        const { useAllPipelines, useTemplates, useExecuteWorkflow } = require("../api/queries");

        useAllPipelines.mockReturnValue({
            data: [pipelineWithOverride],
            isLoading: false,
            isSuccess: true,
        });

        useTemplates.mockReturnValue({
            data: [templateWithAllowOverride],
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
});
