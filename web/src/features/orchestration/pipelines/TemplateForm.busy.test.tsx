/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a template save is in flight Save keeps keyboard focus and reads "Saving..."
 * (`aria-disabled`, not `disabled`), ignores a second press, and Cancel, Back and the Stepper's step
 * buttons do nothing; a rejected save leaves focus on Save.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplateForm from "./TemplateForm";
import type { Template } from "../types";

// The mutation reads whether it is in flight from here, so a test can flip it and re-render.
const save = { pending: false, mutateAsync: jest.fn() };

jest.mock("../api/queries", () => ({
    useTemplate: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
    usePipeline: jest.fn(() => ({ data: { pipelineName: "P1" } })),
    useTemplateMutations: jest.fn(),
}));
jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
    Link: ({ children }: any) => <span>{children}</span>,
}));
jest.mock("../components/ConfigEditor", () => {
    const ReactModule = jest.requireActual("react");
    const Stub = ReactModule.forwardRef(({ value }: any, ref: any) => {
        ReactModule.useImperativeHandle(ref, () => ({ insertAtCursor: jest.fn() }));
        return <textarea data-testid="config-editor" value={value} readOnly />;
    });
    Stub.displayName = "ConfigEditorStub";
    return { __esModule: true, default: Stub };
});

const { useTemplateMutations } = jest.requireMock("../api/queries");

const template = {
    pipelineDatabaseId: "db1",
    pipelineId: "p1",
    templateId: "t1",
    templateName: "Existing",
    description: "d",
    configFormat: "json",
    configBody: '{"a":1}',
    webFormJson: "[]",
    allowCustomEdit: false,
    inputInstructions: "",
    overrides: {},
    isDefault: false,
    tagSchema: [],
} as unknown as Template;

describe("TemplateForm while a save is in flight", () => {
    beforeEach(() => {
        save.pending = false;
        save.mutateAsync.mockReset();
        useTemplateMutations.mockImplementation(() => ({
            createTemplate: { mutateAsync: jest.fn(), isPending: false },
            updateTemplate: { mutateAsync: save.mutateAsync, isPending: save.pending },
        }));
    });

    it("keeps focus on Save, says so and ignores a second press, and leaves focus there after a rejection", async () => {
        const user = userEvent.setup({ delay: null });
        let reject: (reason: Error) => void = jest.fn();
        save.mutateAsync.mockImplementation(
            () =>
                new Promise((_resolve, fail) => {
                    reject = fail;
                })
        );
        const client = new QueryClient();
        const form = () => (
            <QueryClientProvider client={client}>
                <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={template} />
            </QueryClientProvider>
        );
        const { rerender } = render(form());
        for (let i = 0; i < 3; i++) {
            await user.click(screen.getByRole("button", { name: "Next" }));
        }
        const button = screen.getByRole("button", { name: "Save" });
        button.focus();
        await user.keyboard("{Enter}");
        expect(save.mutateAsync).toHaveBeenCalledTimes(1);

        save.pending = true;
        rerender(form());
        expect(button).toHaveAccessibleName("Saving...");
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).not.toBeDisabled();
        expect(button).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(save.mutateAsync).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );

        // The Stepper's step buttons are held like Back: a press keeps Review and "Saving...", so
        // the screen still shows the values being saved.
        const steps = ["Basic", "Pipeline overrides", "Tags and Config Body"].map((label) =>
            screen.getByRole("button", { name: `Go to step ${label}` })
        );
        for (const step of steps) {
            expect(step).toHaveAttribute("aria-disabled", "true");
            expect(step).not.toBeDisabled();
            await user.click(step);
            step.focus();
            await user.keyboard("{Enter}");
            expect(step).toHaveFocus();
            expect(screen.getByRole("button", { name: "Saving..." })).toBe(button);
            expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
        }
        button.focus();

        await act(async () => reject(new Error("rejected")));
        save.pending = false;
        rerender(form());
        expect(button).toHaveFocus();
        expect(button).toHaveAccessibleName("Save");
        expect(button).not.toHaveAttribute("aria-disabled");
    });
});
