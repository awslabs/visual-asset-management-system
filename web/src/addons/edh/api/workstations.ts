/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import {
    type EdhRequestError,
    RESERVATION_FULL_LAUNCH_MESSAGE,
    isReservationFull,
    isUnknownOutcomeStatus,
    toOutcomeTuple,
} from "./outcome";
import type {
    CreateWorkstationRequest,
    EdhListResponse,
    WindowsSignIn,
    WorkstationConnection,
    WorkstationRecord,
} from "../types";

const WORKSTATIONS_PATH = "addon/edh/workstations";

const workstationPath = (sessionId: string, action?: string) =>
    `${WORKSTATIONS_PATH}/${encodeURIComponent(sessionId)}${action ? `/${action}` : ""}`;

/**
 * The launch reply never arrived: the gateway answered 502 or 504 after the request was sent, so the
 * workstation may or may not exist. Callers treat this message as "unknown outcome" and let the list
 * poll settle it rather than reporting a failure.
 */
export const LAUNCH_OUTCOME_UNKNOWN_MESSAGE =
    "The launch request did not receive a reply. The workstation may still be starting; " +
    "the list refreshes automatically.";

export { isUnknownOutcomeStatus };

export async function listWorkstations(): Promise<[boolean, WorkstationRecord[] | string]> {
    return toTuple(async () => {
        const resp: EdhListResponse<WorkstationRecord> = await apiClient.get(WORKSTATIONS_PATH);
        return resp?.Items ?? [];
    });
}

export async function createWorkstation(
    body: CreateWorkstationRequest
): Promise<[boolean, WorkstationRecord | string]> {
    try {
        return [true, await apiClient.post(WORKSTATIONS_PATH, { body })];
    } catch (e: any) {
        console.log(e);
        if (isUnknownOutcomeStatus(e?.status)) return [false, LAUNCH_OUTCOME_UNKNOWN_MESSAGE];
        if (isReservationFull(e)) return [false, RESERVATION_FULL_LAUNCH_MESSAGE];
        return [false, e?.message || "Request failed"];
    }
}

// Stop, start and delete keep a failure's status and body: EDH may carry one out without confirming it.
export async function stopWorkstation(
    sessionId: string
): Promise<[boolean, WorkstationRecord | EdhRequestError]> {
    return toOutcomeTuple(() => apiClient.post(workstationPath(sessionId, "stop"), {}));
}

export async function startWorkstation(
    sessionId: string
): Promise<[boolean, WorkstationRecord | EdhRequestError]> {
    return toOutcomeTuple(() => apiClient.post(workstationPath(sessionId, "start"), {}));
}

/** Resets the lease to the policy's full window from now; the row's own launch window is not reused. */
export async function renewWorkstation(
    sessionId: string
): Promise<[boolean, WorkstationRecord | string]> {
    return toTuple(() => apiClient.post(workstationPath(sessionId, "renew"), {}));
}

/** The identifier is the EDH session id, or the bootstrapId of an ORPHANED launch that never received one. */
export async function deleteWorkstation(
    sessionId: string
): Promise<[boolean, WorkstationRecord | EdhRequestError]> {
    return toOutcomeTuple(() => apiClient.del(workstationPath(sessionId), {}));
}

export async function getWorkstationConnection(
    sessionId: string
): Promise<[boolean, WorkstationConnection | string]> {
    return toTuple(() => apiClient.get(workstationPath(sessionId, "connect")));
}

/**
 * The Windows user name and password of one of the caller's running Windows workstations. The
 * password is a desktop credential: it is returned in this response only.
 */
export async function getWindowsSignIn(
    sessionId: string
): Promise<[boolean, WindowsSignIn | string]> {
    return toTuple(() => apiClient.get(workstationPath(sessionId, "windows-sign-in")));
}
