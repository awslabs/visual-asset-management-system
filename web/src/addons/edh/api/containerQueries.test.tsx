/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as containerService from "./containers";
import { qk } from "./queries";
import {
    CONTAINER_JOBS_POLL_MS,
    containerQk,
    useContainerCatalog,
    useContainerJobMutations,
    useContainerJobs,
} from "./containerQueries";
import { EdhRequestError } from "./outcome";

jest.mock("./containers", () => ({
    getContainerCatalog: jest.fn(),
    listContainerJobs: jest.fn(),
    submitContainerJob: jest.fn(),
    killContainerJob: jest.fn(),
}));

const wrapperFor = (client: QueryClient) => {
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "QueryWrapper";
    return Wrapper;
};

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

// The bodies the add-on's handlers answer with, as the service layer rejects with them.
const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const UNAVAILABLE = "EDH is unavailable. Try again later.";

describe("containerQk", () => {
    it("namespaces every key under edh and apart from the workstation keys", () => {
        expect(containerQk.catalog()).toEqual(["edh", "containerCatalog"]);
        expect(containerQk.jobs("eks")).toEqual(["edh", "containerJobs", "eks"]);
        expect(containerQk.jobs("eks", "edh-eks-a")).toEqual([
            "edh",
            "containerJobs",
            "eks",
            "edh-eks-a",
        ]);
        expect(containerQk.catalog()).not.toEqual(qk.catalogImages());
        expect(containerQk.jobs("batch")).not.toEqual(qk.workstations());
    });
});

describe("useContainerCatalog / useContainerJobs", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("resolves the catalog through the service", async () => {
        (containerService.getContainerCatalog as jest.Mock).mockResolvedValue([
            true,
            { Items: [{ kind: "jobQueue", jobQueueName: "gpu-queue" }], eksEnabled: false },
        ]);
        const { result } = renderHook(() => useContainerCatalog(), {
            wrapper: wrapperFor(newClient()),
        });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(result.current.data?.Items).toHaveLength(1);
        expect(result.current.data?.eksEnabled).toBe(false);
    });

    it("polls the job list at a fixed cadence", async () => {
        (containerService.listContainerJobs as jest.Mock).mockResolvedValue([true, []]);
        const client = newClient();
        const { result } = renderHook(() => useContainerJobs("batch"), {
            wrapper: wrapperFor(client),
        });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        const query = client.getQueryCache().find({ queryKey: containerQk.jobs("batch") });
        if (!query) throw new Error("the batch jobs query is not in the cache");
        // The observer's options sit on the query at run time; the query's static type omits them.
        const { refetchInterval } = query.options as { refetchInterval?: number };
        expect(refetchInterval).toBe(CONTAINER_JOBS_POLL_MS);
        expect(CONTAINER_JOBS_POLL_MS).toBe(15000);
    });

    it("surfaces a service refusal as the query error", async () => {
        (containerService.listContainerJobs as jest.Mock).mockResolvedValue([false, "Forbidden"]);
        const { result } = renderHook(() => useContainerJobs("batch"), {
            wrapper: wrapperFor(newClient()),
        });
        await waitFor(() => expect(result.current.isError).toBe(true));
        expect((result.current.error as Error).message).toBe("Forbidden");
        expect(containerService.listContainerJobs).toHaveBeenCalledWith("batch", undefined);
    });

    it("forwards an eks scope to the service and keys the query on its cluster", async () => {
        (containerService.listContainerJobs as jest.Mock).mockResolvedValue([true, []]);
        const client = newClient();
        const { result } = renderHook(
            () => useContainerJobs("eks", true, { cluster: "edh-eks-a" }),
            {
                wrapper: wrapperFor(client),
            }
        );
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(containerService.listContainerJobs).toHaveBeenCalledWith("eks", {
            cluster: "edh-eks-a",
        });
        expect(client.getQueryData(containerQk.jobs("eks", "edh-eks-a"))).toEqual([]);
    });

    it("issues nothing while disabled", async () => {
        const { result } = renderHook(() => useContainerJobs("eks", false), {
            wrapper: wrapperFor(newClient()),
        });
        expect(result.current.fetchStatus).toBe("idle");
        expect(containerService.listContainerJobs).not.toHaveBeenCalled();
    });
});

describe("useContainerJobMutations", () => {
    const setup = () => {
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useContainerJobMutations(), {
            wrapper: wrapperFor(client),
        });
        return { result, invalidate };
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("invalidates the backend's job list after a submit succeeds", async () => {
        (containerService.submitContainerJob as jest.Mock).mockResolvedValue([
            true,
            {
                jobId: "j-1",
                jobHandle: "batch:j-1",
                backend: "batch",
                jobDefinition: "sim-train",
                jobQueue: "gpu-queue",
                name: "render-1",
                message: "ok",
            },
        ]);
        const { result, invalidate } = setup();

        await result.current.submitJob.mutateAsync({
            jobDefinition: "sim-train",
            jobQueue: "gpu-queue",
            name: "render-1",
            backend: "batch",
        });

        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: containerQk.jobs("batch") })
        );
    });

    it("rejects with the service message and leaves the lists alone when a kill is refused", async () => {
        (containerService.killContainerJob as jest.Mock).mockResolvedValue([
            false,
            "Job not found",
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.killJob.mutateAsync({ jobId: "j-x", backend: "batch" })
        ).rejects.toThrow("Job not found");

        expect(invalidate).not.toHaveBeenCalled();
    });

    it("re-reads the backend's job list after a submit EDH did not confirm", async () => {
        (containerService.submitContainerJob as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true }),
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.submitJob.mutateAsync({
                jobDefinition: "sim-train",
                jobQueue: "gpu-queue",
                name: "render-1",
                backend: "batch",
            })
        ).rejects.toMatchObject({ status: 502 });

        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: containerQk.jobs("batch") })
        );
    });

    it("leaves the lists alone after a plain 502 on a kill", async () => {
        (containerService.killContainerJob as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE }),
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.killJob.mutateAsync({ jobId: "j-1", backend: "batch" })
        ).rejects.toThrow(UNAVAILABLE);

        expect(invalidate).not.toHaveBeenCalled();
    });

    it("passes the id and the backend through kill and invalidates that backend's list", async () => {
        (containerService.killContainerJob as jest.Mock).mockResolvedValue([
            true,
            { jobId: "j-1", backend: "eks", message: "ok" },
        ]);
        const { result, invalidate } = setup();

        await result.current.killJob.mutateAsync({
            jobId: "j-1",
            backend: "eks",
            scope: { cluster: "edh-eks-a" },
        });

        expect(containerService.killContainerJob).toHaveBeenCalledWith("j-1", "eks", {
            cluster: "edh-eks-a",
        });
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: containerQk.jobs("eks") })
        );
    });
});
