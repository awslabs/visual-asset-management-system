/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A workflow step must be paired with ITS OWN pipeline record.
 *
 * The resolved-pipeline array was compacted, so a reference that did not resolve shortened it and slid
 * every later step onto the previous one's record. Three consumers read it positionally — the step
 * labels, the stage renderer and the review summary — so step 2 was labelled with its own id while
 * being handed step 3's templates and systemConfig, and the chosen template was stored under step 2's
 * composite key. Launch is blocked by the offending-pipelines gate, so nothing bad is submitted; what
 * is wrong is that the operator configures one pipeline believing they configured another.
 *
 * `WizardPipelineStage` is mocked so the pipeline each step is actually handed can be read directly.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecuteWizard from "./ExecuteWizard";
import type { Pipeline, Workflow } from "../types";

jest.mock("../api/queries", () => ({
    useWorkflow: jest.fn(),
    useAllPipelines: jest.fn(),
    useExecuteWorkflow: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    usePrefetchPipelineTemplates: jest.fn(),
    useDatabases: jest.fn(() => ({ data: [], isLoading: false })),
    useAssetSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useAssetFileSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useFileVersions: jest.fn(() => ({ data: [], isFetching: false })),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
    useTemplate: jest.fn(() => ({ data: undefined, isLoading: false })),
}));

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

/** Every pipeline handed to a pipeline stage, newest last. */
const stagedPipelines: any[] = [];

jest.mock("./WizardPipelineStage", () => ({
    __esModule: true,
    default: ({ pipeline, pipelineRef }: any) => {
        stagedPipelines.push({ pipeline, pipelineRef });
        return (
            <div data-testid="pipeline-stage" data-pipeline-id={pipeline?.pipelineId ?? ""}>
                {`stage for ${pipelineRef?.pipelineId}`}
            </div>
        );
    },
}));

const pipeline = (id: string, name: string, over: Record<string, any> = {}): Pipeline =>
    ({
        databaseId: "db1",
        pipelineId: id,
        pipelineName: name,
        enabled: true,
        archived: false,
        executionConfig: { executionType: "Lambda" },
        systemConfig: { inputFileArity: "none" },
        ...over,
    } as Pipeline);

const ALPHA = pipeline("pipe-alpha", "Alpha Pipeline");
const BRAVO = pipeline("pipe-bravo", "Bravo Pipeline");
const CHARLIE = pipeline("pipe-charlie", "Charlie Pipeline");

const THREE_STEP: Workflow = {
    databaseId: "db1",
    workflowId: "wf-three",
    workflowName: "Three Step",
    enabled: true,
    archived: false,
    specifiedPipelines: [
        { pipelineId: "pipe-alpha", pipelineDatabaseId: "db1" },
        { pipelineId: "pipe-bravo", pipelineDatabaseId: "db1" },
        { pipelineId: "pipe-charlie", pipelineDatabaseId: "db1" },
    ],
    systemConfig: { inputFileArity: "none" },
} as Workflow;

const renderWizard = (catalog: Pipeline[]) => {
    const { useWorkflow, useAllPipelines } = require("../api/queries");
    useWorkflow.mockReturnValue({ data: THREE_STEP, isLoading: false });
    useAllPipelines.mockReturnValue({ data: catalog, isLoading: false, isSuccess: true });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <ExecuteWizard open onClose={jest.fn()} workflow={THREE_STEP} databaseId="db1" />
        </QueryClientProvider>
    );
};

const next = () => userEvent.click(screen.getByRole("button", { name: "Next" }));

describe("ExecuteWizard step-to-pipeline alignment", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        stagedPipelines.length = 0;
    });

    it("labels the step whose reference did not resolve, not the one after it", async () => {
        // The middle reference is absent from the catalogue entirely.
        renderWizard([ALPHA, CHARLIE]);

        // Control: the steps that DID resolve carry their own names.
        expect(screen.getByText("Alpha Pipeline")).toBeInTheDocument();
        expect(screen.getByText("Charlie Pipeline")).toBeInTheDocument();
        // The unresolved step falls back to its ordinal — the SECOND one.
        expect(screen.getByText("Pipeline 2")).toBeInTheDocument();
        // Compacting the array put Charlie at index 1 and left index 2 empty, which read as "Pipeline 3".
        expect(screen.queryByText("Pipeline 3")).not.toBeInTheDocument();
    });

    it("hands each stage the pipeline its own reference names", async () => {
        renderWizard([ALPHA, CHARLIE]);

        await next(); // Input -> step 1
        expect(screen.getByTestId("pipeline-stage")).toHaveAttribute(
            "data-pipeline-id",
            "pipe-alpha"
        );

        await next(); // -> step 2, whose reference did not resolve
        expect(screen.getByText("Pipeline not found")).toBeInTheDocument();
        expect(screen.queryByTestId("pipeline-stage")).not.toBeInTheDocument();

        await next(); // -> step 3
        expect(screen.getByTestId("pipeline-stage")).toHaveAttribute(
            "data-pipeline-id",
            "pipe-charlie"
        );
        // The ref and the record must agree: a mismatch is how a chosen template ends up stored under
        // another step's composite key.
        const last = stagedPipelines[stagedPipelines.length - 1];
        expect(last.pipelineRef.pipelineId).toBe("pipe-charlie");
        expect(last.pipeline.pipelineId).toBe("pipe-charlie");
    });

    it("asks for archived pipelines so an archived reference resolves", () => {
        renderWizard([ALPHA, BRAVO, CHARLIE]);

        const { useAllPipelines } = require("../api/queries");
        expect(useAllPipelines).toHaveBeenCalledWith(undefined, true);
    });

    it("reports an archived step as archived rather than not found", () => {
        renderWizard([ALPHA, { ...BRAVO, archived: true } as Pipeline, CHARLIE]);

        // The name appears TWICE once the reference resolves — as the step's own label in the stepper
        // and as the offending-pipeline entry — so the assertion has to name which one it means.
        const matches = screen.getAllByText("Bravo Pipeline");
        expect(matches.length).toBeGreaterThan(1);

        const entry = matches
            .map((el) => el.closest("li"))
            .find((li): li is HTMLLIElement => li !== null);
        expect(entry).toBeTruthy();
        expect(entry).toHaveTextContent("archived");
        // Control: with the record in hand the reason is specific, so the generic branch is not used —
        // an unresolvable reference is what reports "not found".
        expect(entry).not.toHaveTextContent("not found");
    });
});
