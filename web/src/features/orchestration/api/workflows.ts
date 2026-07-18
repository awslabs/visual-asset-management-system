/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple, pageAll } from "./client";
import type { Workflow, WorkflowTrigger } from "../types";

export async function listWorkflows(
    databaseId?: string,
    includeArchived?: boolean
): Promise<[boolean, Workflow[] | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/workflows` : "workflows";
        const opts = includeArchived
            ? { queryStringParameters: { includeArchived: "true" } }
            : {};
        return pageAll((token) =>
            apiClient.get(path, { ...opts, ...(token && { queryStringParameters: { ...opts.queryStringParameters, startingToken: token } }) })
        );
    });
}

export async function getWorkflow(
    databaseId: string,
    workflowId: string
): Promise<[boolean, Workflow | string]> {
    return toTuple(() => apiClient.get(`database/${databaseId}/workflows/${workflowId}`));
}

export async function createWorkflow(body: Workflow): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.post(`database/${body.databaseId}/workflows`, { body })
    );
}

export async function updateWorkflow(
    databaseId: string,
    workflowId: string,
    body: Partial<Workflow>
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.put(`database/${databaseId}/workflows/${workflowId}`, { body })
    );
}

export async function archiveWorkflow(
    databaseId: string,
    workflowId: string
): Promise<[boolean, any]> {
    return toTuple(() => apiClient.del(`database/${databaseId}/workflows/${workflowId}`, {}));
}

export async function listTriggers(
    databaseId: string,
    workflowId: string
): Promise<[boolean, WorkflowTrigger[] | string]> {
    return toTuple(async () => {
        return pageAll((token) =>
            apiClient.get(`database/${databaseId}/workflows/${workflowId}/triggers`, {
                ...(token && { queryStringParameters: { startingToken: token } }),
            })
        );
    });
}

export async function setTrigger(
    databaseId: string,
    workflowId: string,
    triggerType: string,
    body: WorkflowTrigger
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.put(`database/${databaseId}/workflows/${workflowId}/triggers/${triggerType}`, {
            body,
        })
    );
}

export async function deleteTrigger(
    databaseId: string,
    workflowId: string,
    triggerType: string
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.del(`database/${databaseId}/workflows/${workflowId}/triggers/${triggerType}`, {})
    );
}
