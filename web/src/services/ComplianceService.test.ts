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
    fetchComplianceSchemas,
    fetchComplianceState,
    evaluateAssetCompliance,
    getDatabaseBindings,
    revokeException,
    complianceSchemaFormat,
    erroredRuleNames,
    ComplianceSchema,
    EvaluationRecord,
} from "./ComplianceService";

const post = apiClient.post as jest.Mock;
const get = apiClient.get as jest.Mock;
const del = apiClient.del as jest.Mock;

describe("schema format", () => {
    beforeEach(() => jest.clearAllMocks());

    const schema = (overrides: Partial<ComplianceSchema>): ComplianceSchema => ({
        schemaName: "s",
        schemaBody: {},
        ...overrides,
    });

    it("reads the record's own schemaFormat first", () => {
        expect(
            complianceSchemaFormat(
                schema({ schemaFormat: "legacy", schemaBody: { schemaFormat: "vams-rules-v1" } })
            )
        ).toBe("legacy");
        expect(complianceSchemaFormat(schema({ schemaFormat: "vams-rules-v1" }))).toBe(
            "vams-rules-v1"
        );
    });

    it("derives the format from the body when the record does not carry it", () => {
        expect(
            complianceSchemaFormat(schema({ schemaBody: { schemaFormat: "vams-rules-v1" } }))
        ).toBe("vams-rules-v1");
        expect(complianceSchemaFormat(schema({ schemaBody: { type: "object" } }))).toBe("legacy");
    });

    it("fetchComplianceSchemas passes the top-level schemaFormat through", async () => {
        get.mockResolvedValue({
            schemas: [
                { schemaName: "modern", schemaFormat: "vams-rules-v1", schemaBody: {} },
                { schemaName: "old", schemaFormat: "legacy", schemaBody: { type: "object" } },
            ],
        });
        const result = await fetchComplianceSchemas();
        expect(result[0]).toBe(true);
        expect((result[1] as ComplianceSchema[]).map((s) => s.schemaFormat)).toEqual([
            "vams-rules-v1",
            "legacy",
        ]);
    });
});

describe("evaluation errors", () => {
    beforeEach(() => jest.clearAllMocks());

    const evaluation = (overrides: Partial<EvaluationRecord>): EvaluationRecord => ({
        evaluationId: "ev",
        databaseId: "db1",
        assetId: "a1",
        schemaName: "std",
        result: "compliant",
        evaluatedAt: "2026-01-01T00:00:00Z",
        ...overrides,
    });

    it("erroredRuleNames prefers the record's errorRules", () => {
        expect(
            erroredRuleNames(
                evaluation({
                    errorRules: ["geometry"],
                    ruleResults: [
                        {
                            ruleName: "other",
                            ruleType: "metadata",
                            enforcement: "warn",
                            passed: false,
                            status: "error",
                        },
                    ],
                })
            )
        ).toEqual(["geometry"]);
    });

    it("erroredRuleNames reads status error out of JSON-encoded or listed rule results", () => {
        const results = [
            {
                ruleName: "geometry",
                ruleType: "pipeline",
                enforcement: "quarantine",
                passed: false,
                status: "error",
            },
            {
                ruleName: "owner",
                ruleType: "metadata",
                enforcement: "warn",
                passed: false,
                status: "evaluated",
            },
            { ruleName: "links", ruleType: "relationship", enforcement: "inform", passed: true },
        ];
        expect(erroredRuleNames(evaluation({ ruleResults: JSON.stringify(results) }))).toEqual([
            "geometry",
        ]);
        expect(erroredRuleNames(evaluation({ ruleResults: results as any }))).toEqual(["geometry"]);
        expect(erroredRuleNames(evaluation({ ruleResults: "not json" }))).toEqual([]);
        expect(erroredRuleNames(evaluation({}))).toEqual([]);
    });

    it("fetchComplianceState returns the row with its lastEvaluationStatus", async () => {
        get.mockResolvedValue({
            databaseId: "db1",
            assetId: "a1",
            complianceState: "compliant",
            lastEvaluationStatus: "error",
        });
        const result = await fetchComplianceState("db1", "a1");
        expect(get).toHaveBeenCalledWith("compliance/state/db1/a1", {});
        expect(result).toEqual([
            true,
            {
                databaseId: "db1",
                assetId: "a1",
                complianceState: "compliant",
                lastEvaluationStatus: "error",
            },
        ]);
    });

    it("evaluateAssetCompliance returns the schema version, exception and rule-error flags", async () => {
        post.mockResolvedValue({
            message: "Evaluation completed",
            evaluationId: "ev-1",
            schemaName: "std",
            schemaVersion: 3,
            verdict: "compliant",
            complianceState: "compliant",
            ruleResults: [],
            pipelineRulesPending: 0,
            exceptionApplied: false,
            hasRuleErrors: true,
        });
        const result = await evaluateAssetCompliance("db1", "a1");
        expect(post).toHaveBeenCalledWith("compliance/evaluate/db1/a1", { body: {} });
        expect(result[0]).toBe(true);
        expect(result[1]).toMatchObject({
            schemaVersion: 3,
            exceptionApplied: false,
            hasRuleErrors: true,
        });
    });
});

describe("quarantine exceptions", () => {
    beforeEach(() => jest.clearAllMocks());

    it("revokeException DELETEs the exception route and returns the restored state", async () => {
        del.mockResolvedValue({
            message: "Exception revoked",
            databaseId: "db1",
            assetId: "a1",
            complianceState: "quarantined",
        });
        const result = await revokeException("db1", "a1");
        expect(del).toHaveBeenCalledWith("compliance/quarantine/db1/a1/exception", {});
        expect(result).toEqual([
            true,
            {
                message: "Exception revoked",
                databaseId: "db1",
                assetId: "a1",
                complianceState: "quarantined",
            },
        ]);
    });

    it("revokeException surfaces the 400 of a record without an active exception", async () => {
        del.mockRejectedValue(new Error("No exception is active"));
        const result = await revokeException("db1", "a1");
        expect(result).toEqual([false, "No exception is active"]);
    });
});

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
            summary: { compliant: 100, non_compliant: 18, exception: 2, error: 5 },
            assets: [{ assetId: "a1", lastEvaluationStatus: "error" }],
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
                summary: { compliant: 100, non_compliant: 18, exception: 2, error: 5 },
                assets: [{ assetId: "a1", lastEvaluationStatus: "error" }],
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
