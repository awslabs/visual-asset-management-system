/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a workflow save is in flight Save keeps keyboard focus and reads "Saving..."
 * (`aria-disabled`, not `disabled`), ignores a second press, and Cancel and Back do nothing. A
 * create answered with warnings and no workflow id withdraws Save; focus moves to Continue.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WorkflowBuilder from "./WorkflowBuilder";

// Each hook answers with the same object on every render, as a settled query does; a fresh array
// per render would re-run the builder's validation effect without end.
jest.mock("../api/queries", () => {
    const empty = { data: [] };
    return {
        useAllPipelines: jest.fn(() => empty),
        useWorkflow: jest.fn(() => ({ data: undefined })),
        useWorkflowMutations: jest.fn(),
        useTriggers: jest.fn(() => empty),
        useTemplates: jest.fn(() => empty),
        usePrefetchPipelineTemplates: jest.fn(),
    };
});
jest.mock("../permissions/useAllowedRoutes", () => {
    const routes = { loading: false, can: () => true };
    return { useAllowedRoutes: () => routes };
});
jest.mock("../api/workflows", () => ({ setTrigger: jest.fn(), deleteTrigger: jest.fn() }));
jest.mock("./PipelineOrderList", () => ({
    __esModule: true,
    default: ({ onChange }: any) => (
        <button onClick={() => onChange([{ pipelineId: "p1", pipelineDatabaseId: "db1" }])}>
            Add Pipeline
        </button>
    ),
}));
jest.mock("./DagPreview", () => ({ __esModule: true, default: () => null }));
jest.mock("./workflowValidation", () => ({
    validateWorkflow: jest.fn(() => ({ errors: [], warnings: [] })),
    allPipelineRefsSelected: jest.fn(() => true),
}));
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));

const { useWorkflowMutations } = jest.requireMock("../api/queries");

describe("WorkflowBuilder while a save is in flight", () => {
    it("keeps focus on Save, says so and ignores a second press, then hands focus to Continue", async () => {
        const user = userEvent.setup({ delay: null });
        let answer: (value: unknown) => void = jest.fn();
        const create = jest.fn(
            () =>
                new Promise((resolve) => {
                    answer = resolve;
                })
        );
        const mutations = {
            createWorkflow: { mutateAsync: create },
            updateWorkflow: { mutateAsync: jest.fn() },
            archiveWorkflow: { mutateAsync: jest.fn() },
        };
        useWorkflowMutations.mockReturnValue(mutations);
        render(
            <QueryClientProvider client={new QueryClient()}>
                <MemoryRouter>
                    <WorkflowBuilder mode="create" databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );
        await user.click(await screen.findByLabelText(/workflow name/i));
        await user.paste("My Workflow");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(await screen.findByText("Add Pipeline"));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        const save = await screen.findByRole("button", { name: "Save" });
        save.focus();
        await user.keyboard("{Enter}");

        await waitFor(() => expect(save).toHaveAccessibleName("Saving..."));
        expect(save).toHaveAttribute("aria-disabled", "true");
        expect(save).not.toBeDisabled();
        expect(save).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(create).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );

        await act(async () => answer({ warnings: ["arity mismatch"] }));
        await screen.findByText(/arity mismatch/);
        expect(screen.queryByRole("button", { name: /^save/i })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Continue" })).toHaveFocus();
        expect(mockNavigate).not.toHaveBeenCalled();
    });
});
