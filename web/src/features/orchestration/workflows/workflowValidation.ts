/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Workflow, Pipeline } from "../types";
import { metadataEnabled } from "../wizard/resolveRestrictions";

export interface ValidationResult {
    errors: string[];
    warnings: string[];
}

/**
 * Job name pattern. The job name becomes both a Step Functions state name and an S3 output-path
 * segment, and the backend validates it with the shared id validator, so it carries that exact
 * pattern (SpecifiedPipelineInput in backend/backend/models/workflows.py).
 */
export const JOB_NAME_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

/** The metadata-input gates the backend compares between a workflow and its pipelines. */
const METADATA_KEYS = [
    "assetMetadata",
    "fileMetadata",
    "fileAttributes",
    "databaseMetadata",
] as const;

/** The two extension forms the backend recognises: '*.ext' (canonical) and '.ext' (shorthand). */
const EXTENSION_PATTERN = /^\*?\.[a-zA-Z0-9]+$/;

/**
 * Whether two allow patterns can admit a common file (mirrors _patterns_may_overlap in
 * common/workflows/executionValidation.py): two extension patterns compare by extension, and
 * anything carrying a wildcard is treated as possibly overlapping.
 */
function patternsMayOverlap(patternA: string, patternB: string): boolean {
    const a = (patternA || "").toLowerCase();
    const b = (patternB || "").toLowerCase();
    if (EXTENSION_PATTERN.test(patternA || "") && EXTENSION_PATTERN.test(patternB || "")) {
        return a.replace(/^\*/, "") === b.replace(/^\*/, "");
    }
    if (a === b) return true;
    return ["*", "?", "["].some((ch) => a.includes(ch) || b.includes(ch));
}

/**
 * The pipeline allow-patterns the workflow's EXCLUDE list would suppress (mirrors
 * _excluded_pipeline_allows in common/workflows/executionValidation.py). Decidable only for
 * extension-vs-extension comparisons; a wildcard on either side is left alone rather than guessed at,
 * since a false warning on every glob-filtered workflow would train users to ignore the panel.
 */
function excludedPipelineAllows(
    workflowExclude: string[] | undefined,
    pipelineAllow: string[] | undefined
): string[] {
    const ext = (p: string) => p.toLowerCase().replace(/^\*/, "");
    return (pipelineAllow || []).filter(
        (allowed) =>
            allowed &&
            EXTENSION_PATTERN.test(allowed) &&
            (workflowExclude || []).some(
                (excluded) =>
                    excluded && EXTENSION_PATTERN.test(excluded) && ext(excluded) === ext(allowed)
            )
    );
}

/** True when every pipeline reference names a pipeline (the picker starts each card empty). */
export function allPipelineRefsSelected(refs?: { pipelineId?: string }[]): boolean {
    return !!refs && refs.every((ref) => !!ref.pipelineId);
}

/**
 * Validates a workflow before save, checking for structural errors and configuration warnings.
 */
