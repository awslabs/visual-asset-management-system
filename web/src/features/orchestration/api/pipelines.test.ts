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
    listPipelines,
    getPipeline,
    createPipeline,
    updatePipeline,
    archivePipeline,
    listTemplates,
    getTemplate,
    createTemplate,
    updateTemplate,
    archiveTemplate,
    getTagSchema,
    setTagSchema,
} from "./pipelines";

describe("pipelines service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("listPipelines", () => {
        it("listPipelines(db) hits database/{db}/pipelines", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ pipelineId: "p1" }] },
            });
            const r = await listPipelines("db1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines", expect.anything());
            expect(r).toEqual([true, [{ pipelineId: "p1" }]]);
        });

        it("listPipelines() without databaseId hits pipelines", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ pipelineId: "p1" }] },
            });
            const r = await listPipelines();
            expect(apiClient.get).toHaveBeenCalledWith("pipelines", expect.anything());
            expect(r).toEqual([true, [{ pipelineId: "p1" }]]);
        });

        it("listPipelines with includeArchived passes query param", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
            await listPipelines("db1", true);
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines", {
                queryStringParameters: { includeArchived: "true" },
            });
        });
    });

    describe("getPipeline", () => {
        it("calls database/{db}/pipelines/{id}", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { pipelineId: "p1" } });
            const r = await getPipeline("db1", "p1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines/p1");
            expect(r[0]).toBe(true);
        });
    });

    describe("createPipeline", () => {
        it("posts to database/{db}/pipelines", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: { pipelineId: "p1" } });
            const r = await createPipeline({ databaseId: "db1" } as any);
            expect(apiClient.post).toHaveBeenCalledWith("database/db1/pipelines", {
                body: { databaseId: "db1" },
            });
            expect(r[0]).toBe(true);
        });
    });

    describe("updatePipeline", () => {
        it("puts to database/{db}/pipelines/{id}", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            await updatePipeline("db1", "p1", { pipelineName: "new" });
            expect(apiClient.put).toHaveBeenCalledWith("database/db1/pipelines/p1", {
                body: { pipelineName: "new" },
            });
        });
    });

    describe("archivePipeline", () => {
        it("deletes the pipeline path", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "archived" });
            await archivePipeline("db1", "p1");
            expect(apiClient.del).toHaveBeenCalledWith("database/db1/pipelines/p1", {});
        });
    });

    describe("listTemplates", () => {
        it("hits database/{db}/pipelines/{pid}/templates", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ templateId: "t1" }] },
            });
            const r = await listTemplates("db1", "p1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates",
                expect.anything()
            );
            expect(r).toEqual([true, [{ templateId: "t1" }]]);
        });
    });

    describe("getTemplate", () => {
        it("calls database/{db}/pipelines/{pid}/templates/{tid}", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { templateId: "t1" } });
            const r = await getTemplate("db1", "p1", "t1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines/p1/templates/t1");
            expect(r[0]).toBe(true);
        });
    });

    describe("createTemplate", () => {
        it("posts to database/{db}/pipelines/{pid}/templates", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: { templateId: "t1" } });
            const r = await createTemplate("db1", "p1", { templateName: "t1" } as any);
            expect(apiClient.post).toHaveBeenCalledWith("database/db1/pipelines/p1/templates", {
                body: { templateName: "t1" },
            });
            expect(r[0]).toBe(true);
        });
    });

    describe("updateTemplate", () => {
        it("puts to database/{db}/pipelines/{pid}/templates/{tid}", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            await updateTemplate("db1", "p1", "t1", { templateName: "new" });
            expect(apiClient.put).toHaveBeenCalledWith("database/db1/pipelines/p1/templates/t1", {
                body: { templateName: "new" },
            });
        });
    });

    describe("archiveTemplate", () => {
        it("deletes the template path", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "archived" });
            await archiveTemplate("db1", "p1", "t1");
            expect(apiClient.del).toHaveBeenCalledWith("database/db1/pipelines/p1/templates/t1", {});
        });
    });

    describe("getTagSchema", () => {
        it("calls database/{db}/pipelines/{pid}/templates/{tid}/tagSchema", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: [{ tagKey: "k1", type: "string" }],
            });
            const r = await getTagSchema("db1", "p1", "t1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates/t1/tagSchema"
            );
            expect(r[0]).toBe(true);
        });
    });

    describe("setTagSchema", () => {
        it("puts fields array to database/{db}/pipelines/{pid}/templates/{tid}/tagSchema", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            const fields = [{ tagKey: "k1", type: "string" as const }];
            await setTagSchema("db1", "p1", "t1", fields);
            expect(apiClient.put).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates/t1/tagSchema",
                { body: fields }
            );
        });
    });
});
