/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as service from "./amiRecipes";
import {
    ACTIVE_BUILD_STATUSES,
    type EdhAmiBuild,
    type EdhAmiRecipe,
    type EdhAmiRecipeRequest,
} from "../types";

export const amiQk = {
    recipes: () => ["edhAmiRecipes"] as const,
    builds: (recipeId: string) => ["edhAmiBuilds", recipeId] as const,
};

export const AMI_POLL_MS = 20000;

/** 20 s while any listed build is queued, building or registering; off otherwise. */
export function computeAmiRefetchInterval(
    rows: Array<EdhAmiRecipe | EdhAmiBuild> | undefined
): number | false {
    if (!rows || rows.length === 0) return false;
    const moving = rows.some((row) => {
        const status = "latestBuild" in row ? row.latestBuild?.status : (row as EdhAmiBuild).status;
        return !!status && ACTIVE_BUILD_STATUSES.includes(status);
    });
    return moving ? AMI_POLL_MS : false;
}

async function callService<T>(serviceFn: () => Promise<[boolean, T | string]>): Promise<T> {
    const [ok, data] = await serviceFn();
    if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
    return data as T;
}

export function useEdhAmiRecipes(enabled = true) {
    return useQuery({
        queryKey: amiQk.recipes(),
        queryFn: async (): Promise<EdhAmiRecipe[]> =>
            (await callService(() => service.listAmiRecipes())).Items,
        enabled,
        refetchInterval: (query: any) => computeAmiRefetchInterval(query.state.data),
    });
}

export function useEdhAmiBuilds(recipeId: string | null, enabled = true) {
    return useQuery({
        queryKey: amiQk.builds(recipeId ?? ""),
        queryFn: async (): Promise<EdhAmiBuild[]> =>
            (await callService(() => service.listAmiBuilds(recipeId as string))).Items,
        enabled: enabled && !!recipeId,
        refetchInterval: (query: any) => computeAmiRefetchInterval(query.state.data),
    });
}

export function useEdhAmiRecipeMutations() {
    const queryClient = useQueryClient();
    const invalidate = (recipeId?: string) => {
        queryClient.invalidateQueries({ queryKey: amiQk.recipes() });
        if (recipeId) queryClient.invalidateQueries({ queryKey: amiQk.builds(recipeId) });
    };
    const create = useMutation({
        mutationFn: (body: EdhAmiRecipeRequest) => callService(() => service.createAmiRecipe(body)),
        onSuccess: () => invalidate(),
    });
    const update = useMutation({
        mutationFn: ({ recipeId, body }: { recipeId: string; body: EdhAmiRecipeRequest }) =>
            callService(() => service.updateAmiRecipe(recipeId, body)),
        onSuccess: (_d, v) => invalidate(v.recipeId),
    });
    const remove = useMutation({
        mutationFn: (recipeId: string) => callService(() => service.deleteAmiRecipe(recipeId)),
        onSuccess: (_d, recipeId) => invalidate(recipeId),
    });
    const startBuild = useMutation({
        mutationFn: (recipeId: string) => callService(() => service.startAmiBuild(recipeId)),
        onSuccess: (_d, recipeId) => invalidate(recipeId),
    });
    const registerBuild = useMutation({
        mutationFn: ({ recipeId, version }: { recipeId: string; version: number }) =>
            callService(() => service.registerAmiBuild(recipeId, version)),
        onSuccess: (_d, v) => invalidate(v.recipeId),
    });
    const retireBuild = useMutation({
        mutationFn: ({ recipeId, version }: { recipeId: string; version: number }) =>
            callService(() => service.retireAmiBuild(recipeId, version)),
        onSuccess: (_d, v) => invalidate(v.recipeId),
    });
    return { create, update, remove, startBuild, registerBuild, retireBuild };
}
