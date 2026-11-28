/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The trigger form's Save keeps keyboard focus while the trigger is written (`aria-disabled`, not
 * `disabled`, reading "Saving..."), and when the write closes the form focus moves to the list
 * rather than to the page body.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TriggersEditor from "./TriggersEditor";
import type { WorkflowTrigger } from "../types";

jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
}));
jest.mock("../api/workflows", () => ({ setTrigger: jest.fn(), deleteTrigger: jest.fn() }));
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const { useTriggers } = jest.requireMock("../api/queries");
const { setTrigger } = jest.requireMock("../api/workflows");

const stored = {
    triggerType: "fileUpload",
    triggerBaseType: "fileUpload",
    triggerId: "",
    enabled: true,
} as WorkflowTrigger;

const renderEditor = (triggers: WorkflowTrigger[]) => {
    useTriggers.mockReturnValue({ data: triggers, isLoading: false });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
        <QueryClientProvider client={queryClient}>
            <TriggersEditor
                databaseId="db1"
                workflowId="wf-1"
                pipelineRefs={[{ pipelineId: "p1", pipelineDatabaseId: "db1" }]}
            />
        </QueryClientProvider>
    );
};

/** A write the test answers when it chooses. */
const heldWrite = () => {
    let answer: (value: unknown) => void = jest.fn();
    setTrigger.mockImplementation(
        () =>
            new Promise((resolve) => {
                answer = resolve;
            })
    );
    return () => act(async () => answer([true, {}]));
};

describe("TriggersEditor Save while the trigger is written", () => {
    beforeEach(() => jest.clearAllMocks());

    it("keeps focus on Save, says so and ignores a second press, then hands focus to the trigger's Edit", async () => {
        const user = userEvent.setup({ delay: null });
        const answer = heldWrite();
        renderEditor([stored]);
        await user.click(screen.getByRole("button", { name: "Edit trigger fileUpload" }));
        const save = screen.getByRole("button", { name: "Save" });
        save.focus();
        await user.keyboard("{Enter}");

        expect(save).toHaveAccessibleName("Saving...");
        expect(save).toHaveAttribute("aria-disabled", "true");
        expect(save).not.toBeDisabled();
        expect(save).toHaveFocus();
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.keyboard("{Enter}");
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        expect(setTrigger).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Saving..." })).toBeInTheDocument();

        await answer();
        expect(screen.getByRole("button", { name: "Edit trigger fileUpload" })).toHaveFocus();
    });

    it("hands focus to the Add button of the type while the list re-reads a trigger just added", async () => {
        const user = userEvent.setup({ delay: null });
        const answer = heldWrite();
        renderEditor([]);
        await user.click(screen.getByRole("button", { name: /^Add .* trigger$/ }));
        screen.getByRole("button", { name: "Save" }).focus();
        await user.keyboard("{Enter}");
        await answer();
        expect(screen.getByRole("button", { name: /^Add .* trigger$/ })).toHaveFocus();
    });
});
