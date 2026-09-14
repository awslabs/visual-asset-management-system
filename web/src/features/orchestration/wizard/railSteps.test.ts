/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { buildRailSteps } from "./railSteps";
import { tooManyInputFilesText } from "./reviewBlockers";
import type { Pipeline } from "../types";

const pipeline = (id: string, name: string, over: Record<string, any> = {}): Pipeline =>
    ({
        databaseId: "db1",
        pipelineId: id,
        pipelineName: name,
        enabled: true,
        executionConfig: { executionType: "Lambda" },
        systemConfig: {},
        ...over,
    } as Pipeline);

const base = {
    specifiedPipelines: [
        { pipelineId: "a", pipelineDatabaseId: "db1" },
        { pipelineId: "b", pipelineDatabaseId: "db1" },
    ],
    pipelines: [pipeline("a", "Alpha"), pipeline("b", "Bravo")],
    databaseId: "db1",
    pipelineData: {},
    offendingPipelines: [],
    inputSelectionErrors: [],
    outputAssetMissing: false,
};

describe("buildRailSteps", () => {
    it("frames Inputs, one row per pipeline in order, then Review", () => {
        const steps = buildRailSteps(base);
        expect(steps.map((s) => s.id)).toEqual(["input", "pipeline-0", "pipeline-1", "review"]);
        expect(steps.map((s) => s.label)).toEqual(["Inputs", "Alpha", "Bravo", "Review"]);
    });

    it("labels an unresolved reference by its ordinal and flags it Not found", () => {
        const steps = buildRailSteps({ ...base, pipelines: [pipeline("a", "Alpha"), undefined] });
        expect(steps[2]).toMatchObject({ label: "Pipeline 2", status: "Not found" });
    });

    it("reads the Inputs chip from the selection errors and the output gate", () => {
        expect(buildRailSteps(base)[0].status).toBe("Ready");
        expect(buildRailSteps({ ...base, inputSelectionErrors: ["x"] })[0].status).toBe(
            "Incomplete"
        );
        expect(buildRailSteps({ ...base, outputAssetMissing: true })[0].status).toBe("Incomplete");
    });

    it("marks Inputs Incomplete for a selection over the execution cap", () => {
        // The cap is an input-selection error like any other, so the chip reads from the same list.
        const steps = buildRailSteps({
            ...base,
            inputSelectionErrors: [tooManyInputFilesText(1001)],
        });
        expect(steps[0]).toEqual({ id: "input", label: "Inputs", status: "Incomplete" });
    });

    it("flags Inputs as Error for a disabled or archived workflow, over any selection state", () => {
        expect(buildRailSteps({ ...base, workflowBlockedReason: "disabled" })[0].status).toBe(
            "Error"
        );
        expect(
            buildRailSteps({
                ...base,
                workflowBlockedReason: "archived",
                inputSelectionErrors: ["x"],
            })[0].status
        ).toBe("Error");
    });

    it("reads a pipeline chip from its stage data once the step has been visited", () => {
        const data = (over: Record<string, any>) => ({
            pipelineId: "a",
            tags: [],
            errors: [],
            params: {},
            ...over,
        });
        expect(
            buildRailSteps({ ...base, pipelineData: { "db1:a": data({ mode: 1 }) } })[1].status
        ).toBe("Ready");
        expect(
            buildRailSteps({ ...base, pipelineData: { "db1:a": data({ mode: 4 }) } })[1].status
        ).toBe("No configuration");
        expect(
            buildRailSteps({
                ...base,
                pipelineData: { "db1:a": data({ errors: ["Required tags missing: q"] }) },
            })[1].status
        ).toBe("Error");
    });

    it("says Needs template before a require-template step with no default is visited", () => {
        const steps = buildRailSteps({
            ...base,
            pipelines: [
                pipeline("a", "Alpha", { systemConfig: { requireTemplate: true } }),
                pipeline("b", "Bravo"),
            ],
        });
        expect(steps[1].status).toBe("Needs template");
        // A default template on the reference means the step opens configured.
        const withDefault = buildRailSteps({
            ...base,
            specifiedPipelines: [
                { pipelineId: "a", pipelineDatabaseId: "db1", defaultTemplateId: "t1" },
                { pipelineId: "b", pipelineDatabaseId: "db1" },
            ],
            pipelines: [
                pipeline("a", "Alpha", { systemConfig: { requireTemplate: true } }),
                pipeline("b", "Bravo"),
            ],
        });
        expect(withDefault[1].status).toBeUndefined();
        expect(steps[2].status).toBeUndefined();
    });

    it("flags a disabled or archived pipeline as Error whatever its stage data says", () => {
        const steps = buildRailSteps({
            ...base,
            offendingPipelines: [{ pipelineId: "b", pipelineName: "Bravo", reason: "archived" }],
            pipelineData: {
                "db1:b": { pipelineId: "b", tags: [], errors: [], params: {}, mode: 1 },
            },
        });
        expect(steps[2].status).toBe("Error");
    });
});
