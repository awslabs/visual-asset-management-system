/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TriggerDraftsEditor from "./TriggerDraftsEditor";
import type { WorkflowTrigger } from "../types";

// Only the form's template dropdown reads a query hook. `useTriggers` is deliberately absent from
// the factory: a draft editor that read the live list would throw on render.
jest.mock("../api/queries", () => ({
    useTemplates: jest.fn(() => ({
        data: [{ templateId: "t1", templateName: "Template One" }],
        isLoading: false,
    })),
}));

const pipelineRefs = [{ pipelineId: "p1", pipelineDatabaseId: "db1" }];

// No QueryClientProvider: a mutation or query-client call anywhere below would throw.
const renderDrafts = (drafts: WorkflowTrigger[]) => {
    const onChange = jest.fn();
    render(<TriggerDraftsEditor drafts={drafts} onChange={onChange} pipelineRefs={pipelineRefs} />);
    return onChange;
};

describe("TriggerDraftsEditor", () => {
    beforeEach(() => jest.clearAllMocks());

    it("adds a draft and reports the new list through onChange", async () => {
        const onChange = renderDrafts([]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.type(await screen.findByLabelText("Trigger name"), "nightly");
        await userEvent.selectOptions(screen.getByRole("combobox"), "t1");
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

        expect(onChange).toHaveBeenCalledWith([
            {
                triggerType: "fileUpload#nightly",
                enabled: true,
                inputFileFilters: { allow: [], exclude: [] },
                defaultTemplateIds: { "db1:p1": "t1" },
            },
        ]);
        // Back on the list after saving the draft.
        expect(screen.queryByLabelText("Trigger name")).not.toBeInTheDocument();
    });

    it("edits a draft in place under its own key", async () => {
        const onChange = renderDrafts([
            { triggerType: "fileUpload", enabled: true, inputFileFilters: { allow: ["*.glb"] } },
        ]);

        await userEvent.click(screen.getByRole("button", { name: "Edit trigger fileUpload" }));
        await userEvent.click(screen.getByRole("checkbox"));
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

        expect(onChange).toHaveBeenCalledWith([
            {
                triggerType: "fileUpload",
                enabled: false,
                inputFileFilters: { allow: ["*.glb"], exclude: [] },
                defaultTemplateIds: {},
            },
        ]);
    });

    it("removes a draft without a confirm dialog", async () => {
        const onChange = renderDrafts([{ triggerType: "fileUpload", enabled: true }]);

        await userEvent.click(screen.getByRole("button", { name: "Delete trigger fileUpload" }));

        expect(onChange).toHaveBeenCalledWith([]);
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("refuses a key already drafted", async () => {
        const onChange = renderDrafts([{ triggerType: "fileUpload#nightly", enabled: true }]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.type(await screen.findByLabelText("Trigger name"), "nightly");

        expect(screen.getByText(/already has a trigger with that name/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
        expect(onChange).not.toHaveBeenCalled();
    });

    it("says drafts are written once the workflow is created", () => {
        renderDrafts([]);
        expect(screen.getByText(/written once the workflow is created/i)).toBeInTheDocument();
    });
});
