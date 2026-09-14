/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecuteWorkflowModal from "./ExecuteWorkflowModal";

jest.mock("../api/queries", () => ({
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined, isLoading: false, isError: false })),
    useExecuteWorkflow: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAssetSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useAssetFileSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useFileVersions: jest.fn(() => ({ data: [] })),
    // The wizard body mounts inside this dialog once Continue is pressed (or a workflow is preset),
    // so its hooks must exist here too. Prefetching only warms caches; a no-op is faithful.
    usePrefetchPipelineTemplates: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false, isSuccess: true })),
    useTemplate: jest.fn(() => ({ data: undefined, isLoading: false })),
}));

// ConfigEditor lazy-loads Monaco, so this stub is reached only by a test that steps into a pipeline
// stage; the other wizard suites carry the same stub for the same reason.
jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

// The component calls useAllWorkflows twice (the scope's database, then GLOBAL) and concatenates the
// results, so a mock returning the same list for both yields a duplicate option. Return the workflow
// only for the GLOBAL call, which is what a GLOBAL-owned workflow actually looks like.
const workflowsByDatabase = (workflows: any[]) => (databaseId?: string) => ({
    data: databaseId === "GLOBAL" ? workflows : [],
});
// The wizard module is NOT mocked. The modal imports validateInputSelection (through the picker) and
// ExecuteWizardBody from it, so stubbing it would remove the very validation these tests assert on.

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

