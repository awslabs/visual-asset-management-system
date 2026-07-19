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
