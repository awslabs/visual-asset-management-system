/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The wizard with a selection of hundreds of files: the preset path opens straight into the list,
 * the request carries every entry unchanged, and a selection over the execution cap is a blocker the
 * rail, the Review list and the Launch gate all report.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard, { validateInputSelection } from "./ExecuteWizard";
import { MAX_INPUT_FILES_PER_EXECUTION, tooManyInputFilesText } from "./reviewBlockers";
import type { Workflow, Pipeline, ExecuteInputFile } from "../types";

jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useTemplates: jest.fn(),
    useTemplate: jest.fn(),
    usePrefetchPipelineTemplates: jest.fn(),
    useExecuteWorkflow: jest.fn(),
    useDatabases: jest.fn(() => ({ data: [{ databaseId: "db1" }], isLoading: false })),
    useAssetSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
    })),
    useAssetFileSearch: jest.fn(() => ({
        data: { items: [], total: 0, listFallback: false },
        isFetching: false,
    })),
    useAssetFilePages: jest.fn(),
    useFileVersions: jest.fn(() => ({ data: [], isLoading: false })),
}));

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

const workflow: Workflow = {
    databaseId: "db1",
    workflowId: "wf-multi",
    workflowName: "Multi",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
    systemConfig: {
        inputFileArity: "multi",
        assetScope: { crossAssetAllowed: true },
        outputTarget: { locationType: "asset", allowOverride: false },
    } as any,
};

const pipeline: Pipeline = {
    databaseId: "db1",
    pipelineId: "pipe1",
    pipelineName: "Multi Pipeline",
    enabled: true,
    executionConfig: { executionType: "Lambda" },
    systemConfig: { inputFileArity: "multi", assetScope: { crossAssetAllowed: true } } as any,
};

const filesFor = (count: number): ExecuteInputFile[] =>
    Array.from({ length: count }, (_, i) => ({
        databaseId: "db1",
        assetId: "asset-a",
        relativeFileKey: `/bulk/f${i}.txt`,
    }));

function renderWizard(presetInputFiles: ExecuteInputFile[]) {
    const q = require("../api/queries");
    const mutateAsync = jest.fn().mockResolvedValue({ executionId: "exec-1" });
    q.useWorkflow.mockReturnValue({ data: workflow, isLoading: false });
    q.useAllPipelines.mockReturnValue({ data: [pipeline], isLoading: false });
    q.useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
    q.useTemplate.mockReturnValue({ data: undefined, isLoading: false });
    q.useExecuteWorkflow.mockReturnValue({ mutateAsync, isPending: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={qc}>
            <ExecuteWizard
                open
                onClose={() => undefined}
                workflow={workflow}
                databaseId="db1"
                presetInputFiles={presetInputFiles}
            />
        </QueryClientProvider>
    );
    return { mutateAsync };
}

async function goToReview() {
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
        expect(screen.getAllByRole("heading", { level: 3 }).length).toBeGreaterThan(0)
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByText("Review & Launch")).toBeInTheDocument());
}

describe("ExecuteWizard with hundreds of preset files", () => {
    it("opens straight into the list with the count, no picker rows, and launches every entry", async () => {
        const { mutateAsync } = renderWizard(filesFor(300));
        expect(screen.getByTestId("input-file-count")).toHaveTextContent(
            "300 files across 1 asset"
        );
        expect(screen.queryAllByLabelText("File")).toHaveLength(0);
        expect(screen.getAllByTestId("input-file-row").length).toBeLessThan(100);
        // A preset launch is complete: nothing is listed under "To continue" and the rail is Ready.
        expect(screen.queryByText("To continue")).not.toBeInTheDocument();
        expect(screen.getByRole("navigation", { name: "Execution steps" })).toHaveTextContent(
            "Ready"
        );

        await goToReview();
        expect(screen.getByTestId("review-input-count")).toHaveTextContent(
            "300 files across 1 asset"
        );
        expect(screen.queryByText("Blockers")).not.toBeInTheDocument();
        const launch = screen.getByRole("button", { name: "Launch" });
        expect(launch).toBeEnabled();
        fireEvent.click(launch);
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        // The payload is the selection itself, unchanged and uncompressed.
        const body = mutateAsync.mock.calls[0][0].body;
        expect(body.inputFiles).toHaveLength(300);
        expect(body.inputFiles[299]).toEqual({
            databaseId: "db1",
            assetId: "asset-a",
            relativeFileKey: "/bulk/f299.txt",
        });
    });

    it("blocks Launch for a selection over the execution cap and names it on the rail and under Blockers", async () => {
        renderWizard(filesFor(MAX_INPUT_FILES_PER_EXECUTION + 1));
        const rail = screen.getByRole("navigation", { name: "Execution steps" });
        expect(rail).toHaveTextContent("Incomplete");
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("over the 1000 limit");

        await goToReview();
        expect(screen.getByText("Blockers")).toBeInTheDocument();
        expect(screen.getByText(tooManyInputFilesText(1001))).toBeInTheDocument();
        expect(screen.getByText(/Too many input files \(1001 > 1000\)/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
    });
});

describe("validateInputSelection input-file cap", () => {
    it("raises the cap error above the limit and nothing at it", () => {
        const ok = validateInputSelection(workflow.systemConfig, [], filesFor(1000));
        expect(ok).toEqual([]);
        const over = validateInputSelection(workflow.systemConfig, [], filesFor(1001));
        expect(over).toContain(tooManyInputFilesText(1001));
    });
});
