/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// The change-source value a workflow output write stamps on the S3 object version
// (VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION in backend common/s3MetadataKeys.py).
export const WORKFLOW_EXECUTION_CHANGE_SOURCE = "workflowExecution";

// The API route the execution quick view and detail page read; the "View execution" link is
// hidden when the caller may not call it, matching how the orchestration pages gate reads.
export const EXECUTION_DETAILS_API_ROUTE = "/workflows/executions/{executionId}/details";

/** HashRouter path of the full execution detail page (see routes.tsx). */
export function executionDetailPath(executionId: string): string {
    return `/executions/${executionId}`;
}

/**
 * The execution to link a file version to, or undefined. Only a version whose change source is
 * a workflow execution links: the ids are blank on every other source, and a stale id on a
 * version later rewritten by an upload would otherwise point at the wrong run.
 */
export function linkedExecutionId(
    item?: { changeSource?: string | null; changeWorkflowExecutionId?: string | null } | null
): string | undefined {
    if (!item || item.changeSource !== WORKFLOW_EXECUTION_CHANGE_SOURCE) {
        return undefined;
    }
    return item.changeWorkflowExecutionId || undefined;
}
