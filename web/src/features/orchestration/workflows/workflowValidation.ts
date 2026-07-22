/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Workflow, Pipeline } from "../types";

export interface ValidationResult {
    errors: string[];
    warnings: string[];
}

/**
 * Validates a workflow before save, checking for structural errors and configuration warnings.
 */
export function validateWorkflow(
    wf: Workflow,
    pipelinesById: Record<string, Pipeline>
): ValidationResult {
    const errors: string[] = [];
    const warnings: string[] = [];

    // ERROR: At least one pipeline required
    if (!wf.specifiedPipelines || wf.specifiedPipelines.length < 1) {
        errors.push("At least one pipeline is required");
    }

    // ERROR: Results-only must have arity none
    if (
        wf.systemConfig?.outputTarget?.locationType === "none" &&
        wf.systemConfig?.inputFileArity !== "none"
    ) {
        errors.push(
            "Workflows with no output location (results-only) must have inputFileArity set to 'none'"
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

    // WARNING: Check for archived/disabled pipelines and arity mismatches
    if (wf.specifiedPipelines) {
        for (const ref of wf.specifiedPipelines) {
            const compositeKey = ref.pipelineDatabaseId
                ? `${ref.pipelineDatabaseId}:${ref.pipelineId}`
                : ref.pipelineId;

            const pipeline = pipelinesById[compositeKey];
            if (!pipeline) {
                // No warning if pipeline not found in map
                continue;
            }

            // Warn if archived
            if (pipeline.archived === true) {
                warnings.push(`Pipeline '${ref.pipelineId}' is archived and may not be executable`);
            }

            // Warn if disabled
            if (pipeline.enabled === false) {
                warnings.push(`Pipeline '${ref.pipelineId}' is disabled and may not be executable`);
            }

            // Warn on arity incompatibility
            const workflowArity = wf.systemConfig?.inputFileArity;
            const pipelineArity = pipeline.systemConfig?.inputFileArity;

            if (workflowArity && pipelineArity) {
                const incompatible =
                    (workflowArity === "one" && pipelineArity === "multi") ||
                    (workflowArity === "none" &&
                        (pipelineArity === "one" || pipelineArity === "multi"));

                if (incompatible) {
                    warnings.push(
                        `Pipeline '${ref.pipelineId}' requires inputFileArity '${pipelineArity}' but workflow provides '${workflowArity}'`
                    );
                }
            }
        }
    }

    return { errors, warnings };
}
