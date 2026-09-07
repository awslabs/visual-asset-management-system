/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple, toTupleWithWarnings } from "./client";
import type { ResultWithWarnings } from "./client";
import type { ExecuteRequest, Execution, ExecutionDetail, ExecuteResponse } from "../types";

export async function executeWorkflow(
    workflowDatabaseId: string,
    workflowId: string,
    body: ExecuteRequest
): Promise<[boolean, ExecuteResponse | string]> {
    return toTuple(() =>
        apiClient.post(`workflows/${workflowDatabaseId}/${workflowId}/execute`, { body })
    );
}

export async function listExecutionsGlobal(
    params?: Record<string, string>
): Promise<[boolean, { Items: Execution[]; NextToken?: string } | string]> {
    return toTuple(async () => {
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get("workflows/executions", opts);
    });
}

export async function listExecutionsForAsset(
    databaseId: string,
    assetId: string,
    params?: Record<string, string>
): Promise<[boolean, { Items: Execution[]; NextToken?: string } | string]> {
    return toTuple(async () => {
        const path = `database/${databaseId}/assets/${assetId}/workflows/executions`;
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(path, opts);
    });
}

export async function getExecutionDetails(
    executionId: string
): Promise<[boolean, ExecutionDetail | string]> {
    return toTuple(() => apiClient.get(`workflows/executions/${executionId}/details`));
}

/** The metadata collections the paged detail-metadata route serves, in its own vocabulary. */
export type DetailMetadataCollection = "input" | "inputDatabase" | "output";

/** One page of one detail metadata collection. NextToken is absent on the last page. */
export interface DetailMetadataPage {
    Items: any[];
    collection: string;
    NextToken?: string;
}

/**
 * One page of one of an execution's metadata collections.
 *
 * Rows carry the same scrubbed shape the details view returns (plus the producing pipelineId), so a
 * caller renders them with the details view's own columns. `collection` selects which one:
 * `input` (asset/file metadata), `inputDatabase`, `output`.
 */
export async function getExecutionDetailsMetadata(
    executionId: string,
    params?: Record<string, string>
): Promise<[boolean, DetailMetadataPage | string]> {
    return toTuple(() => {
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(`workflows/executions/${executionId}/details/metadata`, opts);
    });
}

export async function getExecutionLogs(
    executionId: string,
    params?: Record<string, string>
): Promise<[boolean, any]> {
    return toTuple(() => {
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(`workflows/executions/${executionId}/logs`, opts);
    });
}

/**
 * Abort an execution, or every active execution in a group. The abort itself always succeeds, but
 * the response carries a `warnings` array when a registered sub-process could not be stopped — a
 * Batch job or Deadline Cloud farm job left running after the execution is marked ABORTED. Read with
 * `toTupleWithWarnings` because the backend puts `warnings` beside `message`, which the plain
 * `toTuple` reader would drop; that array is the only signal the compute was not released.
 */
export async function abortExecution(
    executionId: string,
    groupId?: string
): Promise<[boolean, ResultWithWarnings | string]> {
    return toTupleWithWarnings(() => {
        const opts = groupId ? { queryStringParameters: { groupId } } : {};
        return apiClient.del(`workflows/executions/${executionId}`, opts);
    });
}

export async function rerunExecution(
    executionId: string,
    executionGroupId?: string
): Promise<[boolean, any]> {
    return toTuple(() => {
        const body = executionGroupId ? { executionGroupId } : {};
        return apiClient.post(`workflows/executions/${executionId}/rerun`, { body });
    });
}

export async function permanentDeleteExecution(executionId: string): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.del(`workflows/executions/${executionId}/permanent`, {
            body: { confirmDelete: true },
        })
    );
}