/** The same pipeline as the body's catalogue sees it: enabled, so the Inputs step renders its card. */
const CATALOGUE_PIPELINE = {
    ...PIPELINE,
    pipelineName: "Converter",
    enabled: true,
    archived: false,
    executionConfig: { executionType: "Lambda" },
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

    it("summarizes what each workflow accepts on its row, resolved from its pipelines", async () => {
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
        // Exactly once: the row carries it on this step; the strip takes over from Inputs onward.
        expect(screen.getAllByText(/may narrow once a template is chosen/)).toHaveLength(1);
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

/**
 * The picker resolves every referenced pipeline from the same catalogue read the wizard body makes: one
 * unscoped, archived-inclusive list. A database workflow's own pipelines are not GLOBAL, so a picker
 * that read only the GLOBAL catalogue on an unscoped launch previewed them as "Pipeline N" and treated
 * their filters as absent until Continue.
 */
describe("ExecuteWorkflowModal pipeline catalogue", () => {
    const DB_PIPELINE = {
        databaseId: "db1",
        pipelineId: "thumbs",
        pipelineName: "Thumbnails",
        enabled: true,
        archived: false,
        executionConfig: { executionType: "Lambda" },
        systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
    };
    const DB_WORKFLOW = {
        ...WORKFLOW,
        workflowId: "wf-db",
        workflowName: "Thumbs",
        specifiedPipelines: [{ pipelineDatabaseId: "db1", pipelineId: "thumbs" }],
    };

    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAllWorkflows.mockImplementation((db?: string) => ({
            data: db === undefined ? [DB_WORKFLOW] : [],
        }));
        // Honours its arguments: the database-owned pipeline is in the unscoped, archived-inclusive
        // list only — never in a "GLOBAL" read.
        queries().useAllPipelines.mockImplementation((db?: string, includeArchived?: boolean) => ({
            data: db === undefined && includeArchived === true ? [DB_PIPELINE] : [],
        }));
    });

    it("names a database workflow's own pipelines on the preview rail before Continue", async () => {
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await userEvent.click(await screen.findByRole("option", { name: /Thumbs/ }));

        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(rail).toHaveTextContent("Thumbnails");
        expect(rail).not.toHaveTextContent("Pipeline 1");
        // The row's summary reflects the pipeline's filter, so the Inputs step cannot later narrow
        // what the row promised.
        expect(screen.getByRole("option", { name: /Thumbs/ })).toHaveTextContent(
            "1 file type · 1 file · writes to an asset"
        );
    });

    it("reads the catalogue once, with the body's own query, whatever the launch scope", () => {
        const { unmount } = render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        const calls = () =>
            queries().useAllPipelines.mock.calls.filter((c: any[]) => c[2] !== false);
        expect(calls()).toHaveLength(1);
        expect(calls()[0].slice(0, 2)).toEqual([undefined, true]);
        unmount();
        jest.clearAllMocks();

        render(<ExecuteWorkflowModal open onClose={jest.fn()} databaseId="db1" assetId="a1" />);
        expect(calls()).toHaveLength(1);
        expect(calls()[0].slice(0, 2)).toEqual([undefined, true]);
    });
});

/**
 * One dialog. The picker is the first step of the same dialog the wizard renders in; Continue swaps
 * the step rather than opening a second dialog, and the rail's Workflow row leads back.
 */
describe("ExecuteWorkflowModal one-dialog composition", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAllWorkflows.mockImplementation(workflowsByDatabase([WORKFLOW]));
        queries().useAllPipelines.mockReturnValue({ data: [CATALOGUE_PIPELINE], isLoading: false });
    });

    it("continues into the wizard inside the same dialog and shows the rail", async () => {
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        expect(screen.getByRole("dialog")).toHaveTextContent("Execute a workflow");
        // The picker step previews the rail too, with Workflow current.
        expect(screen.getByRole("navigation", { name: "Execution steps" })).toHaveTextContent(
            "Workflow"
        );

        await pickWorkflow();
        await userEvent.click(screen.getByRole("button", { name: "Continue" }));

        expect(screen.getAllByRole("dialog")).toHaveLength(1);
        expect(screen.getByRole("dialog")).toHaveTextContent("Execute Convert");
        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(rail).toHaveTextContent("Workflow");
        expect(rail).toHaveTextContent("Inputs");
        expect(rail).toHaveTextContent("Converter");
        expect(rail).toHaveTextContent("Review");
        // The body owns the footer now.
        expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
        // Arity one: the Inputs step opens on the single-file card.
        expect(screen.getByText("Input File")).toBeInTheDocument();
        // The requirements now live in the strip, once.
        expect(screen.getAllByText("Writes to an asset")).toHaveLength(1);
    });

    it("returns to the picker from the rail with the selection kept, and keeps the started body", async () => {
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        await userEvent.click(screen.getByRole("button", { name: "Continue" }));

        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        await userEvent.click(within(rail).getByRole("button", { name: /Workflow/ }));

        expect(screen.getByRole("dialog")).toHaveTextContent("Execute a workflow");
        expect(screen.getByRole("option", { name: /Convert/ })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
        // The body's footer is withdrawn while the picker shows; the body itself stays mounted hidden.
        expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
        expect(screen.getByText("Input File").closest("[hidden]")).not.toBeNull();
    });

    /** A second workflow on its own pipeline, so the two bodies are told apart by rail and step. */
    const SECOND_PIPELINE = {
        ...CATALOGUE_PIPELINE,
        databaseId: "db1",
        pipelineId: "thumbs",
        pipelineName: "Thumbnails",
        systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
    };
    const SECOND_WORKFLOW = {
        ...WORKFLOW,
        workflowId: "wf2",
        workflowName: "Thumbs",
        specifiedPipelines: [{ pipelineDatabaseId: "db1", pipelineId: "thumbs" }],
    };

    it("starts a different workflow fresh after returning to the picker", async () => {
        // The body is keyed on the started workflow, and nothing else resets its step, files or tag
        // values: a Continue on another workflow must not resume where the first one was left.
        queries().useAllWorkflows.mockImplementation(
            workflowsByDatabase([WORKFLOW, SECOND_WORKFLOW])
        );
        queries().useAllPipelines.mockReturnValue({
            data: [CATALOGUE_PIPELINE, SECOND_PIPELINE],
            isLoading: false,
        });
        render(<ExecuteWorkflowModal open onClose={jest.fn()} />);
        await pickWorkflow();
        await userEvent.click(screen.getByRole("button", { name: "Continue" }));
        // Leave the first body one step in, so an inherited body is visible as such.
        await userEvent.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
        expect(screen.queryByText("Input File")).not.toBeInTheDocument();

        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        await userEvent.click(within(rail).getByRole("button", { name: /Workflow/ }));
        await userEvent.click(screen.getByRole("option", { name: /Thumbs/ }));
        await userEvent.click(screen.getByRole("button", { name: "Continue" }));

        expect(screen.getByRole("dialog")).toHaveTextContent("Execute Thumbs");
        // On its own Inputs step, not on the first workflow's step position.
        expect(screen.getByText("Input File")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
        const railAfter = screen.getByRole("navigation", { name: "Execution steps" });
        expect(railAfter).toHaveTextContent("Thumbnails");
        expect(railAfter).not.toHaveTextContent("Converter");
    });

    it("re-opens on the picker with nothing selected after the dialog is closed", async () => {
        // The dialog's own close (the × button, Escape, the scrim) reaches handleClose, which drops the
        // selection and the started body so the next open does not resume a half-configured launch.
        const Host: React.FC = () => {
            const [open, setOpen] = React.useState(true);
            return (
                <>
                    <button onClick={() => setOpen(true)}>Reopen</button>
                    <ExecuteWorkflowModal open={open} onClose={() => setOpen(false)} />
                </>
            );
        };
        render(<Host />);
        await pickWorkflow();
        await userEvent.click(screen.getByRole("button", { name: "Continue" }));
        expect(screen.getByRole("dialog")).toHaveTextContent("Execute Convert");

        await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Reopen" }));
        expect(screen.getByRole("dialog")).toHaveTextContent("Execute a workflow");
        expect(screen.getByRole("listbox", { name: "Workflows" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { selected: true })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
        expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
        expect(screen.queryByText("Input File")).not.toBeInTheDocument();
    });

    it("badges each row's compatibility when launched from a selection", async () => {
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
        expect(await screen.findByRole("option", { name: /Convert/ })).toHaveTextContent(
            "Not compatible"
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
});

/**
 * A preset workflow skips the Workflow step. It is fetched directly: the enabled-only list would drop
 * a disabled workflow and leave the dialog blank instead of saying why it cannot run.
 */
describe("ExecuteWorkflowModal presetWorkflow", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAllWorkflows.mockReturnValue({ data: [] });
        queries().useAllPipelines.mockReturnValue({ data: [CATALOGUE_PIPELINE], isLoading: false });
    });

    it("opens straight on the wizard and never lists workflows", () => {
        queries().useWorkflow.mockReturnValue({ data: WORKFLOW, isLoading: false, isError: false });
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                presetWorkflow={{ databaseId: "db1", workflowId: "wf1" }}
            />
        );
        // The picker's list, not a bare option role: the Inputs step's native database <select>
        // contributes <option> elements of its own.
        expect(screen.queryByRole("listbox", { name: "Workflows" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
        expect(screen.getByRole("dialog")).toHaveTextContent("Execute Convert");
        expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(within(rail).queryByText("Workflow")).not.toBeInTheDocument();
        expect(queries().useWorkflow).toHaveBeenCalledWith("db1", "wf1");
        // The catalogue lists are not needed: the unscoped query (enabled by default) is disabled.
        const unscopedCall = queries().useAllWorkflows.mock.calls.find(
            (c: any[]) => c[0] === undefined
        );
        expect(unscopedCall[2]).toBe(false);
    });

    it("works for a disabled workflow and says why it cannot run", () => {
        queries().useWorkflow.mockReturnValue({
            data: { ...WORKFLOW, enabled: false },
            isLoading: false,
            isError: false,
        });
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                presetWorkflow={{ databaseId: "db1", workflowId: "wf1" }}
            />
        );
        expect(screen.getByText(/This workflow is disabled\./)).toBeInTheDocument();
    });

    /**
     * A results-only workflow whose one step needs no template: nothing but the workflow's own state
     * stands between the user and Launch, so this is where a missed disabled/archived gate shows.
     */
    const RESULTS_ONLY = {
        ...WORKFLOW,
        systemConfig: {
            inputFileArity: "none",
            inputFileFilters: {},
            metadataInputs: {},
            outputTarget: { locationType: "none" },
        },
    };

    /** The step of a results-only run reads no files either; a pipeline's arity defaults to one. */
    const RESULTS_ONLY_PIPELINE = {
        ...CATALOGUE_PIPELINE,
        systemConfig: { inputFileArity: "none" },
    };

    const openPreset = (workflow: Record<string, any>) => {
        const mutateAsync = jest.fn();
        queries().useExecuteWorkflow.mockReturnValue({ mutateAsync, isPending: false });
        queries().useAllPipelines.mockReturnValue({
            data: [
                workflow.systemConfig?.inputFileArity === "none"
                    ? RESULTS_ONLY_PIPELINE
                    : CATALOGUE_PIPELINE,
            ],
            isLoading: false,
        });
        queries().useWorkflow.mockReturnValue({ data: workflow, isLoading: false, isError: false });
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                presetWorkflow={{ databaseId: "db1", workflowId: "wf1" }}
            />
        );
        return mutateAsync;
    };

    const walkToReview = async () => {
        await userEvent.click(screen.getByRole("button", { name: "Next" }));
        await userEvent.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByText("Review & Launch")).toBeInTheDocument();
    };

    it("lets a runnable results-only workflow launch (control for the gates below)", async () => {
        const mutateAsync = openPreset(RESULTS_ONLY);
        await walkToReview();
        expect(screen.queryByText("Blockers")).not.toBeInTheDocument();
        const launch = screen.getByRole("button", { name: "Launch" });
        expect(launch).toBeEnabled();
        await userEvent.click(launch);
        expect(mutateAsync).toHaveBeenCalledTimes(1);
    });

    it("blocks Launch, flags Inputs and lists the blocker for a disabled workflow", async () => {
        const mutateAsync = openPreset({ ...RESULTS_ONLY, enabled: false });
        await walkToReview();

        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(within(rail).getByRole("button", { name: /Inputs/ })).toHaveTextContent("Error");
        expect(within(rail).getByRole("button", { name: /Inputs/ })).not.toHaveTextContent("Ready");
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        expect(blockers).toHaveTextContent("Inputs");
        expect(blockers).toHaveTextContent("This workflow is disabled.");

        const launch = screen.getByRole("button", { name: "Launch" });
        expect(launch).toBeDisabled();
        await userEvent.click(launch);
        expect(mutateAsync).not.toHaveBeenCalled();
    });

    it("names an archived workflow the same way", async () => {
        openPreset({ ...RESULTS_ONLY, archived: true });
        await walkToReview();
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        expect(blockers).toHaveTextContent("This workflow is archived.");
        expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
    });

    it("does not ask for a file above the banner that removed the file picker", async () => {
        // Arity one and disabled: the Inputs body is the banner, so the arity rule has no control
        // to act on and must not be raised — on the step, in the rail or under Blockers.
        openPreset({ ...WORKFLOW, enabled: false });
        const banner = screen.getByText(/This workflow is disabled\./);
        await userEvent.click(banner);
        expect(screen.queryByText("To continue")).not.toBeInTheDocument();
        expect(screen.queryByText(/requires exactly one input file/)).not.toBeInTheDocument();
        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(within(rail).getByText("Inputs").closest("li")).toHaveTextContent("Error");

        await walkToReview();
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        expect(blockers.querySelectorAll("li")).toHaveLength(1);
        expect(blockers).toHaveTextContent("This workflow is disabled.");
        expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
    });

    it("shows a loading state until the workflow arrives, with only Cancel available", () => {
        queries().useWorkflow.mockReturnValue({ data: undefined, isLoading: true, isError: false });
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                presetWorkflow={{ databaseId: "db1", workflowId: "wf1" }}
            />
        );
        expect(screen.getByText("Loading workflow…")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    });

    it("says so when the workflow cannot be loaded", () => {
        queries().useWorkflow.mockReturnValue({ data: undefined, isLoading: false, isError: true });
        render(
            <ExecuteWorkflowModal
                open
                onClose={jest.fn()}
                presetWorkflow={{ databaseId: "db1", workflowId: "wf1" }}
            />
        );
        expect(screen.getByText("This workflow could not be loaded.")).toBeInTheDocument();
    });
});
