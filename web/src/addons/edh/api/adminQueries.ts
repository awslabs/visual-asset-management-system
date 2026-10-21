/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as adminService from "./admin";
import type {
    EdhAdminImage,
    EdhAdminInstance,
    EdhCapacity,
    EdhHealth,
    EdhImageKind,
    RegisterSimulationImageRequest,
    RegisterVdiImageRequest,
} from "./admin";
import { type EdhRequestError, mayHaveTakenEffect } from "./outcome";
import type { LeasePolicy } from "../types";

const STALE_INSTANCES_KEY = "edhAdminStaleInstances";

// Query key factory for the admin tabs.
export const adminQk = {
    images: () => ["edhAdminImages"] as const,
    instances: () => ["edhAdminInstances"] as const,
    // The prefix every idleMinutes variant of the stale view sits under.
    staleInstancesAll: () => [STALE_INSTANCES_KEY] as const,
    staleInstances: (idleMinutes: number) => [STALE_INSTANCES_KEY, idleMinutes] as const,
    capacity: () => ["edhAdminCapacity"] as const,
    health: () => ["edhAdminHealth"] as const,
    settings: () => ["edhAdminSettings"] as const,
};

// Instance views poll on a fixed cadence: an administrator watches other users' sessions, none of
// which are transitional because of an action taken on this page.
export const ADMIN_INSTANCES_REFETCH_MS = 20000;

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

export function useEdhAdminImages(enabled = true) {
    return useQuery({
        queryKey: adminQk.images(),
        queryFn: async (): Promise<EdhAdminImage[]> =>
            (await callService(() => adminService.listAdminImages())).Items,
        enabled,
    });
}

export function useEdhAdminInstances(enabled = true) {
    return useQuery({
        queryKey: adminQk.instances(),
        queryFn: async (): Promise<EdhAdminInstance[]> =>
            (await callService(() => adminService.listAdminInstances())).Items,
        enabled,
        refetchInterval: ADMIN_INSTANCES_REFETCH_MS,
    });
}

export function useEdhStaleInstances(idleMinutes: number, enabled = true) {
    return useQuery({
        queryKey: adminQk.staleInstances(idleMinutes),
        queryFn: async (): Promise<EdhAdminInstance[]> =>
            (await callService(() => adminService.listStaleInstances(idleMinutes))).Items,
        enabled,
        refetchInterval: ADMIN_INSTANCES_REFETCH_MS,
    });
}

export function useEdhCapacity(enabled = true) {
    return useQuery({
        queryKey: adminQk.capacity(),
        queryFn: () => callService<EdhCapacity>(() => adminService.getCapacity()),
        enabled,
    });
}

export function useEdhHealth(enabled = true) {
    return useQuery({
        queryKey: adminQk.health(),
        queryFn: () => callService<EdhHealth>(() => adminService.getHealth()),
        enabled,
        // A 502 is the answer, not a transient to retry.
        retry: false,
    });
}

export function useEdhAdminMutations() {
    const queryClient = useQueryClient();
    const invalidateImages = () => {
        queryClient.invalidateQueries({ queryKey: adminQk.images() });
    };
    const invalidateInstances = () => {
        queryClient.invalidateQueries({ queryKey: adminQk.instances() });
        queryClient.invalidateQueries({ queryKey: adminQk.staleInstancesAll() });
        queryClient.invalidateQueries({ queryKey: adminQk.capacity() });
    };
    // A mutation EDH did not confirm may still have happened, so its views are re-read after one as
    // after a success.
    const unlessFailed = (invalidate: () => void) => (_data: unknown, err: unknown) => {
        if (mayHaveTakenEffect(err)) invalidate();
    };

    const registerVdi = useMutation({
        mutationFn: (body: RegisterVdiImageRequest) =>
            callService(() => adminService.registerVdiImage(body)),
        onSettled: unlessFailed(invalidateImages),
    });
    const registerSimulation = useMutation({
        mutationFn: (body: RegisterSimulationImageRequest) =>
            callService(() => adminService.registerSimulationImage(body)),
        onSettled: unlessFailed(invalidateImages),
    });
    const deleteImage = useMutation({
        mutationFn: ({ kind, imageId }: { kind: EdhImageKind; imageId: string }) =>
            callService(() =>
                kind === "vdi"
                    ? adminService.deleteVdiImage(imageId)
                    : adminService.deleteSimulationImage(imageId)
            ),
        onSettled: unlessFailed(invalidateImages),
    });
    const stopInstance = useMutation({
        mutationFn: (sessionId: string) =>
            callService(() => adminService.stopAdminInstance(sessionId)),
        onSettled: unlessFailed(invalidateInstances),
    });
    const deactivateInstance = useMutation({
        mutationFn: (sessionId: string) =>
            callService(() => adminService.deactivateAdminInstance(sessionId)),
        onSettled: unlessFailed(invalidateInstances),
    });

    return { registerVdi, registerSimulation, deleteImage, stopInstance, deactivateInstance };
}

export function useEdhAdminSettings(enabled = true) {
    return useQuery({
        queryKey: adminQk.settings(),
        queryFn: async (): Promise<LeasePolicy> =>
            (await callService(() => adminService.getAdminSettings())).lease,
        enabled,
    });
}

export function useUpdateEdhAdminSettings() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (body: Partial<LeasePolicy>) =>
            callService(() => adminService.updateAdminSettings(body)),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: adminQk.settings() }),
    });
}
