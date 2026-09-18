/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Pipeline, SpecifiedPipelineRef } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";
import type { WorkflowBlockedReason } from "./reviewBlockers";
import type { RailStep, RailStatus } from "./WizardRail";

export interface RailStepInputs {
    specifiedPipelines: SpecifiedPipelineRef[];
    /** Aligned 1:1 with `specifiedPipelines`; `undefined` where a reference did not resolve. */
    pipelines: (Pipeline | undefined)[];
    databaseId: string;
    pipelineData: Record<string, PipelineStageData>;
    offendingPipelines: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
    inputSelectionErrors: string[];
    outputAssetMissing: boolean;
    /** Set when the workflow itself is disabled or archived; the Inputs step is then its banner. */
    workflowBlockedReason?: WorkflowBlockedReason;
}

/**
 * The wizard's own rail rows: Inputs, one per pipeline reference (positionally, so an unresolved
 * middle reference keeps its ordinal), then Review — each with the chip the current state earns.
 */
export function buildRailSteps(input: RailStepInputs): RailStep[] {
    const inputsStatus: RailStatus = input.workflowBlockedReason
        ? "Error"
        : input.inputSelectionErrors.length > 0 || input.outputAssetMissing
        ? "Incomplete"
        : "Ready";

    const pipelineSteps: RailStep[] = input.specifiedPipelines.map((ref, idx) => {
        const id = `pipeline-${idx}`;
        const pipeline = input.pipelines[idx];
        const label = pipeline?.pipelineName || `Pipeline ${idx + 1}`;
        if (!pipeline) return { id, label, status: "Not found" };

        const offending = input.offendingPipelines.some((o) => o.pipelineId === ref.pipelineId);
        const data =
            input.pipelineData[`${ref.pipelineDatabaseId || input.databaseId}:${ref.pipelineId}`];
        let status: RailStatus | undefined;
        if (offending) {
            status = "Error";
        } else if (data) {
            // Mode 4 is "no template, no override": the step runs on its built-in settings.
            status =
                data.errors.length > 0 ? "Error" : data.mode === 4 ? "No configuration" : "Ready";
        } else if (pipeline.systemConfig?.requireTemplate && !ref.defaultTemplateId) {
            status = "Needs template";
        }
        return { id, label, status };
    });

    return [
        { id: "input", label: "Inputs", status: inputsStatus },
        ...pipelineSteps,
        { id: "review", label: "Review" },
    ];
}
