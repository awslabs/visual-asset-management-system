/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import { type EdhRequestError, edhRequestError } from "./outcome";
import type {
    EdhContainerBackend,
    EdhContainerCatalog,
    EdhContainerJob,
    EdhContainerScope,
    EdhListResponse,
    KillContainerJobResponse,
    SubmitContainerJobRequest,
    SubmitContainerJobResponse,
} from "../types";

const CONTAINERS_PATH = "addon/edh/containers";
const JOBS_PATH = `${CONTAINERS_PATH}/jobs`;

/** The query parameters of a list or kill: the backend, plus the cluster and namespace of an eks scope. */
function scopeParams(
    backend: EdhContainerBackend,
    scope?: EdhContainerScope
): Record<string, string> {
    const params: Record<string, string> = { backend };
    if (backend === "eks" && scope) {
        params.cluster = scope.cluster;
        if (scope.namespace) params.namespace = scope.namespace;
    }
    return params;
}

/**
 * Like `toOutcomeTuple`, but keeps the whole reply. The submit and kill replies carry `message` beside
 * `jobId` and the other fields, which `toTuple`'s `unwrapMessage` would reduce to that one string.
 */
async function toBodyTuple<T>(fn: () => Promise<T>): Promise<[boolean, T | EdhRequestError]> {
    try {
        return [true, await fn()];
    } catch (e: any) {
        console.log(e);
        return [false, edhRequestError(e)];
    }
}

export async function getContainerCatalog(): Promise<[boolean, EdhContainerCatalog | string]> {
    return toTuple(async () => {
        const resp = await apiClient.get(`${CONTAINERS_PATH}/catalog`);
        return { Items: resp?.Items ?? [], eksEnabled: !!resp?.eksEnabled };
    });
}

export async function listContainerJobs(
    backend: EdhContainerBackend,
    scope?: EdhContainerScope
): Promise<[boolean, EdhContainerJob[] | string]> {
    return toTuple(async () => {
        const resp: EdhListResponse<EdhContainerJob> = await apiClient.get(JOBS_PATH, {
            queryStringParameters: scopeParams(backend, scope),
        });
        return resp?.Items ?? [];
    });
}

export async function submitContainerJob(
    body: SubmitContainerJobRequest
): Promise<[boolean, SubmitContainerJobResponse | EdhRequestError]> {
    return toBodyTuple(() => apiClient.post(JOBS_PATH, { body }));
}

export async function killContainerJob(
    jobId: string,
    backend: EdhContainerBackend,
    scope?: EdhContainerScope
): Promise<[boolean, KillContainerJobResponse | EdhRequestError]> {
    return toBodyTuple(() =>
        apiClient.del(`${JOBS_PATH}/${encodeURIComponent(jobId)}`, {
            queryStringParameters: scopeParams(backend, scope),
        })
    );
}
