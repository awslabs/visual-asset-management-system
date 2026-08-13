/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The triggers editor is a LIST keyed by trigger key, not a single form: a workflow may carry several
 * triggers of one type, each with its own filters and default templates, and an upload runs the workflow
 * once per matching trigger. The types it offers come from TRIGGER_TYPES, so adding a type there
 * surfaces it with no change to the component — `fileUpload` is simply the only one implemented today.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TriggersEditor from "./TriggersEditor";
import type { WorkflowTrigger } from "../types";
import { TRIGGER_TYPES } from "../types";

jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
}));

jest.mock("../api/workflows", () => ({
    setTrigger: jest.fn(),
    deleteTrigger: jest.fn(),
}));

const pipelineRefs = [{ pipelineId: "p1", pipelineDatabaseId: "db1" }];

const renderEditor = (triggers: WorkflowTrigger[], isLoading = false) => {
    const { useTriggers } = require("../api/queries");
    useTriggers.mockReturnValue({ data: triggers, isLoading });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries");
    const rendered = render(
        <QueryClientProvider client={queryClient}>
            <TriggersEditor databaseId="db1" workflowId="wf-1" pipelineRefs={pipelineRefs} />
        </QueryClientProvider>
    );
    return { ...rendered, invalidateQueries };
};

/** The query keys a mutation invalidated, as arrays. */
const invalidatedKeys = (spy: jest.SpyInstance) =>
    spy.mock.calls.map((call) => (call[0] as any)?.queryKey);

const trigger = (over: Partial<WorkflowTrigger> = {}): WorkflowTrigger =>
    ({
        triggerType: "fileUpload",
        triggerBaseType: "fileUpload",
        triggerId: "",
        enabled: true,
        ...over,
    } as WorkflowTrigger);

describe("TriggersEditor list loading", () => {
    beforeEach(() => jest.clearAllMocks());

    it("does not claim a workflow has no triggers while the list is still loading", () => {
        // The query defaults to [], so a length check alone reports an in-flight list as an empty
        // one — telling the reader this workflow fires on nothing when it may have several triggers.
        renderEditor([], true);
        expect(screen.queryByText("No triggers configured")).not.toBeInTheDocument();
        expect(screen.getByText(/Loading triggers/i)).toBeInTheDocument();
    });

    it("shows the empty state once the load finishes with no triggers", () => {
        renderEditor([], false);
        expect(screen.getByText("No triggers configured")).toBeInTheDocument();
        expect(screen.queryByText(/Loading triggers/i)).not.toBeInTheDocument();
    });
});

