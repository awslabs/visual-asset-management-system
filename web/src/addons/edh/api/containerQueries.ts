/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as containerService from "./containers";
import { type EdhRequestError, mayHaveTakenEffect } from "./outcome";
import type {
    EdhContainerBackend,
    EdhContainerCatalog,
    EdhContainerJob,
    EdhContainerScope,
    KillContainerJobResponse,
    SubmitContainerJobRequest,
    SubmitContainerJobResponse,
} from "../types";

// Query key factory for the container feature, under the same "edh" namespace as queries.ts. A job
// list is keyed by backend and, for eks, by cluster; invalidating the backend prefix reaches both.
export const containerQk = {
    catalog: () => ["edh", "containerCatalog"] as const,
    jobs: (backend: EdhContainerBackend, cluster?: string) =>
        cluster
            ? (["edh", "containerJobs", backend, cluster] as const)
            : (["edh", "containerJobs", backend] as const),
};

/** Job states are owned by EDH and change without a VAMS action, so the list re-reads on a cadence. */
export const CONTAINER_JOBS_POLL_MS = 15000;

async function callService<T>(
    serviceFn: () => Promise<[boolean, T | string | EdhRequestError]>
): Promise<T> {
    const [ok, data] = await serviceFn();
    if (!ok) {
        // An EdhRequestError is thrown as it is: its status and body say whether EDH confirmed it.
        if (data instanceof Error) throw data;
        throw new Error(typeof data === "string" ? data : "Service call failed");
    }
    return data as T;
}

export function useContainerCatalog(enabled = true) {
    return useQuery({
        queryKey: containerQk.catalog(),
        queryFn: () =>
            callService<EdhContainerCatalog>(() => containerService.getContainerCatalog()),
        enabled,
        staleTime: 60_000,
    });
}

export function useContainerJobs(
    backend: EdhContainerBackend,
    enabled = true,
    scope?: EdhContainerScope
) {
    return useQuery({
        queryKey: containerQk.jobs(backend, scope?.cluster),
        queryFn: () =>
            callService<EdhContainerJob[]>(() =>
                containerService.listContainerJobs(backend, scope)
            ),
        enabled,
        refetchInterval: CONTAINER_JOBS_POLL_MS,
    });
}

export function useContainerJobMutations() {
    const queryClient = useQueryClient();
    const invalidateJobs = (backend: EdhContainerBackend) =>
        queryClient.invalidateQueries({ queryKey: containerQk.jobs(backend) });

    const submitJob = useMutation({
        mutationFn: (body: SubmitContainerJobRequest) =>
            callService<SubmitContainerJobResponse>(() =>
                containerService.submitContainerJob(body)
            ),
        // A submit or kill EDH did not confirm may still have happened, so the list is re-read then too.
        onSettled: (_response, err, body) =>
            mayHaveTakenEffect(err) ? invalidateJobs(body.backend) : undefined,
    });

    const killJob = useMutation({
        mutationFn: ({
            jobId,
            backend,
            scope,
        }: {
            jobId: string;
            backend: EdhContainerBackend;
            scope?: EdhContainerScope;
        }) =>
            callService<KillContainerJobResponse>(() =>
                containerService.killContainerJob(jobId, backend, scope)
            ),
        onSettled: (_response, err, { backend }) =>
            mayHaveTakenEffect(err) ? invalidateJobs(backend) : undefined,
    });

    return { submitJob, killJob };
}
