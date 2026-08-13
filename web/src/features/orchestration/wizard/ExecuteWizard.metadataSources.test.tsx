/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The metadata-source payload at arity "none".
 *
 * The two source fields are their OWN request fields, never inputFiles — the backend rejects any input
 * file at this arity. And the selection is always optional: launching with nothing selected must reach
 * Review and go through, because a pipeline that truly needs the metadata validates and fails itself.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard from "./ExecuteWizard";
import type { Workflow, Pipeline } from "../types";

jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useTemplates: jest.fn(),
    useTemplate: jest.fn(),
    usePrefetchPipelineTemplates: jest.fn(),
    useExecuteWorkflow: jest.fn(),
    useDatabases: jest.fn(),
    useAssets: jest.fn(),
    useAssetSearch: jest.fn(),
    useAssetFiles: jest.fn(),
    useAssetFileSearch: jest.fn(),
    useFileVersions: jest.fn(),
}));

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

const METADATA_ON = {
    assetMetadata: true,
    // The file-scoped types are off: an arity-none run has no file to collect them from.
    fileMetadata: false,
    fileAttributes: false,
    databaseMetadata: true,
};

const NONE_WORKFLOW: Workflow = {
    databaseId: "db1",
    workflowId: "wf-none",
    workflowName: "Results only",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
    systemConfig: {
        inputFileArity: "none",
        metadataInputs: METADATA_ON,
        assetScope: { crossAssetAllowed: true, singleAssetOnly: false },
        outputTarget: { locationType: "none" },
    },
};

const NONE_PIPELINE: Pipeline = {
    databaseId: "db1",
    pipelineId: "pipe1",
    pipelineName: "Results Pipeline",
    enabled: true,
    executionConfig: { executionType: "Lambda" },
    systemConfig: { inputFileArity: "none", metadataInputs: METADATA_ON },
};

/** Mounts the wizard on the Input step with the source pickers populated. */
function mountWizard(executeResult: any = { executionId: "exec-1" }) {
    const q = require("../api/queries");
    const execMutate = jest.fn().mockResolvedValue(executeResult);
    q.useWorkflow.mockReturnValue({ data: NONE_WORKFLOW, isLoading: false });
    q.useAllPipelines.mockReturnValue({ data: [NONE_PIPELINE], isLoading: false });
    q.useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
    q.useTemplate.mockReturnValue({ data: undefined, isLoading: false });
    q.useExecuteWorkflow.mockReturnValue({ mutateAsync: execMutate, isPending: false });
    q.useDatabases.mockReturnValue({
        data: [{ databaseId: "db1" }, { databaseId: "db2" }, { databaseId: "GLOBAL" }],
        isLoading: false,
    });
    q.useAssets.mockReturnValue({ data: [], isLoading: false });
    q.useAssetSearch.mockReturnValue({
        data: {
            items: [{ databaseId: "db1", assetId: "asset-a", assetName: "Pump A" }],
            total: 1,
            listFallback: false,
        },
        isFetching: false,
    });
    q.useAssetFiles.mockReturnValue({ data: [], isLoading: false });
    q.useAssetFileSearch.mockReturnValue({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
    });
    q.useFileVersions.mockReturnValue({ data: [], isFetching: false });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={qc}>
            <ExecuteWizard
                open
                onClose={() => undefined}
                workflow={NONE_WORKFLOW}
                databaseId="db1"
            />
        </QueryClientProvider>
    );
    return execMutate;
}

/** Input -> pipeline -> Review. */
async function stepToReview() {
    fireEvent.click(screen.getByRole("button", { name: /^Next$/i }));
    await waitFor(() =>
        expect(screen.getAllByRole("heading", { level: 3 }).length).toBeGreaterThan(0)
    );
    fireEvent.click(screen.getByRole("button", { name: /^Next$/i }));
    await waitFor(() => expect(screen.getByText(/Review & Launch/i)).toBeInTheDocument());
}