describe("TriggersEditor", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { setTrigger, deleteTrigger } = require("../api/workflows");
        setTrigger.mockResolvedValue([true, {}]);
        deleteTrigger.mockResolvedValue([true, {}]);
    });

    it("creates a trigger enabled, matching the backend default", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        // enabled:false would be stored verbatim and _trigger_fires() would never fire.
        expect(setTrigger.mock.calls[0][3].enabled).toBe(true);
        // No name given, so this is the workflow's FIRST trigger of the type and takes the bare key —
        // which is what every trigger stored before multiple triggers existed already uses.
        expect(setTrigger.mock.calls[0][2]).toBe("fileUpload");
    });

    it("drops default templates for pipelines no longer in the workflow", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([trigger({ defaultTemplateIds: { "db1:p1": "t1", "db1:removed": "t9" } })]);

        await userEvent.click(await screen.findByRole("button", { name: /edit trigger/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        expect(setTrigger.mock.calls[0][3].defaultTemplateIds).toEqual({ "db1:p1": "t1" });
    });

    it("lists every trigger of a type, not just the first", async () => {
        renderEditor([
            trigger({ inputFileFilters: { allow: ["*.glb"] } }),
            trigger({
                triggerType: "fileUpload#nightly",
                triggerId: "nightly",
                inputFileFilters: { allow: ["*.obj"] },
            }),
        ]);
        // Both rows, each with its own filters — the point of the feature.
        expect(await screen.findByText("*.glb")).toBeInTheDocument();
        expect(screen.getByText("*.obj")).toBeInTheDocument();
        expect(screen.getByText("nightly")).toBeInTheDocument();
        expect(screen.getByText(/first of type/i)).toBeInTheDocument();
        expect(screen.getAllByRole("button", { name: /edit trigger/i })).toHaveLength(2);
    });

    it("adds a second trigger of a type under a suffixed key", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([trigger()]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.type(await screen.findByLabelText("Trigger name"), "nightly");
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        // The key carries the id; the bare key would have REPLACED the existing trigger.
        expect(setTrigger.mock.calls[0][2]).toBe("fileUpload#nightly");
        expect(setTrigger.mock.calls[0][3].triggerType).toBe("fileUpload#nightly");
    });

    it("refuses to add under a key that is already taken", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([trigger({ triggerType: "fileUpload#nightly", triggerId: "nightly" })]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.type(await screen.findByLabelText("Trigger name"), "nightly");

        // A taken key would REPLACE that sibling rather than add a trigger, so the save is blocked
        // rather than silently overwriting it.
        expect(screen.getByText(/already has a trigger with that name/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
        expect(setTrigger).not.toHaveBeenCalled();
    });

    it("rejects a malformed trigger name before it reaches the API", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.type(await screen.findByLabelText("Trigger name"), "a b");

        expect(screen.getByText(/letters, numbers, hyphens and underscores/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
        await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
        expect(setTrigger).not.toHaveBeenCalled();
    });

    it("cannot change the name while editing, because it addresses the row", async () => {
        renderEditor([trigger({ triggerType: "fileUpload#nightly", triggerId: "nightly" })]);
        await userEvent.click(await screen.findByRole("button", { name: /edit trigger/i }));
        expect(await screen.findByLabelText("Trigger name")).toBeDisabled();
    });

    it("deletes the trigger the row names, not a hard-coded type", async () => {
        const { deleteTrigger } = require("../api/workflows");
        renderEditor([
            trigger(),
            trigger({ triggerType: "fileUpload#nightly", triggerId: "nightly" }),
        ]);

        await userEvent.click(
            await screen.findByRole("button", { name: /delete trigger fileUpload#nightly/i })
        );
        // The dialog names the trigger: with several of one type, "this trigger" would not say which.
        expect(await screen.findByText(/fileUpload#nightly/)).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: /^delete$/i, hidden: false }));

        await waitFor(() => expect(deleteTrigger).toHaveBeenCalled());
        expect(deleteTrigger.mock.calls[0][2]).toBe("fileUpload#nightly");
    });

    it("offers an add button for every configurable trigger type", () => {
        renderEditor([]);
        // Driven by TRIGGER_TYPES so a future type needs no change here or in the component.
        for (const t of TRIGGER_TYPES) {
            expect(
                screen.getByRole("button", { name: new RegExp(`add ${t.label} trigger`, "i") })
            ).toBeInTheDocument();
        }
    });

    it("says a workflow with no triggers runs only when started explicitly", () => {
        renderEditor([]);
        expect(screen.getByText(/no triggers configured/i)).toBeInTheDocument();
        expect(screen.getByText(/started explicitly/i)).toBeInTheDocument();
    });
});

describe("TriggersEditor cache invalidation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { setTrigger, deleteTrigger } = require("../api/workflows");
        setTrigger.mockResolvedValue([true, {}]);
        deleteTrigger.mockResolvedValue([true, {}]);
    });

    // triggerCount/triggersEnabledCount are computed server-side per LIST row and drive the workflow
    // cards and the trigger facet, so a trigger write that invalidates only this list leaves the
    // workflows page contradicting the trigger just saved for as long as its data stays fresh.
    it("invalidates the workflow queries when a trigger is saved", async () => {
        const { setTrigger } = require("../api/workflows");
        const { invalidateQueries } = renderEditor([]);

        await userEvent.click(screen.getByRole("button", { name: /add file upload trigger/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        await waitFor(() =>
            expect(invalidatedKeys(invalidateQueries)).toEqual(
                expect.arrayContaining([["workflows"]])
            )
        );
        expect(invalidatedKeys(invalidateQueries)).toEqual(
            expect.arrayContaining([
                ["triggers", "db1", "wf-1"],
                ["workflow", "db1", "wf-1"],
            ])
        );
    });

    it("invalidates the workflow queries when a trigger is deleted", async () => {
        const { deleteTrigger } = require("../api/workflows");
        const { invalidateQueries } = renderEditor([trigger()]);

        await userEvent.click(
            await screen.findByRole("button", { name: /delete trigger fileUpload/i })
        );
        await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

        await waitFor(() => expect(deleteTrigger).toHaveBeenCalled());
        await waitFor(() =>
            expect(invalidatedKeys(invalidateQueries)).toEqual(
                expect.arrayContaining([["workflows"]])
            )
        );
        expect(invalidatedKeys(invalidateQueries)).toEqual(
            expect.arrayContaining([
                ["triggers", "db1", "wf-1"],
                ["workflow", "db1", "wf-1"],
            ])
        );
    });
});
