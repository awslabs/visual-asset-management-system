/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as workstationService from "./workstations";
import * as catalogService from "./catalog";
import { type EdhRequestError, mayHaveTakenEffect } from "./outcome";
import { isStartStalled } from "../labels";
import type {
    CapacityReservationView,
    CatalogLeasePolicy,
    CreateWorkstationRequest,
    EdhCatalogImage,
    EdhInstanceType,
    OsSignIn,
    WorkstationConnection,
    WorkstationRecord,
} from "../types";

// Query key factory, local to this module so its keys never collide with the orchestration module's.
export const qk = {
    workstations: () => ["edh", "workstations"] as const,
    catalogImages: () => ["edh", "catalogImages"] as const,
    instanceTypes: () => ["edh", "instanceTypes"] as const,
    catalogLease: () => ["edh", "catalogLease"] as const,
    catalogCapacityReservations: () => ["edh", "catalogCapacityReservations"] as const,
};

/** Poll cadence while any listed workstation is still changing state. */
export const WORKSTATION_POLL_TRANSITIONAL_MS = 5000;
/** Poll cadence once every listed workstation is settled: EDH can still stop one on idle. */
export const WORKSTATION_POLL_STABLE_MS = 20000;

/**
 * A row still moving towards a steady state: one the service reports `starting` or `stopping`
 * (`activity`), a launch EDH has not reported back on, or an active desktop EDH does not yet report
 * connectable. A start the sweep found stalled (`isStartStalled`) is CREATING but not moving: nothing
 * changes until the user stops it or EDH finishes, so it does not hold the list at the fast cadence.
 * Nor does a running workstation whose remote display session has ended (`connectionUnavailableReason`):
 * it is not starting, and stays as it is until the user stops it.
 */
export function isTransitionalWorkstation(
    row: Pick<WorkstationRecord, "state" | "connectionAvailable" | "activity" | "lastError"> &
        Partial<Pick<WorkstationRecord, "connectionUnavailableReason">>
): boolean {
    return (
        !!row.activity ||
        (row.state === "CREATING" && !isStartStalled(row)) ||
        (row.state === "ACTIVE" && !row.connectionAvailable && !row.connectionUnavailableReason)
    );
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
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (sessionId: string) =>
            callService<WorkstationConnection>(() =>
                workstationService.getWorkstationConnection(sessionId)
            ),
        // A refused connect may have recorded why on the row (a running desktop whose remote display
        // session has ended), so the list is re-read and the row's Connect shows the reason.
        onError: () => queryClient.invalidateQueries({ queryKey: qk.workstations() }),
        gcTime: 0,
    });
}

/**
 * The desktop sign-in of one of the caller's running workstations, requested when its dialog
 * opens: a Windows password is a desktop credential, so the answer is never kept in the query
 * cache, and a settled call leaves the mutation cache as soon as no dialog observes it.
 */
export function useOsSignIn() {
    return useMutation({
        mutationFn: (sessionId: string) =>
            callService<OsSignIn>(() => workstationService.getOsSignIn(sessionId)),
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
        // The start is accepted (202) and runs in the background: the answered record, `activity`
        // `starting`, replaces its row at once, so the badge reads Starting before the next poll.
        onSuccess: (record) => {
            if (!record?.bootstrapId) return;
            // The credential outcome belongs to this answer, not to the listed row.
            const row: WorkstationRecord = { ...record };
            delete row.sessionBootstrap;
            queryClient.setQueryData<WorkstationRecord[]>(qk.workstations(), (rows) =>
                rows?.map((r) => (r.bootstrapId === row.bootstrapId ? { ...r, ...row } : r))
            );
        },
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

/**
 * The capacity reservations the caller may launch into. Read fresh each time the wizard opens: the
 * free counts move with every launch and stop.
 */
export function useEdhCatalogCapacityReservations(enabled = true) {
    return useQuery({
        queryKey: qk.catalogCapacityReservations(),
        queryFn: () =>
            callService<CapacityReservationView[]>(() =>
                catalogService.listEdhCapacityReservations()
            ),
        enabled,
        staleTime: 0,
        // A caller the route refuses launches on-demand; there is nothing to retry towards.
        retry: false,
    });
}
