/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as workstationService from "./workstations";
import * as catalogService from "./catalog";
import { type EdhRequestError, mayHaveTakenEffect } from "./outcome";
import type {
    CatalogLeasePolicy,
    CreateWorkstationRequest,
    EdhCatalogImage,
    EdhInstanceType,
    WindowsSignIn,
    WorkstationConnection,
    WorkstationRecord,
} from "../types";

// Query key factory, local to this module so its keys never collide with the orchestration module's.
export const qk = {
    workstations: () => ["edh", "workstations"] as const,
    catalogImages: () => ["edh", "catalogImages"] as const,
    instanceTypes: () => ["edh", "instanceTypes"] as const,
    catalogLease: () => ["edh", "catalogLease"] as const,
};

/** Poll cadence while any listed workstation is still changing state. */
export const WORKSTATION_POLL_TRANSITIONAL_MS = 5000;
/** Poll cadence once every listed workstation is settled: EDH can still stop one on idle. */
export const WORKSTATION_POLL_STABLE_MS = 20000;

/**
 * A row still moving towards a steady state: a launch EDH has not reported back on, or an active
 * desktop EDH does not yet report connectable (a fresh start looks like this too).
 */
export function isTransitionalWorkstation(
    row: Pick<WorkstationRecord, "state" | "connectionAvailable">
): boolean {
    return row.state === "CREATING" || (row.state === "ACTIVE" && !row.connectionAvailable);
}

// Pure helper: 5000 while any row is transitional, 20000 when every row is settled, false when empty.
export function computeWorkstationRefetchInterval(
    rows: WorkstationRecord[] | undefined
): number | false {
    if (!rows || rows.length === 0) return false;
    return rows.some(isTransitionalWorkstation)
        ? WORKSTATION_POLL_TRANSITIONAL_MS
        : WORKSTATION_POLL_STABLE_MS;
}

// Helper to throw on service [false, msg] tuple for query error state
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

export function useWorkstations() {
    return useQuery({
        queryKey: qk.workstations(),
        queryFn: () =>
            callService<WorkstationRecord[]>(() => workstationService.listWorkstations()),
        // A hidden tab skips the tick (refetchIntervalInBackground defaults off), so a list left
        // open in a background tab issues nothing until it is looked at again.
        refetchInterval: (query: any) => computeWorkstationRefetchInterval(query.state.data),
    });
}

/**
 * The connection URL of one active workstation, requested when the user clicks Connect and never on
 * render: the URL carries the desktop credential, so it is neither prefetched nor kept in the DOM, and
 * a settled call leaves the mutation cache as soon as nothing observes it.
 */
export function useWorkstationConnect() {
    return useMutation({
        mutationFn: (sessionId: string) =>
            callService<WorkstationConnection>(() =>
                workstationService.getWorkstationConnection(sessionId)
            ),
        gcTime: 0,
    });
}

/**
 * The Windows sign-in of one of the caller's running Windows workstations, requested when its
 * dialog opens: the password is a desktop credential, so it is never kept in the query cache, and
 * a settled call leaves the mutation cache as soon as no dialog observes it.
 */
export function useWindowsSignIn() {
    return useMutation({
        mutationFn: (sessionId: string) =>
            callService<WindowsSignIn>(() => workstationService.getWindowsSignIn(sessionId)),
        gcTime: 0,
    });
}

export function useWorkstationMutations() {
    const queryClient = useQueryClient();
    const invalidateList = () => queryClient.invalidateQueries({ queryKey: qk.workstations() });
    // A stop, start or delete EDH did not confirm may still have happened, so the list is re-read
    // after one as after a success.
    const invalidateUnlessFailed = (_data: unknown, err: unknown) =>
        mayHaveTakenEffect(err) ? invalidateList() : undefined;

    const createWorkstation = useMutation({
        mutationFn: (body: CreateWorkstationRequest) =>
            callService<WorkstationRecord>(() => workstationService.createWorkstation(body)),
        // Settled rather than success: a launch whose reply was lost may still have written the
        // row, so the list is re-read either way.
        onSettled: () => invalidateList(),
    });

    const stopWorkstation = useMutation({
        mutationFn: ({ sessionId }: { sessionId: string }) =>
            callService<WorkstationRecord>(() => workstationService.stopWorkstation(sessionId)),
        onSettled: invalidateUnlessFailed,
    });

    const startWorkstation = useMutation({
        mutationFn: ({ sessionId }: { sessionId: string }) =>
            callService<WorkstationRecord>(() => workstationService.startWorkstation(sessionId)),
        onSettled: invalidateUnlessFailed,
    });

    const deleteWorkstation = useMutation({
        mutationFn: ({ sessionId }: { sessionId: string }) =>
            callService<WorkstationRecord>(() => workstationService.deleteWorkstation(sessionId)),
        onSettled: invalidateUnlessFailed,
    });

    const renewWorkstation = useMutation({
        mutationFn: ({ sessionId }: { sessionId: string }) =>
            callService<WorkstationRecord>(() => workstationService.renewWorkstation(sessionId)),
        onSuccess: () => invalidateList(),
    });

    return {
        createWorkstation,
        stopWorkstation,
        startWorkstation,
        deleteWorkstation,
        renewWorkstation,
    };
}

export function useEdhCatalogImages(enabled = true) {
    return useQuery({
        queryKey: qk.catalogImages(),
        queryFn: () => callService<EdhCatalogImage[]>(() => catalogService.listEdhImages()),
        enabled,
    });
}

export function useEdhInstanceTypes(enabled = true) {
    return useQuery({
        queryKey: qk.instanceTypes(),
        queryFn: () => callService<EdhInstanceType[]>(() => catalogService.listEdhInstanceTypes()),
        enabled,
    });
}

export function useEdhCatalogLease(enabled = true) {
    return useQuery({
        queryKey: qk.catalogLease(),
        queryFn: async () => {
            const payload = await callService<{ lease: CatalogLeasePolicy }>(() =>
                catalogService.getEdhLeasePolicy()
            );
            return payload.lease;
        },
        enabled,
    });
}
