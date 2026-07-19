/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "./client";
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

export async function getExecutionLogs(
    executionId: string,
    params?: Record<string, string>
): Promise<[boolean, any]> {
    return toTuple(() => {
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(`workflows/executions/${executionId}/logs`, opts);
    });
}

export async function abortExecution(
    executionId: string,
    groupId?: string
): Promise<[boolean, any]> {
    return toTuple(() => {
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
