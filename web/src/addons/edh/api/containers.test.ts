/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../../../services/apiClient", () => ({
    apiClient: {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        del: jest.fn(),
    },
}));

import { apiClient } from "../../../services/apiClient";
import {
    getContainerCatalog,
    killContainerJob,
    listContainerJobs,
    submitContainerJob,
} from "./containers";
import { EdhRequestError } from "./outcome";

const SUBMIT_BODY = {
    jobDefinition: "sim-train",
    jobQueue: "gpu-queue",
    name: "render-1",
    backend: "batch" as const,
    command: ["python", "main.py"],
    vcpus: 4,
};
const JOB = {
    jobId: "j-1",
    jobName: "render-1",
    status: "RUNNING",
    backend: "batch",
    edhJobDefinition: "sim-train",
    jobHandle: "batch:j-1",
};

describe("containers service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("getContainerCatalog", () => {
        it("reads addon/edh/containers/catalog and returns the Items and the EKS switch", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                Items: [{ kind: "jobDefinition", jobDefinitionName: "sim-train" }],
                eksEnabled: true,
            });
            const r = await getContainerCatalog();
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/containers/catalog");
            expect(r).toEqual([
                true,
                {
                    Items: [{ kind: "jobDefinition", jobDefinitionName: "sim-train" }],
                    eksEnabled: true,
                },
            ]);
        });

        it("reads an empty response as an empty catalog with EKS off", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({});
            expect(await getContainerCatalog()).toEqual([true, { Items: [], eksEnabled: false }]);
        });

        it("returns the failure tuple when the request is rejected", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(new Error("Forbidden"));
            expect(await getContainerCatalog()).toEqual([false, "Forbidden"]);
        });
    });

    describe("listContainerJobs", () => {
        it("reads addon/edh/containers/jobs with the backend as a query parameter", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ Items: [JOB] });
            const r = await listContainerJobs("batch");
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/containers/jobs", {
                queryStringParameters: { backend: "batch" },
            });
            expect(r).toEqual([true, [JOB]]);
        });

        it("sends the cluster and namespace of an eks scope", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ Items: [] });
            await listContainerJobs("eks", { cluster: "edh-eks-a", namespace: "team" });
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/containers/jobs", {
                queryStringParameters: { backend: "eks", cluster: "edh-eks-a", namespace: "team" },
            });
        });

        it("returns an empty array when the response carries no Items", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({});
            expect(await listContainerJobs("batch")).toEqual([true, []]);
        });
    });

    describe("submitContainerJob", () => {
        it("posts the body to addon/edh/containers/jobs and returns the response", async () => {
            const answer = {
                jobId: "j-1",
                jobHandle: "batch:j-1",
                backend: "batch",
                jobDefinition: "sim-train",
                jobQueue: "gpu-queue",
                name: "render-1",
                message: "ok",
            };
            (apiClient.post as jest.Mock).mockResolvedValue(answer);
            const r = await submitContainerJob(SUBMIT_BODY);
            expect(apiClient.post).toHaveBeenCalledWith("addon/edh/containers/jobs", {
                body: SUBMIT_BODY,
            });
            expect(r).toEqual([true, answer]);
        });

        it("returns the backend's message on a rejection", async () => {
            (apiClient.post as jest.Mock).mockRejectedValue(
                new Error("VAMS General Error: The eks backend is not enabled in this deployment")
            );
            const [ok, err] = await submitContainerJob({ ...SUBMIT_BODY, backend: "eks" });
            expect(ok).toBe(false);
            expect((err as EdhRequestError).message).toBe(
                "VAMS General Error: The eks backend is not enabled in this deployment"
            );
        });

        it("keeps the status and parsed body of a rejection", async () => {
            const body = {
                message: "EDH did not confirm the request in time.",
                outcomeUnknown: true,
            };
            (apiClient.post as jest.Mock).mockRejectedValue(
                Object.assign(new Error(body.message), { status: 502, body })
            );
            const [, err] = await submitContainerJob(SUBMIT_BODY);
            expect(err).toBeInstanceOf(EdhRequestError);
            expect(err).toMatchObject({ status: 502, body });
        });
    });

    describe("killContainerJob", () => {
        it("uses DELETE on addon/edh/containers/jobs/{jobId} with the backend as a query parameter", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({
                jobId: "j-1",
                backend: "batch",
                message: "ok",
            });
            const r = await killContainerJob("j-1", "batch");
            expect(apiClient.del).toHaveBeenCalledWith("addon/edh/containers/jobs/j-1", {
                queryStringParameters: { backend: "batch" },
            });
            expect(apiClient.post).not.toHaveBeenCalled();
            expect(r).toEqual([true, { jobId: "j-1", backend: "batch", message: "ok" }]);
        });

        it("sends the cluster of an eks scope and defaults its namespace to the service", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({});
            await killContainerJob("render-eks", "eks", { cluster: "edh-eks-a" });
            expect(apiClient.del).toHaveBeenCalledWith("addon/edh/containers/jobs/render-eks", {
                queryStringParameters: { backend: "eks", cluster: "edh-eks-a" },
            });
        });

        it("keeps the status and parsed body of a rejection", async () => {
            (apiClient.del as jest.Mock).mockRejectedValue(
                Object.assign(new Error("Endpoint request timed out"), { status: 504, body: null })
            );
            const [ok, err] = await killContainerJob("j-1", "batch");
            expect(ok).toBe(false);
            expect(err).toBeInstanceOf(EdhRequestError);
            expect(err).toMatchObject({ status: 504 });
        });

        it("URL-encodes a job id that carries reserved characters", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({});
            await killContainerJob("job/with:colon", "batch");
            expect(apiClient.del).toHaveBeenCalledWith(
                "addon/edh/containers/jobs/job%2Fwith%3Acolon",
                expect.anything()
            );
        });
    });
});
