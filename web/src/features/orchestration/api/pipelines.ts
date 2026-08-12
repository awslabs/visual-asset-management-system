/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple, pageAll, unwrapMessage } from "./client";
import type { Pipeline, PipelineCreateRequest, Template, TagSchemaField } from "../types";

/**
 * A pipeline save result: the unwrapped pipeline plus any non-blocking warnings the backend
 * returned alongside `message` (e.g. a require-template pipeline in an auto-trigger with no
 * default template chosen). The save still succeeded — warnings are surfaced, not thrown.
 */
export interface PipelineSaveResult {
    pipeline: any;
    warnings: string[];
}

async function savePipeline(
    fn: () => Promise<any>
): Promise<[boolean, PipelineSaveResult | string]> {
    try {
        const resp = await fn();
        const warnings = Array.isArray(resp?.warnings) ? resp.warnings : [];
        return [true, { pipeline: unwrapMessage(resp), warnings }];
    } catch (e: any) {
        console.log(e);
        return [false, e?.message || "Request failed"];
    }
}

/**
 * One server page of pipelines. Returns the raw page object { Items, NextToken? } so the
 * caller (useInfiniteQuery) can page via the backend's maxItems/pageSize/startingToken params.
 */
export async function listPipelines(
    databaseId?: string,
    params?: Record<string, string>
): Promise<[boolean, { Items: Pipeline[]; NextToken?: string } | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/pipelines` : "pipelines";
        const opts = params ? { queryStringParameters: params } : {};
        return apiClient.get(path, opts);
    });
}

/**
 * All pipelines, draining every server page. Used where the complete set is required
 * (e.g. resolving a workflow's pipeline references, populating the builder's picker) —
 * NOT for the paginated list view, which uses listPipelines + useInfiniteQuery.
 */
export async function listAllPipelines(
    databaseId?: string,
    includeArchived?: boolean
): Promise<[boolean, Pipeline[] | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/pipelines` : "pipelines";
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

export async function getPipeline(
    databaseId: string,
    pipelineId: string
): Promise<[boolean, Pipeline | string]> {
    return toTuple(() => apiClient.get(`database/${databaseId}/pipelines/${pipelineId}`));
}

export async function createPipeline(
    body: PipelineCreateRequest
): Promise<[boolean, PipelineSaveResult | string]> {
    return savePipeline(() => apiClient.post(`database/${body.databaseId}/pipelines`, { body }));
}

export async function updatePipeline(
    databaseId: string,
    pipelineId: string,
    body: Partial<Pipeline>
): Promise<[boolean, PipelineSaveResult | string]> {
    return savePipeline(() =>
        apiClient.put(`database/${databaseId}/pipelines/${pipelineId}`, { body })
    );
}

export async function archivePipeline(
    databaseId: string,
    pipelineId: string
): Promise<[boolean, any]> {
    return toTuple(() => apiClient.del(`database/${databaseId}/pipelines/${pipelineId}`, {}));
}

export async function listTemplates(
    databaseId: string,
    pipelineId: string
): Promise<[boolean, Template[] | string]> {
    return toTuple(async () => {
        return pageAll((token) =>
            apiClient.get(`database/${databaseId}/pipelines/${pipelineId}/templates`, {
                ...(token && { queryStringParameters: { startingToken: token } }),
            })
        );
    });
}

export async function getTemplate(
    databaseId: string,
    pipelineId: string,
    templateId: string
): Promise<[boolean, Template | string]> {
    return toTuple(() =>
        apiClient.get(`database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}`)
    );
}

export async function createTemplate(
    databaseId: string,
    pipelineId: string,
    body: Template
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.post(`database/${databaseId}/pipelines/${pipelineId}/templates`, { body })
    );
}

export async function updateTemplate(
    databaseId: string,
    pipelineId: string,
    templateId: string,
    body: Partial<Template>
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.put(`database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}`, {
            body,
        })
    );
}

/**
 * Permanently delete a template. Unlike a pipeline or workflow delete (a soft archive), the backend
 * removes the template row, its offloaded S3 config bodies, and its tag schema — there is no archived
 * state to restore from.
 */
export async function deleteTemplate(
    databaseId: string,
    pipelineId: string,
    templateId: string
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.del(`database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}`, {})
    );
}

export async function getTagSchema(
    databaseId: string,
    pipelineId: string,
    templateId: string
): Promise<[boolean, TagSchemaField[] | string]> {
    // The response is a TagSchemaResponseModel object with the fields under `.fields`.
    return toTuple(async () => {
        const resp: any = await apiClient.get(
            `database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}/tagSchema`
        );
        const msg = resp?.message ?? resp;
        return msg?.fields ?? [];
    });
}

export async function setTagSchema(
    databaseId: string,
    pipelineId: string,
    templateId: string,
    fields: TagSchemaField[]
): Promise<[boolean, any]> {
    // The set handler parses SetTagSchemaRequestModel, which expects a `{ fields: [...] }` object.
    return toTuple(() =>
        apiClient.put(
            `database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}/tagSchema`,
            { body: { fields } }
        )
    );
}
