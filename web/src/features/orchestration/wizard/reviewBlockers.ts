/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Pipeline, SpecifiedPipelineRef, Workflow } from "../types";

export interface ReviewBlocker {
    /** The wizard step that fixes it: "input" or "pipeline-<index>". */
    stepId: string;
    stepLabel: string;
    text: string;
}

/** Why the workflow itself cannot run, whatever the selection and the steps say. */
export type WorkflowBlockedReason = "disabled" | "archived";

/**
 * The workflow's own state as a blocker — the same predicate, in the same order, as the Inputs step's
 * "Cannot Execute" banner, so the banner, the rail chip, the Review blockers and the Launch gate all
 * read one fact.
 */
export const workflowBlockedReason = (
    workflow: Pick<Workflow, "enabled" | "archived">
): WorkflowBlockedReason | undefined =>
    !workflow.enabled ? "disabled" : workflow.archived ? "archived" : undefined;

export const WORKFLOW_BLOCKED_TEXT: Record<WorkflowBlockedReason, string> = {
    disabled: "This workflow is disabled.",
    archived: "This workflow is archived.",
};

export interface ReviewBlockerInputs {
    specifiedPipelines: SpecifiedPipelineRef[];
    /** Aligned 1:1 with `specifiedPipelines`; `undefined` where a reference did not resolve. */
    pipelines: (Pipeline | undefined)[];
    databaseId: string;
    /** Per-pipeline template errors, keyed `${pipelineDatabaseId || databaseId}:${pipelineId}`. */
    validationErrors: Record<string, string[]>;
    inputSelectionErrors: string[];
    outputAssetMissing: boolean;
    offendingPipelines: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
    workflowBlockedReason?: WorkflowBlockedReason;
}

export const OUTPUT_ASSET_MISSING_TEXT =
    "The selected input files span multiple assets — choose an output asset on the Inputs step.";

/** Upper bound on the input-file selection per execute request — models/executions.py. */
export const MAX_INPUT_FILES_PER_EXECUTION = 1000;

/** The blocker a selection over the cap raises; the request model rejects it whatever else holds. */
export const tooManyInputFilesText = (count: number): string =>
    `Too many input files (${count} > ${MAX_INPUT_FILES_PER_EXECUTION}) — remove ` +
    `${count - MAX_INPUT_FILES_PER_EXECUTION} to continue.`;

/**
 * Every condition that disables Launch, stated once and attributed to the step that clears it. The
 * same fact can arrive from more than one check, so entries are deduplicated on step and text.
 */
export function collectReviewBlockers(input: ReviewBlockerInputs): ReviewBlocker[] {
    const out: ReviewBlocker[] = [];
    const seen = new Set<string>();
    const push = (blocker: ReviewBlocker) => {
        const key = `${blocker.stepId}|${blocker.text}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push(blocker);
    };

    if (input.workflowBlockedReason) {
        push({
            stepId: "input",
            stepLabel: "Inputs",
            text: WORKFLOW_BLOCKED_TEXT[input.workflowBlockedReason],
        });
    }
    input.inputSelectionErrors.forEach((text) =>
        push({ stepId: "input", stepLabel: "Inputs", text })
    );
    if (input.outputAssetMissing) {
        push({ stepId: "input", stepLabel: "Inputs", text: OUTPUT_ASSET_MISSING_TEXT });
    }

    input.specifiedPipelines.forEach((ref, idx) => {
        const stepId = `pipeline-${idx}`;
        const pipeline = input.pipelines[idx];
        const stepLabel = pipeline?.pipelineName || `Pipeline ${idx + 1}`;
        const offending = input.offendingPipelines.find((o) => o.pipelineId === ref.pipelineId);
        if (offending) {
            push({
                stepId,
                stepLabel,
                text:
                    offending.reason === "not found"
                        ? "This pipeline was not found."
                        : `This pipeline is ${offending.reason}.`,
            });
        }
        const compositeKey = `${ref.pipelineDatabaseId || input.databaseId}:${ref.pipelineId}`;
        (input.validationErrors[compositeKey] || []).forEach((text) =>
            push({ stepId, stepLabel, text })
        );
    });

    return out;
}
