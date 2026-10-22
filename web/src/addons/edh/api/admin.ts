/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import { toOutcomeTuple } from "./outcome";
import type { EdhFailure, LeasePolicy } from "../types";

export type EdhImageKind = "vdi" | "simulation";

export interface ListResponse<T> {
    Items: T[];
}

/** One merged catalog entry: a dcv software stack (vdi) or a target_nodes one (simulation). */
export interface EdhAdminImage {
    kind: EdhImageKind;
    imageId: string;
    stackName: string;
    amiId: string;
    baseOs: string;
    osFamily: string;
    /** The image's CPU architecture as EDH registered it (`x86_64`, `arm64`); null when unknown. */
    architecture?: string | null;
    rootSize: number | null;
    profileId: number | null;
    description: string;
}

/** One EDH session joined with its VAMS session row when one exists. */
export interface EdhAdminInstance {
    sessionId: string | null;
    edhState: string | null;
    owner: string | null;
    name: string | null;
    createdAt: string | null;
    updatedAt: string | null;
    userId: string | null;
    bootstrapId: string | null;
    /** The VAMS state derived from EDH's session_state, or the row's when EDH lists no desktop. */
    state: string | null;
    lastError: string | null;
    /** The launch failure VAMS captured from EDH, from the joined VAMS row; null when there is none. */
    edhFailure?: EdhFailure | null;
    imageId: string | null;
    osFamily: string | null;
    instanceType: string | null;
    stale?: boolean;
    staleReason?: "stopped" | "missingInEdh";
}

/**
 * One EDH virtual desktop profile as EDH reports it. Only the id is guaranteed; the rest is the
 * shape of EDH 26.8 and tolerated absent. `allowed_instance_types` is a JSON string of
 * `{ architecture: string[] }` there, and `allowed_subnet_ids` a comma-separated string.
 */
export interface EdhDesktopProfile {
    id: number | string;
    profile_name?: string;
    is_active?: boolean;
    max_root_size?: number;
    allowed_instance_types?: string | string[] | Record<string, string[]>;
    pattern_allowed_instance_types?: string;
    allowed_subnet_ids?: string | string[];
    software_stack_ids?: number[];
    [field: string]: unknown;
}

export interface EdhCapacity {
    profiles: EdhDesktopProfile[];
    totalSessions: number;
    byState: Record<string, number>;
    distinctOwners: number;
}

export type EdhHealthReason = "credentialRejected" | "unreachable" | "refused";

/** The health probe's answer: `ok` with the latency, or `error` naming what an operator has to check. */
export interface EdhHealth {
    status: "ok" | "error";
    edhClusterId: string;
    latencyMs?: number;
    reason?: EdhHealthReason;
    message?: string;
}

export interface RegisterVdiImageRequest {
    amiId: string;
    stackName: string;
    baseOs: string;
    rootSize: number;
    virtualDesktopProfileId: number;
    launchTenancy?: string;
    description?: string;
}

export interface RegisterSimulationImageRequest {
    amiId: string;
    stackName: string;
    rootSize: number;
    targetNodeProfileId: number;
    /** EDH user-data template id; EDH's default template (1) when omitted. */
    targetNodeUserDataId?: number;
    userDataVariables?: string;
    description?: string;
}

export interface RegisteredImage {
    kind: EdhImageKind;
    /** The id EDH lists the new stack under. */
    imageId: string;
    stackName: string;
    amiId: string;
    osFamily: string;
}

export const listAdminImages = () =>
    toTuple<ListResponse<EdhAdminImage>>(() => apiClient.get("addon/edh/admin/images"));

// The registrations, deletes, stop and deactivate keep a failure's status and body: EDH may carry
// one out without confirming it.
export const registerVdiImage = (body: RegisterVdiImageRequest) =>
    toOutcomeTuple<RegisteredImage>(() => apiClient.post("addon/edh/admin/images/vdi", { body }));

export const registerSimulationImage = (body: RegisterSimulationImageRequest) =>
    toOutcomeTuple<RegisteredImage>(() =>
        apiClient.post("addon/edh/admin/images/simulation", { body })
    );

export const deleteVdiImage = (imageId: string) =>
    toOutcomeTuple(() =>
        apiClient.del(`addon/edh/admin/images/vdi/${encodeURIComponent(imageId)}`)
    );

export const deleteSimulationImage = (imageId: string) =>
    toOutcomeTuple(() =>
        apiClient.del(`addon/edh/admin/images/simulation/${encodeURIComponent(imageId)}`)
    );

export const listAdminInstances = () =>
    toTuple<ListResponse<EdhAdminInstance>>(() => apiClient.get("addon/edh/admin/instances"));

export const listStaleInstances = (idleMinutes: number) =>
    toTuple<ListResponse<EdhAdminInstance> & { idleMinutes: number }>(() =>
        apiClient.get("addon/edh/admin/instances/stale", {
            queryStringParameters: { idleMinutes: String(idleMinutes) },
        })
    );

export const stopAdminInstance = (sessionId: string) =>
    toOutcomeTuple<EdhAdminInstance>(() =>
        apiClient.post(`addon/edh/admin/instances/${encodeURIComponent(sessionId)}/stop`)
    );

export const deactivateAdminInstance = (sessionId: string) =>
    toOutcomeTuple<EdhAdminInstance>(() =>
        apiClient.post(`addon/edh/admin/instances/${encodeURIComponent(sessionId)}/deactivate`)
    );

export const getCapacity = () =>
    toTuple<EdhCapacity>(() => apiClient.get("addon/edh/admin/capacity"));

/**
 * The health probe answers 502 with a body that names WHY (a rejected root key, an unreachable
 * cluster, a refused probe); that body is the answer, so it is returned rather than reported as a
 * failed request.
 */
export const getHealth = async (): Promise<[boolean, EdhHealth | string]> => {
    try {
        return [true, await apiClient.get("addon/edh/admin/health")];
    } catch (e: any) {
        if (e?.status === 502 && e?.body?.status === "error") return [true, e.body as EdhHealth];
        console.log(e);
        return [false, e?.message || "Request failed"];
    }
};

export const getAdminSettings = () =>
    toTuple<{ lease: LeasePolicy }>(() => apiClient.get("addon/edh/admin/settings"));

export const updateAdminSettings = (
    body: Partial<Pick<LeasePolicy, "enabled" | "leaseHours" | "forced" | "graceHours">>
) => toTuple<{ lease: LeasePolicy }>(() => apiClient.put("addon/edh/admin/settings", { body }));
