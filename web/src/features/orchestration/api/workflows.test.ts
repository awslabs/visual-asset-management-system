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
    listWorkflows,
    getWorkflow,
    createWorkflow,
    updateWorkflow,
    archiveWorkflow,
    listTriggers,
    setTrigger,
    deleteTrigger,
} from "./workflows";

describe("workflows service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("listWorkflows", () => {
        it("listWorkflows(db) hits database/{db}/workflows", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowId: "w1" }] },
            });
            const r = await listWorkflows("db1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/workflows", expect.anything());
            expect(r).toEqual([true, [{ workflowId: "w1" }]]);
        });

        it("listWorkflows() without databaseId hits workflows", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowId: "w1" }] },
            });
            const r = await listWorkflows();
            expect(apiClient.get).toHaveBeenCalledWith("workflows", expect.anything());
            expect(r).toEqual([true, [{ workflowId: "w1" }]]);
        });

        it("listWorkflows with includeArchived passes query param", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
            await listWorkflows("db1", true);
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/workflows", {
                queryStringParameters: { includeArchived: "true" },
            });
        });
    });

    describe("getWorkflow", () => {
        it("calls database/{db}/workflows/{id}", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { workflowId: "w1" } });
            const r = await getWorkflow("db1", "w1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/workflows/w1");
            expect(r[0]).toBe(true);
        });
    });

    describe("createWorkflow", () => {
        it("posts to database/{db}/workflows", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: { workflowId: "w1" } });
            const r = await createWorkflow({ databaseId: "db1" } as any);
            expect(apiClient.post).toHaveBeenCalledWith("database/db1/workflows", {
                body: { databaseId: "db1" },
            });
            expect(r[0]).toBe(true);
        });
    });

    describe("updateWorkflow", () => {
        it("puts to database/{db}/workflows/{id}", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            await updateWorkflow("db1", "w1", { workflowName: "new" });
            expect(apiClient.put).toHaveBeenCalledWith("database/db1/workflows/w1", {
                body: { workflowName: "new" },
            });
        });
    });

    describe("archiveWorkflow", () => {
        it("deletes the workflow path", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "archived" });
            await archiveWorkflow("db1", "w1");
            expect(apiClient.del).toHaveBeenCalledWith("database/db1/workflows/w1", {});
        });
    });

    describe("listTriggers", () => {
        it("hits database/{db}/workflows/{wid}/triggers", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ triggerType: "fileUpload" }] },
            });
            const r = await listTriggers("db1", "w1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/workflows/w1/triggers",
                expect.anything()
            );
            expect(r).toEqual([true, [{ triggerType: "fileUpload" }]]);
        });
    });

    describe("setTrigger", () => {
        it("puts to database/{db}/workflows/{wid}/triggers/{triggerType}", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            const r = await setTrigger("db1", "w1", "fileUpload", { enabled: true } as any);
            expect(apiClient.put).toHaveBeenCalledWith(
                "database/db1/workflows/w1/triggers/fileUpload",
                { body: { enabled: true } }
            );
            expect(r[0]).toBe(true);
        });
    });

    describe("deleteTrigger", () => {
        it("deletes the trigger path", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "deleted" });
            await deleteTrigger("db1", "w1", "fileUpload");
            expect(apiClient.del).toHaveBeenCalledWith(
                "database/db1/workflows/w1/triggers/fileUpload",
                {}
            );
        });
    });
});
