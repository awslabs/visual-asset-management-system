/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TriggerForm from "./TriggerForm";
import { emptyDraft } from "./triggerDraft";

jest.mock("../api/queries", () => ({
    useTemplates: jest.fn(() => ({
        data: [{ templateId: "t1", templateName: "Template One" }],
        isLoading: false,
    })),
}));

const pipelineRefs = [{ pipelineId: "p1", pipelineDatabaseId: "db1" }];

const renderForm = (over: Partial<React.ComponentProps<typeof TriggerForm>> = {}) => {
    const props = {
        draft: emptyDraft("fileUpload"),
        onChange: jest.fn(),
        onSave: jest.fn(),
        onCancel: jest.fn(),
        isSaving: false,
        saveError: null,
        pipelineRefs,
        existingKeys: [] as string[],
        ...over,
    };
    render(<TriggerForm {...props} />);
    return props;
};

describe("TriggerForm", () => {
    it("reports every edit through onChange without mutating the draft it was given", () => {
        const { onChange, draft } = renderForm();

        fireEvent.change(screen.getByLabelText("Trigger name"), { target: { value: "nightly" } });

        expect(onChange).toHaveBeenCalledWith({ ...draft, triggerId: "nightly" });
        expect(draft.triggerId).toBe("");
    });

    it("selects a default template by name and stores its id under the pipeline composite", async () => {
        const { onChange, draft } = renderForm();

        await userEvent.selectOptions(screen.getByRole("combobox"), "t1");

        expect(onChange).toHaveBeenCalledWith({ ...draft, defaultTemplateIds: { "db1:p1": "t1" } });
    });

    it("refuses a taken key: message shown, Save disabled, onSave not called", async () => {
        const { onSave } = renderForm({
            draft: { ...emptyDraft("fileUpload"), triggerId: "nightly" },
            existingKeys: ["fileUpload#nightly"],
        });

        expect(screen.getByText(/already has a trigger with that name/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
        expect(onSave).not.toHaveBeenCalled();
    });

    it("rejects a malformed name and shows the server's message inline", () => {
        renderForm({
            draft: { ...emptyDraft("fileUpload"), triggerId: "a b" },
            saveError: "Another trigger of this type already uses the same default templates",
        });

        expect(screen.getByText(/letters, numbers, hyphens and underscores/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
        expect(screen.getByText(/already uses the same default templates/)).toBeInTheDocument();
    });

    it("locks the name while editing, because it addresses the row", () => {
        renderForm({
            draft: {
                ...emptyDraft("fileUpload"),
                triggerId: "nightly",
                editingKey: "fileUpload#nightly",
            },
        });

        expect(screen.getByLabelText("Trigger name")).toBeDisabled();
        expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
            /^Edit file upload trigger$/
        );
    });
});
