/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    collectReviewBlockers,
    workflowBlockedReason,
    tooManyInputFilesText,
    MAX_INPUT_FILES_PER_EXECUTION,
    OUTPUT_ASSET_MISSING_TEXT,
} from "./reviewBlockers";
import type { Pipeline } from "../types";

const pipeline = (id: string, name: string): Pipeline =>
    ({
        databaseId: "db1",
        pipelineId: id,
        pipelineName: name,
        executionConfig: { executionType: "Lambda" },
    } as Pipeline);

const base = {
    specifiedPipelines: [{ pipelineId: "a", pipelineDatabaseId: "db1" }, { pipelineId: "b" }],
    pipelines: [pipeline("a", "Alpha"), pipeline("b", "Bravo")],
    databaseId: "db1",
    validationErrors: {},
    inputSelectionErrors: [],
    outputAssetMissing: false,
    offendingPipelines: [],
};

describe("collectReviewBlockers", () => {
    it("is empty when nothing blocks the launch", () => {
        expect(collectReviewBlockers(base)).toEqual([]);
    });

    it("attributes input-selection errors and the output gate to the Inputs step", () => {
        const out = collectReviewBlockers({
            ...base,
            inputSelectionErrors: [
                "Workflow requires at least one input file but none were provided.",
            ],
            outputAssetMissing: true,
        });
        expect(out).toEqual([
            {
                stepId: "input",
                stepLabel: "Inputs",
                text: "Workflow requires at least one input file but none were provided.",
            },
            { stepId: "input", stepLabel: "Inputs", text: OUTPUT_ASSET_MISSING_TEXT },
        ]);
    });

    it("attributes template errors to their pipeline step by composite key, falling back to the wizard database", () => {
        const out = collectReviewBlockers({
            ...base,
            validationErrors: {
                "db1:a": ["Required tags missing: quality"],
                "db1:b": ["This pipeline requires a template (templateId) for execution"],
            },
        });
        expect(out).toEqual([
            { stepId: "pipeline-0", stepLabel: "Alpha", text: "Required tags missing: quality" },
            {
                stepId: "pipeline-1",
                stepLabel: "Bravo",
                text: "This pipeline requires a template (templateId) for execution",
            },
        ]);
    });

    it("names a disabled, archived or missing pipeline once, before its template errors", () => {
        const out = collectReviewBlockers({
            ...base,
            pipelines: [pipeline("a", "Alpha"), undefined],
            offendingPipelines: [
                { pipelineId: "a", pipelineName: "Alpha", reason: "archived" },
                { pipelineId: "b", pipelineName: "b", reason: "not found" },
            ],
            validationErrors: { "db1:a": ["Template not selected"] },
        });
        expect(out).toEqual([
            { stepId: "pipeline-0", stepLabel: "Alpha", text: "This pipeline is archived." },
            { stepId: "pipeline-0", stepLabel: "Alpha", text: "Template not selected" },
            { stepId: "pipeline-1", stepLabel: "Pipeline 2", text: "This pipeline was not found." },
        ]);
    });

    it("names a disabled or archived workflow first, on the Inputs step, in the banner's words", () => {
        const disabled = collectReviewBlockers({
            ...base,
            workflowBlockedReason: "disabled",
            validationErrors: { "db1:a": ["Template not selected"] },
        });
        expect(disabled[0]).toEqual({
            stepId: "input",
            stepLabel: "Inputs",
            text: "This workflow is disabled.",
        });
        expect(disabled).toHaveLength(2);
        expect(collectReviewBlockers({ ...base, workflowBlockedReason: "archived" })).toEqual([
            { stepId: "input", stepLabel: "Inputs", text: "This workflow is archived." },
        ]);
    });

    it("drops an exact duplicate on the same step", () => {
        const out = collectReviewBlockers({
            ...base,
            inputSelectionErrors: [
                "Every input row needs an asset.",
                "Every input row needs an asset.",
            ],
        });
        expect(out).toHaveLength(1);
    });
});

describe("tooManyInputFilesText", () => {
    it("names the count, the cap and how many to remove, and reaches Blockers as an Inputs entry", () => {
        const text = tooManyInputFilesText(1200);
        expect(text).toBe("Too many input files (1200 > 1000) — remove 200 to continue.");
        expect(MAX_INPUT_FILES_PER_EXECUTION).toBe(1000);
        const out = collectReviewBlockers({ ...base, inputSelectionErrors: [text] });
        expect(out).toEqual([{ stepId: "input", stepLabel: "Inputs", text }]);
    });
});

describe("workflowBlockedReason", () => {
    it("reads disabled before archived, as the Inputs banner does, and nothing for a runnable workflow", () => {
        expect(workflowBlockedReason({ enabled: true, archived: false })).toBeUndefined();
        expect(workflowBlockedReason({ enabled: false, archived: false })).toBe("disabled");
        expect(workflowBlockedReason({ enabled: true, archived: true })).toBe("archived");
        expect(workflowBlockedReason({ enabled: false, archived: true })).toBe("disabled");
    });
});
