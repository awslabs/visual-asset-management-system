/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as service from "./amiRecipes";
import {
    AMI_POLL_MS,
    computeAmiRefetchInterval,
    useEdhAmiBuilds,
    useEdhAmiRecipeMutations,
    useEdhAmiRecipes,
} from "./amiRecipeQueries";
import type { EdhAmiRecipeRequest } from "../types";

jest.mock("./amiRecipes", () => ({
    listAmiRecipes: jest.fn(),
    listAmiBuilds: jest.fn(),
    createAmiRecipe: jest.fn(),
    updateAmiRecipe: jest.fn(),
    deleteAmiRecipe: jest.fn(),
    startAmiBuild: jest.fn(),
    registerAmiBuild: jest.fn(),
    retireAmiBuild: jest.fn(),
}));

describe("computeAmiRefetchInterval", () => {
    it("polls while any recipe's latest build is moving and stops otherwise", () => {
        expect(computeAmiRefetchInterval(undefined)).toBe(false);
        expect(computeAmiRefetchInterval([{ latestBuild: { status: "AVAILABLE" } } as any])).toBe(
            false
        );
        expect(computeAmiRefetchInterval([{ latestBuild: { status: "BUILDING" } } as any])).toBe(
            AMI_POLL_MS
        );
        expect(computeAmiRefetchInterval([{ status: "QUEUED" } as any])).toBe(AMI_POLL_MS);
    });
});

describe("useEdhAmiRecipeMutations", () => {
    type Mutations = ReturnType<typeof useEdhAmiRecipeMutations>;
    const BODY = { displayName: "Mine" } as unknown as EdhAmiRecipeRequest;
    const CASES: Array<{
        name: string;
        call: keyof typeof service;
        run: (mutations: Mutations) => Promise<unknown>;
        readsBuilds: boolean;
    }> = [
        {
            name: "create",
            call: "createAmiRecipe",
            run: (m) => m.create.mutateAsync(BODY),
            readsBuilds: false,
        },
        {
            name: "update",
            call: "updateAmiRecipe",
            run: (m) => m.update.mutateAsync({ recipeId: "mine", body: BODY }),
            readsBuilds: true,
        },
        {
            name: "remove",
            call: "deleteAmiRecipe",
            run: (m) => m.remove.mutateAsync("mine"),
            readsBuilds: true,
        },
        {
            name: "startBuild",
            call: "startAmiBuild",
            run: (m) => m.startBuild.mutateAsync("mine"),
            readsBuilds: true,
        },
        {
            name: "registerBuild",
            call: "registerAmiBuild",
            run: (m) => m.registerBuild.mutateAsync({ recipeId: "mine", version: 7 }),
            readsBuilds: true,
        },
        {
            name: "retireBuild",
            call: "retireAmiBuild",
            run: (m) => m.retireBuild.mutateAsync({ recipeId: "mine", version: 7 }),
            readsBuilds: true,
        },
    ];

    const LISTED = [true, { Items: [] }];

    /** A pending answer and the function that gives it. */
    const pending = () => {
        let answer: (value: unknown) => void = () => undefined;
        const promise = new Promise((resolve) => {
            answer = resolve;
        });
        return { promise, answer: (value: unknown) => answer(value) };
    };

    // The recipes list and one recipe's builds on screen, as the AMI Recipes tab and the builds
    // panel hold them, beside the mutations.
    const renderLists = async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const Wrapper = ({ children }: { children: React.ReactNode }) => (
            <QueryClientProvider client={client}>{children}</QueryClientProvider>
        );
        const { result } = renderHook(
            () => ({
                recipes: useEdhAmiRecipes(),
                builds: useEdhAmiBuilds("mine"),
                mutations: useEdhAmiRecipeMutations(),
            }),
            { wrapper: Wrapper }
        );
        await waitFor(() => expect(result.current.recipes.isSuccess).toBe(true));
        await waitFor(() => expect(result.current.builds.isSuccess).toBe(true));
        return result;
    };

    // Long enough for a mutation that did not wait for the lists to have resolved.
    const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

    // A reset, not a clear: an answer a case queued and did not use must not reach the next case.
    beforeEach(() => jest.resetAllMocks());

    it.each(CASES)(
        "$name resolves once the lists it changed are read again",
        async ({ call, run, readsBuilds }) => {
            (service[call] as jest.Mock).mockResolvedValue([true, {}]);
            const recipesRead = pending();
            const buildsRead = pending();
            (service.listAmiRecipes as jest.Mock)
                .mockResolvedValueOnce(LISTED)
                .mockReturnValueOnce(recipesRead.promise);
            (service.listAmiBuilds as jest.Mock)
                .mockResolvedValueOnce(LISTED)
                .mockReturnValueOnce(buildsRead.promise);
            const result = await renderLists();

            let resolved = false;
            const done = run(result.current.mutations).then(() => {
                resolved = true;
            });
            await waitFor(() => expect(service.listAmiRecipes).toHaveBeenCalledTimes(2));
            expect(service.listAmiBuilds).toHaveBeenCalledTimes(readsBuilds ? 2 : 1);
            await settle();
            expect(resolved).toBe(false);

            recipesRead.answer(LISTED);
            if (readsBuilds) {
                await settle();
                expect(resolved).toBe(false);
                buildsRead.answer(LISTED);
            }
            await done;
            expect(resolved).toBe(true);
        }
    );

    it("waits for lists read again that then fail, and resolves with its own answer", async () => {
        (service.retireAmiBuild as jest.Mock).mockResolvedValue([true, { version: 7 }]);
        const recipesRead = pending();
        const buildsRead = pending();
        (service.listAmiRecipes as jest.Mock)
            .mockResolvedValueOnce(LISTED)
            .mockReturnValueOnce(recipesRead.promise);
        (service.listAmiBuilds as jest.Mock)
            .mockResolvedValueOnce(LISTED)
            .mockReturnValueOnce(buildsRead.promise);
        const result = await renderLists();

        let resolved = false;
        const done = result.current.mutations.retireBuild
            .mutateAsync({ recipeId: "mine", version: 7 })
            .then((answer) => {
                resolved = true;
                return answer;
            });
        await waitFor(() => expect(service.listAmiBuilds).toHaveBeenCalledTimes(2));
        await settle();
        expect(resolved).toBe(false);

        recipesRead.answer([false, "EDH is unavailable."]);
        buildsRead.answer([false, "EDH is unavailable."]);
        await expect(done).resolves.toEqual({ version: 7 });
    });
});
