/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple, pageAll } from "./client";
import type { Workflow, WorkflowCreateRequest, WorkflowTrigger } from "../types";

/**
 * One server page of workflows. Returns the raw page object { Items, NextToken? } so the
 * caller (useInfiniteQuery) can page via the backend's maxItems/pageSize/startingToken params.
 */
export async function listWorkflows(
    databaseId?: string,
    params?: Record<string, string>
): Promise<[boolean, { Items: Workflow[]; NextToken?: string } | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/workflows` : "workflows";
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(path, opts);
    });
}

/**
 * All workflows, draining every server page. Used where the complete set is required;
 * NOT for the paginated list view, which uses listWorkflows + useInfiniteQuery.
 */
export async function listAllWorkflows(
    databaseId?: string,
    includeArchived?: boolean
): Promise<[boolean, Workflow[] | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/workflows` : "workflows";
        const opts = includeArchived ? { queryStringParameters: { includeArchived: "true" } } : {};
        return pageAll((token) =>
            apiClient.get(path, {
                ...opts,
                ...(token && {
                    queryStringParameters: { ...opts.queryStringParameters, startingToken: token },
                }),
            })
        );
    });
}

export async function getWorkflow(
    databaseId: string,
    workflowId: string
): Promise<[boolean, Workflow | string]> {
    return toTuple(() => apiClient.get(`database/${databaseId}/workflows/${workflowId}`));
}

export async function createWorkflow(body: WorkflowCreateRequest): Promise<[boolean, any]> {
    return toTuple(() => apiClient.post(`database/${body.databaseId}/workflows`, { body }));
}

export async function updateWorkflow(
    databaseId: string,
    workflowId: string,
    body: Partial<Workflow>
): Promise<[boolean, any]> {
    return toTuple(() => apiClient.put(`database/${databaseId}/workflows/${workflowId}`, { body }));
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
        const items = await pageAll((token) =>
            apiClient.get(`database/${databaseId}/workflows/${workflowId}/triggers`, {
                ...(token && { queryStringParameters: { startingToken: token } }),
            })
        );
        // The response nests the fileUpload settings under `triggerConfig`; flatten them to the
        // top level so a trigger reads back in the same flat shape the set request sends.
        return (items || []).map((t: any) => ({
            triggerType: t.triggerType,
            enabled: t.enabled,
            inputFileFilters: t.triggerConfig?.inputFileFilters ?? t.inputFileFilters,
            defaultTemplateIds: t.triggerConfig?.defaultTemplateIds ?? t.defaultTemplateIds,
        }));
    });
}

/**
 * A trigger key as a path segment.
 *
 * The key is the bare type for a workflow's first trigger of that type, or `type#triggerId` for an
 * additional one. A raw `#` is a URL fragment delimiter, so interpolating the key directly would send
 * only the bare type and act on the WRONG trigger — the request would silently target a sibling.
 */
function triggerSegment(triggerType: string): string {
    return encodeURIComponent(triggerType);
}

export async function setTrigger(
    databaseId: string,
    workflowId: string,
    triggerType: string,
    body: WorkflowTrigger
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.put(
            `database/${databaseId}/workflows/${workflowId}/triggers/${triggerSegment(
                triggerType
            )}`,
            {
                body,
            }
        )
    );
}

export async function deleteTrigger(
    databaseId: string,
    workflowId: string,
    triggerType: string
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.del(
            `database/${databaseId}/workflows/${workflowId}/triggers/${triggerSegment(
                triggerType
            )}`,
            {}
        )
    );
}
