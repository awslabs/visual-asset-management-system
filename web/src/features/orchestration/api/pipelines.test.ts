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
    listAllPipelines,
    getPipeline,
    createPipeline,
    updatePipeline,
    archivePipeline,
    listTemplates,
    getTemplate,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    getTagSchema,
    setTagSchema,
} from "./pipelines";

describe("pipelines service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("listPipelines (one server page)", () => {
        it("listPipelines(db) hits database/{db}/pipelines and returns the page object", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ pipelineId: "p1" }], NextToken: "tok" },
            });
            const r = await listPipelines("db1");
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines", {});
            // Returns the unwrapped page { Items, NextToken? } so useInfiniteQuery can page.
            expect(r).toEqual([true, { Items: [{ pipelineId: "p1" }], NextToken: "tok" }]);
        });

        it("listPipelines() without databaseId hits pipelines", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: { Items: [{ pipelineId: "p1" }] },
            });
            const r = await listPipelines();
            expect(apiClient.get).toHaveBeenCalledWith("pipelines", {});
            expect(r).toEqual([true, { Items: [{ pipelineId: "p1" }] }]);
        });

        it("listPipelines(db, params) sends pagination query params", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
            await listPipelines("db1", { pageSize: "50", startingToken: "tok" });
            expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines", {
                queryStringParameters: { pageSize: "50", startingToken: "tok" },
            });
        });
    });

    describe("listAllPipelines (drains all pages)", () => {
        it("pages to exhaustion and returns a flat array", async () => {
            (apiClient.get as jest.Mock)
                .mockResolvedValueOnce({
                    message: { Items: [{ pipelineId: "p1" }], NextToken: "t2" },
                })
                .mockResolvedValueOnce({ message: { Items: [{ pipelineId: "p2" }] } });
            const r = await listAllPipelines("db1", true);
            expect(r).toEqual([true, [{ pipelineId: "p1" }, { pipelineId: "p2" }]]);
            expect(apiClient.get).toHaveBeenCalledTimes(2);
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
        it("posts to database/{db}/pipelines and returns { pipeline, warnings }", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ message: { pipelineId: "p1" } });
            const r = await createPipeline({ databaseId: "db1" } as any);
            expect(apiClient.post).toHaveBeenCalledWith("database/db1/pipelines", {
                body: { databaseId: "db1" },
            });
            expect(r).toEqual([true, { pipeline: { pipelineId: "p1" }, warnings: [] }]);
        });

        it("threads the top-level warnings array through the save result", async () => {
            const warning =
                "pipeline 'P' requires a template and is part of auto-triggered workflow 'db1:wf1' (trigger 'fileUpload'), but that trigger has not chosen a default template for it.";
            (apiClient.post as jest.Mock).mockResolvedValue({
                message: { pipelineId: "p1" },
                warnings: [warning],
            });
            const r = await createPipeline({ databaseId: "db1" } as any);
            expect(r).toEqual([true, { pipeline: { pipelineId: "p1" }, warnings: [warning] }]);
        });
    });

    describe("updatePipeline", () => {
        it("puts to database/{db}/pipelines/{id} and returns { pipeline, warnings }", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            const r = await updatePipeline("db1", "p1", { pipelineName: "new" });
            expect(apiClient.put).toHaveBeenCalledWith("database/db1/pipelines/p1", {
                body: { pipelineName: "new" },
            });
            expect(r).toEqual([true, { pipeline: "updated", warnings: [] }]);
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

    describe("deleteTemplate", () => {
        it("deletes the template path", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ message: "deleted" });
            await deleteTemplate("db1", "p1", "t1");
            expect(apiClient.del).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates/t1",
                {}
            );
        });
    });

    describe("getTagSchema", () => {
        it("unwraps the fields array from the tagSchema response object", async () => {
            // Backend returns a TagSchemaResponseModel object with the array under `.fields`.
            (apiClient.get as jest.Mock).mockResolvedValue({
                message: {
                    pipelineDatabaseId: "db1",
                    pipelineId: "p1",
                    templateId: "t1",
                    fields: [{ tagKey: "k1", type: "string" }],
                },
            });
            const r = await getTagSchema("db1", "p1", "t1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates/t1/tagSchema"
            );
            expect(r).toEqual([true, [{ tagKey: "k1", type: "string" }]]);
        });
    });

    describe("setTagSchema", () => {
        it("puts a { fields } object to database/{db}/pipelines/{pid}/templates/{tid}/tagSchema", async () => {
            (apiClient.put as jest.Mock).mockResolvedValue({ message: "updated" });
            const fields = [{ tagKey: "k1", type: "string" as const }];
            await setTagSchema("db1", "p1", "t1", fields);
            expect(apiClient.put).toHaveBeenCalledWith(
                "database/db1/pipelines/p1/templates/t1/tagSchema",
                { body: { fields } }
            );
        });
    });
});
