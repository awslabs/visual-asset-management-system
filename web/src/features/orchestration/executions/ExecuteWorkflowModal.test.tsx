/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecuteWorkflowModal from "./ExecuteWorkflowModal";

jest.mock("../api/queries", () => ({
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined, isLoading: false })),
    useExecuteWorkflow: jest.fn(() => ({ mutateAsync: jest.fn() })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAssetSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useAssetFileSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useFileVersions: jest.fn(() => ({ data: [] })),
    usePipelineTemplates: jest.fn(() => ({ data: [] })),
}));

// The component calls useAllWorkflows twice (the scope's database, then GLOBAL) and concatenates the
// results, so a mock returning the same list for both yields a duplicate option. Return the workflow
// only for the GLOBAL call, which is what a GLOBAL-owned workflow actually looks like.
const workflowsByDatabase = (workflows: any[]) => (databaseId?: string) => ({
    data: databaseId === "GLOBAL" ? workflows : [],
});
// The wizard component is NOT mocked. The modal imports validateInputSelection from that module, so
// stubbing it would remove the very validation these tests assert on. The wizard only renders once
// Continue is pressed, which these tests do not do — so the real module is cheap to leave in place.

const queries = () => require("../api/queries");

const WORKFLOW = {
    databaseId: "db1",
    workflowId: "wf1",
    workflowName: "Convert",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineDatabaseId: "GLOBAL", pipelineId: "conv" }],
    systemConfig: {
        inputFileArity: "one",
        inputFileFilters: {},
        metadataInputs: {},
        outputTarget: { locationType: "asset" },
    },
};

const PIPELINE = {
    databaseId: "GLOBAL",
    pipelineId: "conv",
    systemConfig: { inputFileFilters: { allow: ["*.glb", "*.obj"] } },
};

/** Open the dialog and choose the workflow. */
async function pickWorkflow() {
    await userEvent.click(await screen.findByLabelText("Workflow"));
    await userEvent.click(await screen.findByRole("option", { name: /Convert/ }));
}

describe("ExecuteWorkflowModal", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAllWorkflows.mockImplementation(workflowsByDatabase([WORKFLOW]));
        queries().useAllPipelines.mockReturnValue({ data: [PIPELINE] });
    });

    it("summarizes what the selected workflow accepts, resolved from its pipelines", async () => {
        // The workflow itself declares no filters, so the restriction has to come from the pipeline
        // it references — which is why the picker loads pipelines at all.
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        expect(
            await screen.findByText(/2 file types · 1 file · writes to an asset/)
        ).toBeInTheDocument();
    });

    it("keeps the summary compact — no pattern list in the picker", async () => {
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        await screen.findByText(/2 file types/);
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
    });

    it("says the summary may narrow when a step requires a template", async () => {
        queries().useAllPipelines.mockReturnValue({
            data: [
                { ...PIPELINE, systemConfig: { ...PIPELINE.systemConfig, requireTemplate: true } },
            ],
        });
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        expect(await screen.findByText(/may narrow once a template is chosen/)).toBeInTheDocument();
    });

    // ---- Launching from a known selection (the asset file manager's Automation action) ----

    it("blocks a workflow that cannot accept the supplied selection", async () => {
        // The whole point of launching from a selection: the mismatch is caught HERE, not two steps
        // later. This workflow's pipeline accepts only .glb/.obj, so a .txt cannot run.
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                databaseId="db1"
                assetId="a1"
                presetInputFiles={[
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/notes.txt" },
                ]}
            />
        );
        await pickWorkflow();
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
    });

    it("allows a workflow that accepts the supplied selection", async () => {
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                databaseId="db1"
                assetId="a1"
                presetInputFiles={[
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/pump.glb" },
                ]}
            />
        );
        await pickWorkflow();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();
    });

    it("rejects a multi-file selection against a single-file workflow", async () => {
        // Arity is part of the up-front check, not just the filters.
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                databaseId="db1"
                assetId="a1"
                presetInputFiles={[
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/b.glb" },
                ]}
            />
        );
        await pickWorkflow();
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        // Both the workflow gate and the pipeline report it, so there is more than one message.
        expect(screen.getAllByText(/single input file/i).length).toBeGreaterThan(0);
    });

    it("rejects a whole-asset selection when the workflow disallows one", async () => {
        // A '/' selection is checked against the assetScope gates, which default to disallowing it.
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                databaseId="db1"
                assetId="a1"
                presetInputFiles={[{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]}
            />
        );
        await pickWorkflow();
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        expect(screen.getByText(/whole-asset/i)).toBeInTheDocument();
    });

    it("summarizes the selection it will run on", async () => {
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                databaseId="db1"
                assetId="a1"
                presetInputFiles={[
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/b.glb" },
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/c.glb" },
                    { databaseId: "db1", assetId: "a1", relativeFileKey: "/d.glb" },
                ]}
            />
        );
        expect(await screen.findByText(/Running on 4 selections/)).toBeInTheDocument();
        // Long selections are truncated rather than overflowing the dialog.
        expect(screen.getByText(/\+1 more/)).toBeInTheDocument();
    });

    it("does not require a selection when launched without one", async () => {
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();
    });
});

