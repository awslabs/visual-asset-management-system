/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple, pageAll } from "./client";
import type { Pipeline, Template, TagSchemaField } from "../types";

export async function listPipelines(
    databaseId?: string,
    includeArchived?: boolean
): Promise<[boolean, Pipeline[] | string]> {
    return toTuple(async () => {
        const path = databaseId ? `database/${databaseId}/pipelines` : "pipelines";
        const opts = includeArchived
            ? { queryStringParameters: { includeArchived: "true" } }
            : {};
        return pageAll((token) =>
            apiClient.get(path, { ...opts, ...(token && { queryStringParameters: { ...opts.queryStringParameters, startingToken: token } }) })
        );
    });
}

export async function getPipeline(
    databaseId: string,
    pipelineId: string
): Promise<[boolean, Pipeline | string]> {
    return toTuple(() => apiClient.get(`database/${databaseId}/pipelines/${pipelineId}`));
}

export async function createPipeline(body: Pipeline): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.post(`database/${body.databaseId}/pipelines`, { body })
    );
}

export async function updatePipeline(
    databaseId: string,
    pipelineId: string,
    body: Partial<Pipeline>
): Promise<[boolean, any]> {
    return toTuple(() =>
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

export async function archiveTemplate(
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
    return toTuple(() =>
        apiClient.get(
            `database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}/tagSchema`
        )
    );
}

export async function setTagSchema(
    databaseId: string,
    pipelineId: string,
    templateId: string,
    fields: TagSchemaField[]
): Promise<[boolean, any]> {
    return toTuple(() =>
        apiClient.put(
            `database/${databaseId}/pipelines/${pipelineId}/templates/${templateId}/tagSchema`,
            { body: fields }
        )
    );
}
