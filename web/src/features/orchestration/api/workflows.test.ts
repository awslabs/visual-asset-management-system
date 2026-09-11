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
    listAllWorkflows,
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

    describe("listWorkflows (one server page)", () => {
        it("listWorkflows(db) hits database/{db}/workflows and returns the page object", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowId: "w1" }], NextToken: "tok" },
            });
            const r = await listWorkflows("db1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/workflows", {});
            expect(r).toEqual([true, { Items: [{ workflowId: "w1" }], NextToken: "tok" }]);
        });

        it("listWorkflows() without databaseId hits workflows", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ workflowId: "w1" }] },
            });
            const r = await listWorkflows();
            expect(apiClient.get).toHaveBeenCalledWith("workflows", {});
            expect(r).toEqual([true, { Items: [{ workflowId: "w1" }] }]);
        });

        it("listWorkflows(db, params) sends pagination query params", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
            await listWorkflows("db1", { pageSize: "50", startingToken: "tok" });
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/workflows", {
                queryStringParameters: { pageSize: "50", startingToken: "tok" },
            });
        });
    });

    describe("listAllWorkflows (drains all pages)", () => {
        it("pages to exhaustion and returns a flat array", async () => {
            (apiClient.get as jest.Mock)
                .mockResolvedValueOnce({
                    message: { Items: [{ workflowId: "w1" }], NextToken: "t2" },
                })
                .mockResolvedValueOnce({ message: { Items: [{ workflowId: "w2" }] } });
            const r = await listAllWorkflows("db1", true);
            expect(r).toEqual([true, [{ workflowId: "w1" }, { workflowId: "w2" }]]);
            expect(apiClient.get).toHaveBeenCalledTimes(2);
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
                message: { Items: [{ triggerType: "fileUpload", enabled: true }] },
            });
            const r = await listTriggers("db1", "w1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/workflows/w1/triggers",
                expect.anything()
            );
            expect(r[0]).toBe(true);
            expect((r[1] as any[])[0].triggerType).toBe("fileUpload");
        });

        it("flattens triggerConfig so filters/defaults survive a load→save round-trip", async () => {
            // Response nests the settings under triggerConfig (backend TriggerResponseModel shape).
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: {
                    Items: [
                        {
                            triggerType: "fileUpload",
                            enabled: true,
                            triggerConfig: {
                                inputFileFilters: { allow: ["*.glb"], exclude: ["*.tmp"] },
                                defaultTemplateIds: { "GLOBAL:p1": "t1" },
                            },
                        },
                    ],
                },
            });
            const [, data] = await listTriggers("db1", "w1");
            const trigger = (data as any[])[0];
            // The editor reads these flat — they must be populated, not undefined.
            expect(trigger.inputFileFilters).toEqual({ allow: ["*.glb"], exclude: ["*.tmp"] });
            expect(trigger.defaultTemplateIds).toEqual({ "GLOBAL:p1": "t1" });
            expect(trigger.enabled).toBe(true);
        });
    });

    describe("setTrigger", () => {
        it("percent-encodes a suffixed trigger key so it is not read as a URL fragment", async () => {
            // A trigger key may be "type#triggerId". A raw '#' is a fragment delimiter, so an
            // unencoded key would send only "fileUpload" and act on the WRONG trigger — a sibling.
            (apiClient.put as jest.Mock).mockResolvedValue({});
            await setTrigger("db1", "w1", "fileUpload#nightly", {} as any);
            expect((apiClient.put as jest.Mock).mock.calls[0][0]).toBe(
                "database/db1/workflows/w1/triggers/fileUpload%23nightly"
            );

            (apiClient.del as jest.Mock).mockResolvedValue({});
            await deleteTrigger("db1", "w1", "fileUpload#nightly");
            expect((apiClient.del as jest.Mock).mock.calls[0][0]).toBe(
                "database/db1/workflows/w1/triggers/fileUpload%23nightly"
            );
        });

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
