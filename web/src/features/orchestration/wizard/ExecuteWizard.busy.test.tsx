/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a launch is in flight the execute wizard holds: Launch keeps keyboard focus and reads
 * "Launching..." (`aria-disabled`, not `disabled`); Back, the rail's rows and the Review step's
 * Edit links are held the same way and do not leave Review; and the dialog does not close. A launch
 * answered with warnings hands focus to Close.
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard from "./ExecuteWizard";
import type { Pipeline, Template, Workflow } from "../types";

jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useTemplates: jest.fn(),
    useTemplate: jest.fn(),
    usePrefetchPipelineTemplates: jest.fn(),
    useExecuteWorkflow: jest.fn(),
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useAssetSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
        error: null,
    })),
    useAssetFileSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
        error: null,
    })),
    useFileVersions: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));
jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

const queries = jest.requireMock("../api/queries");

const workflow: Workflow = {
    databaseId: "db1",
    workflowId: "wf1",
    workflowName: "Test Workflow",
    enabled: true,
    archived: false,
    specifiedPipelines: [
        { pipelineId: "pipe1", pipelineDatabaseId: "db1", defaultTemplateId: "tpl1" },
    ],
    systemConfig: { inputFileArity: "none" },
};
const pipeline: Pipeline = {
    databaseId: "db1",
    pipelineId: "pipe1",
    pipelineName: "Test Pipeline",
    enabled: true,
    executionConfig: { executionType: "Lambda" },
    systemConfig: { inputFileArity: "none" },
};
const template: Template = {
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
            default: "defaultValue",
            label: "Required Tag",
        },
    ],
};

// The launch mutation reads whether it is in flight from here, so a test can flip it and re-render.
const launch = { pending: false, mutateAsync: jest.fn() };

describe("ExecuteWizard while a launch is in flight", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        launch.pending = false;
        queries.useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
        queries.useAllPipelines.mockReturnValue({
            data: [pipeline],
            isLoading: false,
            isSuccess: true,
        });
        // The list endpoint omits tagSchema; the single-template read carries it.
        const listRow = { ...template, tagSchema: undefined };
        queries.useTemplates.mockReturnValue({
            data: [listRow],
            isLoading: false,
            isSuccess: true,
        });
        queries.useTemplate.mockReturnValue({ data: template, isLoading: false, isSuccess: true });
        queries.useExecuteWorkflow.mockImplementation(() => ({
            mutateAsync: launch.mutateAsync,
            isPending: launch.pending,
        }));
    });

    it("holds on Review with Launch focused and reading Launching..., then hands focus to Close", async () => {
        const user = userEvent.setup({ delay: null });
        let answer: (value: unknown) => void = jest.fn();
        launch.mutateAsync.mockImplementation(
            () =>
                new Promise((resolve) => {
                    answer = resolve;
                })
        );
        const onClose = jest.fn();
        const client = new QueryClient();
        const wizard = () => (
            <QueryClientProvider client={client}>
                <ExecuteWizard open onClose={onClose} workflow={workflow} databaseId="db1" />
            </QueryClientProvider>
        );
        const { rerender } = render(wizard());
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(await screen.findByRole("button", { name: "Next" }));
        await screen.findByText(/Review & Launch/i);
        const launchButton = screen.getByRole("button", { name: "Launch" });
        await waitFor(() => expect(launchButton).not.toBeDisabled());
        launchButton.focus();
        await user.keyboard("{Enter}");
        expect(launch.mutateAsync).toHaveBeenCalledTimes(1);

        launch.pending = true;
        rerender(wizard());
        expect(launchButton).toHaveAccessibleName("Launching...");
        expect(launchButton).toHaveAttribute("aria-disabled", "true");
        expect(launchButton).not.toBeDisabled();
        expect(launchButton).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        expect(launch.mutateAsync).toHaveBeenCalledTimes(1);
        expect(onClose).not.toHaveBeenCalled();
        expect(screen.getByRole("button", { name: "Close dialog" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );

        const back = screen.getByRole("button", { name: "Back" });
        expect(back).toHaveAttribute("aria-disabled", "true");
        await user.click(back);
        expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Launching..." })).toBeInTheDocument();

        // The rail's visited rows and the Review step's Edit links are held like Back: focusable,
        // marked unavailable, and a press (click, Enter or Space) stays on Review.
        const railRows = within(
            screen.getByRole("navigation", { name: "Execution steps" })
        ).getAllByRole("button");
        const editLinks = screen.getAllByRole("button", { name: /^Edit / });
        expect(railRows).toHaveLength(2);
        expect(editLinks.map((link) => link.getAttribute("aria-label"))).toEqual([
            "Edit Inputs",
            "Edit Output Target",
            "Edit Test Pipeline",
        ]);
        for (const held of [...railRows, ...editLinks]) {
            expect(held).toHaveAttribute("aria-disabled", "true");
            expect(held).not.toBeDisabled();
            await user.click(held);
            held.focus();
            await user.keyboard("{Enter}");
            await user.keyboard(" ");
            expect(held).toHaveFocus();
            expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument();
            expect(screen.getByRole("button", { name: "Launching..." })).toBeInTheDocument();
        }
        back.focus();

        // Answered with warnings: the footer becomes Close, which takes the focus Back held.
        await act(async () => answer({ warnings: ["pipeline 'db1:pipe1' is disabled"] }));
        launch.pending = false;
        rerender(wizard());
        await act(() => Promise.resolve());
        expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    });
});
