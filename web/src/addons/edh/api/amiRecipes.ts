/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import type { EdhAmiBuild, EdhAmiRecipe, EdhAmiRecipeRequest, EdhListResponse } from "../types";

const RECIPES_PATH = "addon/edh/admin/ami-recipes";
const recipePath = (recipeId: string, ...rest: Array<string | number>) =>
    [RECIPES_PATH, encodeURIComponent(recipeId), ...rest.map(String)].join("/");

export const listAmiRecipes = () =>
    toTuple<EdhListResponse<EdhAmiRecipe>>(() => apiClient.get(RECIPES_PATH));
export const getAmiRecipe = (recipeId: string) =>
    toTuple<EdhAmiRecipe>(() => apiClient.get(recipePath(recipeId)));
export const createAmiRecipe = (body: EdhAmiRecipeRequest) =>
    toTuple<EdhAmiRecipe>(() => apiClient.post(RECIPES_PATH, { body }));
export const updateAmiRecipe = (recipeId: string, body: EdhAmiRecipeRequest) =>
    toTuple<EdhAmiRecipe>(() => apiClient.put(recipePath(recipeId), { body }));
export const deleteAmiRecipe = (recipeId: string) =>
    toTuple<{ recipeId: string; deleted: boolean; warnings: string[] }>(() =>
        apiClient.del(recipePath(recipeId), {})
    );
export const listAmiBuilds = (recipeId: string) =>
    toTuple<EdhListResponse<EdhAmiBuild>>(() => apiClient.get(recipePath(recipeId, "builds")));
export const startAmiBuild = (recipeId: string) =>
    toTuple<EdhAmiBuild>(() => apiClient.post(recipePath(recipeId, "builds"), {}));
export const registerAmiBuild = (recipeId: string, version: number) =>
    toTuple<EdhAmiBuild>(() =>
        apiClient.post(recipePath(recipeId, "builds", version, "register"), {})
    );
export const retireAmiBuild = (recipeId: string, version: number) =>
    toTuple<EdhAmiBuild>(() => apiClient.del(recipePath(recipeId, "builds", version), {}));