export function validateWorkflow(
    // Accepts an in-progress create body too (workflowId may be null/absent before save); the
    // validation rules never read the workflow id.
    wf: Omit<Workflow, "workflowId"> & { workflowId?: string | null },
    pipelinesById: Record<string, Pipeline>
): ValidationResult {
    const errors: string[] = [];
    const warnings: string[] = [];

    // ERROR: At least one pipeline required
    if (!wf.specifiedPipelines || wf.specifiedPipelines.length < 1) {
        errors.push("At least one pipeline is required");
    }

    // ERROR: writing to an asset with no input files needs a selectable output asset at execute time
    if (
        wf.systemConfig?.outputTarget?.locationType === "asset" &&
        (wf.systemConfig?.inputFileArity ?? "one") === "none" &&
        !wf.systemConfig?.outputTarget?.allowOverride
    ) {
        errors.push(
            "A workflow with no input files (inputFileArity 'none') that writes to an asset must allow output override so an output asset can be chosen at execution time"
        );
    }

    // ERROR: workflowId pattern validation
    if (wf.workflowId && !/^[-_a-zA-Z0-9]{3,63}$/.test(wf.workflowId)) {
        errors.push(
            "workflowId must be 3-63 characters and contain only letters, numbers, hyphens, and underscores"
        );
    }

    // ERROR: subDashboardUrl must be an absolute http(s) URL (mirrors the backend rule; blocks
    // javascript:/data:/relative URLs that would otherwise be a stored-XSS link in the UI).
    if (wf.subDashboardUrl && !/^https?:\/\//i.test(wf.subDashboardUrl)) {
        errors.push("Sub-Dashboard URL must be an absolute http:// or https:// URL");
    }

    // Per-reference rules, then archived/disabled pipelines and arity mismatches
    if (wf.specifiedPipelines) {
        wf.specifiedPipelines.forEach((ref, i) => {
            const position = i + 1;

            // ERROR: an added card with nothing selected (the backend rejects an empty pipelineId)
            if (!ref.pipelineId) {
                errors.push(`Pipeline #${position} has no pipeline selected`);
            }

            // ERROR: the job name becomes a Step Functions state name and an S3 path segment
            if (ref.jobName && !JOB_NAME_PATTERN.test(ref.jobName)) {
                errors.push(
                    `Pipeline #${position} job name must be 3-63 characters and contain only letters, numbers, hyphens, and underscores`
                );
            }

            const compositeKey = ref.pipelineDatabaseId
                ? `${ref.pipelineDatabaseId}:${ref.pipelineId}`
                : ref.pipelineId;

            const pipeline = pipelinesById[compositeKey];
            if (!pipeline) {
                // No warning if pipeline not found in map
                return;
            }

            // ERROR: the backend rejects a workflow that references an archived pipeline
            if (pipeline.archived === true) {
                errors.push(
                    `Pipeline #${position} ('${ref.pipelineId}') is archived and cannot be used in a workflow`
                );
            }

            // Warn if disabled
            if (pipeline.enabled === false) {
                warnings.push(`Pipeline '${ref.pipelineId}' is disabled and may not be executable`);
            }

            // Arity incompatibility. Mirrors validate_workflow_save: a pipeline that needs at least
            // one file gets none from a 'none' workflow, and a single-file pipeline may be handed
            // several by a 'multi' workflow. ('one' feeding a 'multi' pipeline is satisfied.)
            const workflowArity = wf.systemConfig?.inputFileArity ?? "one";
            const pipelineArity = pipeline.systemConfig?.inputFileArity;

            if (pipelineArity) {
                const incompatible =
                    (workflowArity === "none" && pipelineArity !== "none") ||
                    (workflowArity === "multi" && pipelineArity === "one");

                if (incompatible) {
                    warnings.push(
                        `Pipeline '${ref.pipelineId}' requires inputFileArity '${pipelineArity}' but workflow provides '${workflowArity}'`
                    );
                }
            }

            // A metadata input the pipeline uses but the workflow gate turns off. Both sides read
            // through metadataEnabled: a key either map omits carries its builder default (ON), so
            // only an explicit `false` on the workflow is a gate. Reading the raw values instead would
            // warn that the workflow suppresses a type it actually provides — the same rule
            // validate_workflow_save applies in common/workflows/executionValidation.py.
            const pipelineMetadata = pipeline.systemConfig?.metadataInputs || {};
            const workflowMetadata = wf.systemConfig?.metadataInputs || {};
            METADATA_KEYS.forEach((key) => {
                if (
                    metadataEnabled(pipelineMetadata, key) &&
                    !metadataEnabled(workflowMetadata, key)
                ) {
                    warnings.push(
                        `Pipeline '${ref.pipelineId}' uses ${key} but the workflow's metadata input for ${key} is off; the pipeline will run without it`
                    );
                }
            });

            // Two independent ways the workflow's filters can starve this pipeline of its input.
            const workflowAllow = wf.systemConfig?.inputFileFilters?.allow || [];
            const pipelineAllow = pipeline.systemConfig?.inputFileFilters?.allow || [];

            // (1) The workflow's allow-list admits nothing the pipeline accepts.
            if (
                workflowAllow.length > 0 &&
                pipelineAllow.length > 0 &&
                !workflowAllow.some((w) => pipelineAllow.some((p) => patternsMayOverlap(w, p)))
            ) {
                warnings.push(
                    `Pipeline '${ref.pipelineId}' input-file filters may exclude everything the workflow filters allow`
                );
            }

            // (2) The workflow EXCLUDES a type the pipeline accepts. Distinct from (1): the
            // allow-lists can overlap perfectly and an exclude still removes the file afterwards,
            // since exclude is applied second. Separate message because the fix is a different field.
            const suppressed = excludedPipelineAllows(
                wf.systemConfig?.inputFileFilters?.exclude,
                pipelineAllow
            );
            if (suppressed.length > 0) {
                const remaining = pipelineAllow.filter((p) => !suppressed.includes(p));
                const detail =
                    remaining.length === 0
                        ? "leaving it no accepted input type"
                        : `leaving it only ${remaining.join(", ")}`;
                warnings.push(
                    `Pipeline '${ref.pipelineId}' accepts ${suppressed.join(", ")} but the ` +
                        `workflow's input-file filters exclude ` +
                        `${suppressed.length === 1 ? "that" : "those"}, ${detail}`
                );
            }
        });
    }

    return { errors, warnings };
}
