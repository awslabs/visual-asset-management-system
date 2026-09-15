/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("./apiClient", () => ({
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

import { apiClient } from "./apiClient";
import {
    approveCascade,
    rejectCascade,
    fetchCascade,
    fetchAuditLog,
    fetchAssetAuditHistory,
    fetchEvaluationHistory,
    fetchQuarantinedAssets,
    fetchDatabaseComplianceOverview,
    getDatabaseBindings,
} from "./ComplianceService";

const post = apiClient.post as jest.Mock;
const get = apiClient.get as jest.Mock;

describe("cascade approve/reject reasons", () => {
    beforeEach(() => jest.clearAllMocks());

    it("rejectCascade posts the reason", async () => {
        post.mockResolvedValue({ message: "Cascade rejected", cascadeId: "c-1" });
        const result = await rejectCascade("c-1", "duplicate trigger");
        expect(post).toHaveBeenCalledWith("compliance/cascades/c-1/reject", {
            body: { reason: "duplicate trigger" },
        });
        expect(result).toEqual([true, "Cascade rejected"]);
    });

    it("approveCascade posts the reason and returns the async state", async () => {
        post.mockResolvedValue({
            message: "Cascade approved",
            cascadeId: "c-1",
            state: "executing",
        });
        const result = await approveCascade("c-1", "reviewed");
        expect(post).toHaveBeenCalledWith("compliance/cascades/c-1/approve", {
            body: { reason: "reviewed" },
        });
        expect(result).toEqual([
            true,
            { message: "Cascade approved", cascadeId: "c-1", state: "executing" },
        ]);
    });

    it("approveCascade without a reason posts an empty body", async () => {
        post.mockResolvedValue({ message: "Cascade approved", cascadeId: "c-1" });
        await approveCascade("c-1");
        expect(post).toHaveBeenCalledWith("compliance/cascades/c-1/approve", { body: {} });
    });

    it("fetchCascade reads one cascade by id", async () => {
        get.mockResolvedValue({ cascadeId: "c-1", state: "completed" });
        const result = await fetchCascade("c-1");
        expect(get).toHaveBeenCalledWith("compliance/cascades/c-1", {});
        expect(result).toEqual([true, { cascadeId: "c-1", state: "completed" }]);
    });
});

describe("paged listings", () => {
    beforeEach(() => jest.clearAllMocks());

    it("fetchAuditLog forwards filters and paging and returns nextToken", async () => {
        get.mockResolvedValue({ entries: [{ entryId: "e1" }], NextToken: "tok-2" });
        const result = await fetchAuditLog({
            eventType: "compliance_check",
            maxItems: 50,
            startingToken: "tok-1",
        });
        expect(get).toHaveBeenCalledWith("compliance/audit", {
            queryStringParameters: {
                eventType: "compliance_check",
                maxItems: "50",
                startingToken: "tok-1",
            },
        });
        expect(result).toEqual([true, { entries: [{ entryId: "e1" }], nextToken: "tok-2" }]);
    });

    it("fetchAuditLog omits nextToken on the last page and empty params", async () => {
        get.mockResolvedValue({ entries: [] });
        const result = await fetchAuditLog();
        expect(get).toHaveBeenCalledWith("compliance/audit", { queryStringParameters: {} });
        expect(result).toEqual([true, { entries: [], nextToken: undefined }]);
    });

    it("fetchAssetAuditHistory pages the asset route", async () => {
        get.mockResolvedValue({ entries: [{ entryId: "e1" }], NextToken: "tok-2" });
        const result = await fetchAssetAuditHistory("db1", "a1", {
            maxItems: 10,
            startingToken: "tok-1",
        });
        expect(get).toHaveBeenCalledWith("compliance/audit/db1/a1", {
            queryStringParameters: { maxItems: "10", startingToken: "tok-1" },
        });
        expect(result).toEqual([true, { entries: [{ entryId: "e1" }], nextToken: "tok-2" }]);
    });

    it("fetchEvaluationHistory returns { evaluations, nextToken }", async () => {
        get.mockResolvedValue({ evaluations: [{ evaluationId: "ev1" }], NextToken: "tok-2" });
        const result = await fetchEvaluationHistory("db1", "a1", { maxItems: 20 });
        expect(get).toHaveBeenCalledWith("compliance/evaluations/db1/a1", {
            queryStringParameters: { maxItems: "20" },
        });
        expect(result).toEqual([
            true,
            { evaluations: [{ evaluationId: "ev1" }], nextToken: "tok-2" },
        ]);
    });

    it("fetchQuarantinedAssets returns the page and nextToken", async () => {
        get.mockResolvedValue({
            quarantinedAssets: [{ databaseId: "db1", assetId: "a1" }],
            NextToken: "tok-2",
        });
        const result = await fetchQuarantinedAssets({ maxItems: 50, startingToken: "tok-1" });
        expect(get).toHaveBeenCalledWith("compliance/quarantine", {
            queryStringParameters: { maxItems: "50", startingToken: "tok-1" },
        });
        expect(result).toEqual([
            true,
            { quarantinedAssets: [{ databaseId: "db1", assetId: "a1" }], nextToken: "tok-2" },
        ]);
    });

    it("fetchDatabaseComplianceOverview keeps the summary and pages the assets", async () => {
        get.mockResolvedValue({
            databaseId: "db1",
            totalAssets: 120,
            summary: { compliant: 100, non_compliant: 20 },
            assets: [{ assetId: "a1" }],
            NextToken: "tok-2",
        });
        const result = await fetchDatabaseComplianceOverview("db1", { maxItems: 50 });
        expect(get).toHaveBeenCalledWith("compliance/state/db1", {
            queryStringParameters: { maxItems: "50" },
        });
        expect(result).toEqual([
            true,
            {
                databaseId: "db1",
                totalAssets: 120,
                summary: { compliant: 100, non_compliant: 20 },
                assets: [{ assetId: "a1" }],
                nextToken: "tok-2",
            },
        ]);
    });

    it("getDatabaseBindings surfaces the override page and total", async () => {
        get.mockResolvedValue({
            databaseId: "db1",
            databaseSchema: "std",
            complianceAutoEval: true,
            assetOverrides: [{ assetId: "a1", schemaName: "special" }],
            assetOverrideCount: 3,
            NextToken: "tok-2",
        });
        const result = await getDatabaseBindings("db1", { startingToken: "tok-1" });
        expect(get).toHaveBeenCalledWith("compliance/bind/db1", {
            queryStringParameters: { startingToken: "tok-1" },
        });
        expect(result).toEqual([
            true,
            {
                databaseId: "db1",
                databaseSchema: "std",
                complianceAutoEval: true,
                assetOverrides: [{ assetId: "a1", schemaName: "special" }],
                assetOverrideCount: 3,
                nextToken: "tok-2",
            },
        ]);
    });

    it("returns the backend error message as a failed tuple", async () => {
        get.mockResolvedValue({ message: "Invalid pagination token error" });
        const result = await fetchAuditLog({ startingToken: "garbage" });
        expect(result).toEqual([false, "Invalid pagination token error"]);
    });
});