/** Steps to Review and launches, returning the submitted body. */
async function launch(execMutate: jest.Mock) {
    await stepToReview();
    const button = screen.getByRole("button", { name: /Launch/i });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    await waitFor(() => expect(execMutate).toHaveBeenCalled());
    return execMutate.mock.calls[0][0].body;
}

/** Adds a source-asset row and selects the fixture asset in it. */
async function selectSourceAsset() {
    fireEvent.click(screen.getByRole("button", { name: "Add Metadata Source Asset" }));
    fireEvent.change(await screen.findByLabelText("Metadata source asset database"), {
        target: { value: "db1" },
    });
    fireEvent.click(screen.getByLabelText("Metadata source asset"));
    fireEvent.click(await screen.findByRole("option", { name: /Pump A/ }));
}

beforeEach(() => {
    jest.clearAllMocks();
});

describe("ExecuteWizard metadata-source payload", () => {
    it("reaches Review and launches with NOTHING selected", async () => {
        // The load-bearing requirement: a metadata source is never enforced. Neither field is sent, and
        // no input file is invented to carry one.
        const execMutate = mountWizard();
        await screen.findByLabelText("Metadata source database");
        const body = await launch(execMutate);
        expect(body.inputFiles).toEqual([]);
        expect(body.metadataSourceDatabaseId).toBeUndefined();
        expect(body.metadataSourceAssets).toBeUndefined();
    });

    it("sends the chosen database in metadataSourceDatabaseId, not as an input file", async () => {
        const execMutate = mountWizard();
        fireEvent.change(await screen.findByLabelText("Metadata source database"), {
            target: { value: "db2" },
        });
        const body = await launch(execMutate);
        expect(body.metadataSourceDatabaseId).toBe("db2");
        expect(body.inputFiles).toEqual([]);
    });

    it("sends a chosen source asset in metadataSourceAssets, with no file key", async () => {
        const execMutate = mountWizard();
        await screen.findByLabelText("Metadata source database");
        await selectSourceAsset();
        const body = await launch(execMutate);
        expect(body.metadataSourceAssets).toEqual([{ databaseId: "db1", assetId: "asset-a" }]);
        // A source is an entity, never a file — the tuple carries no key of any kind.
        expect(body.metadataSourceAssets[0].relativeFileKey).toBeUndefined();
        expect(body.inputFiles).toEqual([]);
    });

    it("omits a half-filled source row rather than sending an incomplete tuple", async () => {
        // A row with a database but no asset yet is not a selection; the request model would reject it.
        const execMutate = mountWizard();
        await screen.findByLabelText("Metadata source database");
        fireEvent.click(screen.getByRole("button", { name: "Add Metadata Source Asset" }));
        fireEvent.change(await screen.findByLabelText("Metadata source asset database"), {
            target: { value: "db1" },
        });
        const body = await launch(execMutate);
        expect(body.metadataSourceAssets).toBeUndefined();
    });

    it("lists the chosen sources on the Review step", async () => {
        const execMutate = mountWizard();
        fireEvent.change(await screen.findByLabelText("Metadata source database"), {
            target: { value: "db2" },
        });
        await selectSourceAsset();
        await stepToReview();

        expect(screen.getByText("Metadata Sources")).toBeInTheDocument();
        expect(screen.getByText(/Database: db2/)).toBeInTheDocument();
        expect(screen.getByText("db1 / asset-a")).toBeInTheDocument();
        expect(execMutate).not.toHaveBeenCalled();
    });

    it("surfaces execute warnings rather than dropping them", async () => {
        // The response's `warnings` has a real writer (metadata truncated, or a source database that
        // could not be read), so a launch can succeed with caveats worth reading.
        const execMutate = mountWizard({
            executionId: "exec-1",
            warnings: ["db1:pipe1 uses database metadata but the execution captured none."],
        });
        await launch(execMutate);
        expect(await screen.findByText(/Execution launched with warnings/i)).toBeInTheDocument();
        expect(screen.getByText(/captured none/)).toBeInTheDocument();
    });
});
