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
    executeWorkflow,
    listExecutionsGlobal,
    listExecutionsForAsset,
    getExecutionDetails,
    getExecutionDetailsMetadata,
    getExecutionDetailsFiles,
    getExecutionResults,
    getExecutionLogs,
    abortExecution,
    rerunExecution,
    permanentDeleteExecution,
} from "./executions";

describe("executions service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("executeWorkflow", () => {
        it("executeWorkflow posts to workflows/{wdb}/{wid}/execute", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: "Execution started" });
            const body = { inputFiles: [] };
            await executeWorkflow("db1", "wf1", body);
            expect(apiClient.post).toHaveBeenCalledWith("workflows/db1/wf1/execute", { body });
        });
    });

    describe("listExecutionsGlobal", () => {
        it("listExecutionsGlobal() hits workflows/executions without params", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowExecutionId: "e1" }] },
            });
            const r = await listExecutionsGlobal();
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions", {});
            // Returns the unwrapped page object { Items, NextToken? } so useInfiniteQuery can page.
            expect(r).toEqual([true, { Items: [{ workflowExecutionId: "e1" }] }]);
        });

        it("listExecutionsGlobal(params) sends queryStringParameters", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [] },
            });
            await listExecutionsGlobal({ status: "RUNNING" });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions", {
                queryStringParameters: { status: "RUNNING" },
            });
        });
    });

    describe("listExecutionsForAsset", () => {
        it("listExecutionsForAsset hits database/{db}/assets/{assetId}/workflows/executions", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowExecutionId: "e1" }] },
            });
            const r = await listExecutionsForAsset("db1", "a1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/assets/a1/workflows/executions",
                {}
            );
            // Returns the unwrapped page object { Items, NextToken? } so useInfiniteQuery can page.
            expect(r).toEqual([true, { Items: [{ workflowExecutionId: "e1" }] }]);
        });

        it("listExecutionsForAsset with params sends queryStringParameters", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [] },
            });
            await listExecutionsForAsset("db1", "a1", { limit: "10" });
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/assets/a1/workflows/executions",
                { queryStringParameters: { limit: "10" } }
            );
        });
    });

    describe("getExecutionDetails", () => {
        it("getExecutionDetails hits workflows/executions/{id}/details", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { workflowExecutionId: "e1" },
            });
            const r = await getExecutionDetails("e1");
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details");
            expect(r).toEqual([true, { workflowExecutionId: "e1" }]);
        });

        it("sends includeSubExecutions as a query parameter when asked", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { workflowExecutionId: "e1", pipelines: [] },
            });
            await getExecutionDetails("e1", { includeSubExecutions: "true" });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details", {
                queryStringParameters: { includeSubExecutions: "true" },
            });
        });
    });

    describe("getExecutionDetailsMetadata", () => {
        it("hits workflows/executions/{id}/details/metadata and unwraps the page", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ assetId: "a1" }], collection: "input", NextToken: "t2" },
            });
            const r = await getExecutionDetailsMetadata("e1", {
                collection: "input",
                pageSize: "200",
            });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details/metadata", {
                queryStringParameters: { collection: "input", pageSize: "200" },
            });
            expect(r).toEqual([
                true,
                { Items: [{ assetId: "a1" }], collection: "input", NextToken: "t2" },
            ]);
        });

        it("carries the continuation token through verbatim", async () => {
            // The token is opaque base64 the backend round-trips; it must reach the API unaltered.
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [], collection: "output" },
            });
            await getExecutionDetailsMetadata("e1", {
                collection: "output",
                startingToken: "eyJzdGVwSW5kZXgiOiAxfQ==",
            });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details/metadata", {
                queryStringParameters: {
                    collection: "output",
                    startingToken: "eyJzdGVwSW5kZXgiOiAxfQ==",
                },
            });
        });

        it("reports a rejected read as [false, message]", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(new Error("Forbidden"));
            const r = await getExecutionDetailsMetadata("e1", { collection: "input" });
            expect(r).toEqual([false, "Forbidden"]);
        });

        it("sends no query parameters when none are given", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [], collection: "input" },
            });
            await getExecutionDetailsMetadata("e1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "workflows/executions/e1/details/metadata",
                {}
            );
        });
    });

    describe("getExecutionDetailsFiles", () => {
        it("hits workflows/executions/{id}/details/files and unwraps the page", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ relativeFilePath: "/o.glb" }], collection: "output" },
            });
            const r = await getExecutionDetailsFiles("e1", {
                collection: "output",
                pageSize: "200",
            });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details/files", {
                queryStringParameters: { collection: "output", pageSize: "200" },
            });
            expect(r).toEqual([
                true,
                { Items: [{ relativeFilePath: "/o.glb" }], collection: "output" },
            ]);
        });

        it("carries the continuation token through verbatim", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [], collection: "input" },
            });
            await getExecutionDetailsFiles("e1", {
                collection: "input",
                startingToken: "eyJzdGVwSW5kZXgiOiAwfQ==",
            });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details/files", {
                queryStringParameters: {
                    collection: "input",
                    startingToken: "eyJzdGVwSW5kZXgiOiAwfQ==",
                },
            });
        });
    });

    describe("getExecutionResults", () => {
        it("hits workflows/executions/{id}/details/results and unwraps the result", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: {
                    resultContent: "hello",
                    resultContentTruncated: false,
                    resultRowCount: 1,
                    resultContentBytes: 5,
                },
            });
            const r = await getExecutionResults("e1");
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/details/results");
            expect(r).toEqual([
                true,
                {
                    resultContent: "hello",
                    resultContentTruncated: false,
                    resultRowCount: 1,
                    resultContentBytes: 5,
                },
            ]);
        });
    });

    describe("getExecutionLogs", () => {
        it("getExecutionLogs without params hits workflows/executions/{id}/logs", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { logs: [] },
            });
            const r = await getExecutionLogs("e1");
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/logs", {});
            expect(r).toEqual([true, { logs: [] }]);
        });

        it("getExecutionLogs with params sends queryStringParameters", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { logs: [] },
            });
            await getExecutionLogs("e1", { limit: "50" });
            expect(apiClient.get).toHaveBeenCalledWith("workflows/executions/e1/logs", {
                queryStringParameters: { limit: "50" },
            });
        });
    });

    describe("abortExecution", () => {
        it("abortExecution without groupId sends empty options", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "Execution aborted" });
            await abortExecution("e1");
            expect(apiClient.del).toHaveBeenCalledWith("workflows/executions/e1", {});
        });

        it("abortExecution with groupId sends queryStringParameters", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "Execution aborted" });
            await abortExecution("e1", "g1");
            expect(apiClient.del).toHaveBeenCalledWith("workflows/executions/e1", {
                queryStringParameters: { groupId: "g1" },
            });
        });

        // The abort handler puts `warnings` BESIDE `message`. `unwrapMessage` returns `resp.message`
        // whenever a response carries one, so the plain `toTuple` reader returned the string
        // "Execution aborted" and dropped the array — the one signal that a Batch or Deadline Cloud
        // job is still running after the execution reads ABORTED.
        const SUBPROCESS_WARNING =
            "Batch job 1234 could not be terminated and may still be running.";

        it("keeps the sub-process warnings returned alongside the message", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({
                message: "Execution aborted",
                warnings: [SUBPROCESS_WARNING],
            });
            const [ok, result] = await abortExecution("e1");
            expect(ok).toBe(true);
            expect(typeof result).not.toBe("string");
            expect((result as any).warnings).toEqual([SUBPROCESS_WARNING]);
            expect((result as any).message).toBe("Execution aborted");
        });

        it("reports no warnings for a clean abort rather than undefined", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "Execution aborted" });
            const [ok, result] = await abortExecution("e1");
            expect(ok).toBe(true);
            expect((result as any).warnings).toEqual([]);
        });

        // `notAbortable` sits beside `message` as well. The board reads `result.notAbortable` to
        // lead its warning with what was left running, so the tuple must carry it unchanged.
        const NOT_ABORTABLE = {
            resourceType: "slurmJob",
            label: "Mock Slurm job",
            stageName: "MockTrain",
            locator: "slurm-00001",
        };
        const NOT_ABORTABLE_WARNING =
            "Sub-process of type 'slurmJob' (slurm-00001) could not be aborted: VAMS cannot stop " +
            "this resource type; it may still be running.";

        it("keeps notAbortable and warnings from a single abort", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({
                message: "Execution aborted",
                warnings: [NOT_ABORTABLE_WARNING],
                notAbortable: [NOT_ABORTABLE],
            });
            const [ok, result] = await abortExecution("e1");
            expect(ok).toBe(true);
            expect(result).toEqual({
                message: "Execution aborted",
                warnings: [NOT_ABORTABLE_WARNING],
                notAbortable: [NOT_ABORTABLE],
            });
        });

        it("keeps a group abort's member-attributed notAbortable and warnings", async () => {
            const group = {
                groupId: "g1",
                results: [
                    { executionId: "e1", status: "aborted" },
                    { executionId: "e2", status: "aborted" },
                ],
            };
            (apiClient.del as jest.Mock).mockResolvedValue({
                message: group,
                warnings: [`e2: ${NOT_ABORTABLE_WARNING}`],
                notAbortable: [{ executionId: "e2", ...NOT_ABORTABLE }],
            });
            const [ok, result] = await abortExecution("e1", "g1");
            expect(ok).toBe(true);
            expect(result).toEqual({
                message: group,
                warnings: [`e2: ${NOT_ABORTABLE_WARNING}`],
                notAbortable: [{ executionId: "e2", ...NOT_ABORTABLE }],
            });
        });

        it("omits notAbortable from a clean abort", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({
                message: "Execution aborted",
                notAbortable: [],
            });
            const [, result] = await abortExecution("e1");
            expect(result).toEqual({ message: "Execution aborted", warnings: [] });
        });
    });

    describe("rerunExecution", () => {
        it("rerunExecution without executionGroupId sends empty body", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: "Execution rerun" });
            await rerunExecution("e1");
            expect(apiClient.post).toHaveBeenCalledWith("workflows/executions/e1/rerun", {
                body: {},
            });
        });

        it("rerunExecution with executionGroupId sends body", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: "Execution rerun" });
            await rerunExecution("e1", "g1");
            expect(apiClient.post).toHaveBeenCalledWith("workflows/executions/e1/rerun", {
                body: { executionGroupId: "g1" },
            });
        });
    });

    describe("permanentDeleteExecution", () => {
        it("permanentDelete always sends confirmDelete true", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "deleted" });
            await permanentDeleteExecution("e1");
            expect(apiClient.del).toHaveBeenCalledWith("workflows/executions/e1/permanent", {
                body: { confirmDelete: true },
            });
        });
    });
});