/**
 * Workflow options must never contain duplicates.
 *
 * The unscoped list (`/workflows`, used by the global Executions page) already returns every workflow
 * the caller can see, GLOBAL included. Fetching the GLOBAL catalog again and concatenating produced
 * each GLOBAL workflow twice, and duplicate keys break the picker's list reconciliation — typing in
 * its search box appeared to do nothing at all.
 */
describe("ExecuteWorkflowModal workflow options", () => {
    const GLOBAL_WF = {
        databaseId: "GLOBAL",
        workflowId: "wf-global",
        workflowName: "Shared Convert",
        enabled: true,
        archived: false,
        specifiedPipelines: [],
        systemConfig: { inputFileArity: "one" },
    };

    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAllPipelines.mockReturnValue({ data: [] });
    });

    it("lists a GLOBAL workflow once when both scopes return it", async () => {
        // The worst case: every call returns the same GLOBAL workflow.
        queries().useAllWorkflows.mockReturnValue({ data: [GLOBAL_WF] });

        render(<ExecuteWorkflowModal open onClose={() => undefined} />);
        await userEvent.click(screen.getByLabelText("Workflow"));

        expect(
            screen.getAllByRole("option").filter((o) => /Shared Convert/.test(o.textContent || ""))
        ).toHaveLength(1);
    });

    it("skips the redundant GLOBAL fetch when unscoped", () => {
        // Unscoped already includes GLOBAL, so the second query is disabled rather than merged.
        queries().useAllWorkflows.mockReturnValue({ data: [] });
        render(<ExecuteWorkflowModal open onClose={() => undefined} />);

        const globalCall = queries().useAllWorkflows.mock.calls.find(
            (c: any[]) => c[0] === "GLOBAL"
        );
        expect(globalCall).toBeDefined();
        // Third arg is `enabled`; false when there is no scoping database.
        expect(globalCall[2]).toBe(false);
    });

    it("still fetches GLOBAL when scoped to a database", () => {
        queries().useAllWorkflows.mockReturnValue({ data: [] });
        render(
            <ExecuteWorkflowModal open onClose={() => undefined} databaseId="db1" assetId="a1" />
        );
        const globalCall = queries().useAllWorkflows.mock.calls.find(
            (c: any[]) => c[0] === "GLOBAL"
        );
        expect(globalCall[2]).toBe(true);
    });

    it("keeps the search box filtering the option list", async () => {
        // The user-visible symptom of the duplication.
        queries().useAllWorkflows.mockImplementation((db?: string) => ({
            data:
                db === "GLOBAL"
                    ? []
                    : [
                          GLOBAL_WF,
                          { ...GLOBAL_WF, workflowId: "wf-other", workflowName: "Thumbnails" },
                      ],
        }));

        render(<ExecuteWorkflowModal open onClose={() => undefined} />);
        await userEvent.click(screen.getByLabelText("Workflow"));
        await userEvent.type(screen.getByPlaceholderText(/Type to search/), "thumb");

        const opts = screen.getAllByRole("option");
        expect(opts).toHaveLength(1);
        expect(opts[0].textContent).toContain("Thumbnails");
    });
});
