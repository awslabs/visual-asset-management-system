/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a pipeline save is in flight the form holds: Save keeps keyboard focus and reads
 * "Saving..." (`aria-disabled`, not `disabled`) and cancels a press, Cancel does nothing, and the
 * dialog does not close. A save answered with warnings hands focus to Acknowledge.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";

jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
}));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

const { useCreatePipeline } = jest.requireMock("../api/queries");

describe("PipelineForm while a save is in flight", () => {
    it("holds the dialog with Save focused and reading Saving..., then hands focus to Acknowledge", async () => {
        const user = userEvent.setup({ delay: null });
        let answer: (value: unknown) => void = jest.fn();
        useCreatePipeline.mockReturnValue({
            mutateAsync: jest.fn(
                () =>
                    new Promise((resolve) => {
                        answer = resolve;
                    })
            ),
        });
        const onDone = jest.fn();
        render(
            <QueryClientProvider client={new QueryClient()}>
                <PipelineForm mode="create" databaseId="db1" onDone={onDone} />
            </QueryClientProvider>
        );
        await user.click(screen.getByLabelText(/Pipeline Name/));
        await user.paste("New Pipe");
        const save = screen.getByRole("button", { name: "Create" });
        save.focus();
        // jsdom does not submit through a footer button's form= association; submit the form.
        fireEvent.submit(document.getElementById("pipeline-form") as HTMLFormElement);

        await waitFor(() => expect(save).toHaveAccessibleName("Saving..."));
        expect(save).toHaveAttribute("aria-disabled", "true");
        expect(save).not.toBeDisabled();
        expect(save).toHaveFocus();
        // A press is cancelled, which is what keeps a submit button from submitting again.
        expect(fireEvent.click(save)).toBe(false);
        const cancel = screen.getByRole("button", { name: "Cancel" });
        expect(cancel).toHaveAttribute("aria-disabled", "true");
        await user.keyboard("{Escape}");
        await user.click(screen.getByRole("button", { name: "Close dialog" }));
        expect(onDone).not.toHaveBeenCalled();

        save.focus();
        await act(async () => answer({ pipeline: { pipelineId: "p1" }, warnings: ["heads up"] }));
        await screen.findByText(/Pipeline saved with warnings/);
        await act(() => Promise.resolve());
        expect(screen.getByRole("button", { name: "Acknowledge" })).toHaveFocus();
    });
});
