/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("./apiClient", () => ({
    apiClient: { get: jest.fn() },
}));

import { apiClient } from "./apiClient";
import { fetchTags, fetchTagsForAsset, fetchTagTypesForAsset } from "./APIService";

describe("fetchTags scope params", () => {
    beforeEach(() => jest.clearAllMocks());

    it("passes databaseId as a query string parameter", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
        await fetchTags({ databaseId: "factory-db" });
        const [, init] = (apiClient.get as jest.Mock).mock.calls[0];
        expect(init.queryStringParameters.databaseId).toBe("factory-db");
    });

    it("passes scope as a query string parameter", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
        await fetchTags({ scope: "global" });
        const [, init] = (apiClient.get as jest.Mock).mock.calls[0];
        expect(init.queryStringParameters.scope).toBe("global");
    });

    it("omits scope params when called with no args", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
        await fetchTags();
        const firstCallInit = (apiClient.get as jest.Mock).mock.calls[0][1] || {};
        const qs = firstCallInit.queryStringParameters || {};
        expect(qs.databaseId).toBeUndefined();
        expect(qs.scope).toBeUndefined();
    });
});

describe("fetchTagsForAsset (asset tag picker scope)", () => {
    beforeEach(() => jest.clearAllMocks());

    it("requests GLOBAL tags and the asset's databaseId, and merges them", async () => {
        (apiClient.get as jest.Mock).mockImplementation((_path: string, init: any) => {
            const qs = init?.queryStringParameters || {};
            if (qs.scope === "global") {
                return Promise.resolve({ message: { Items: [{ tagName: "global-tag" }] } });
            }
            if (qs.databaseId === "factory-db") {
                return Promise.resolve({ message: { Items: [{ tagName: "factory-tag" }] } });
            }
            return Promise.resolve({ message: { Items: [] } });
        });

        const result = await fetchTagsForAsset({ databaseId: "factory-db" });

        // Both scopes were requested: GLOBAL + the asset's own database.
        const scopes = (apiClient.get as jest.Mock).mock.calls.map(
            ([, init]: any) => init.queryStringParameters
        );
        expect(scopes).toEqual(
            expect.arrayContaining([
                expect.objectContaining({ scope: "global" }),
                expect.objectContaining({ databaseId: "factory-db" }),
            ])
        );
        // No fetch was issued for any other database's tags.
        expect(scopes.every((qs: any) => !qs.databaseId || qs.databaseId === "factory-db")).toBe(
            true
        );
        // The merged list contains both the GLOBAL and the own-database tag.
        expect((result as any[]).map((t) => t.tagName).sort()).toEqual([
            "factory-tag",
            "global-tag",
        ]);
    });

    it("falls back to the full tag list when no databaseId is given", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ message: { Items: [] } });
        await fetchTagsForAsset();
        const qs = (apiClient.get as jest.Mock).mock.calls[0][1]?.queryStringParameters || {};
        expect(qs.databaseId).toBeUndefined();
        expect(qs.scope).toBeUndefined();
    });
});

// Required tag types drive the asset form's validation, so they must resolve in the same scope as
// the tag picker. Using the unscoped list demanded a tag for a required type belonging to another
// database, which the picker could never offer — the form became impossible to submit.
describe("fetchTagTypesForAsset (required tag type scope)", () => {
    beforeEach(() => jest.clearAllMocks());

    it("requests GLOBAL tag types and the asset's databaseId, and merges them", async () => {
        (apiClient.get as jest.Mock).mockImplementation((_path: string, init: any) => {
            const qs = init?.queryStringParameters || {};
            if (qs.scope === "global") {
                return Promise.resolve({
                    message: { Items: [{ tagTypeName: "Status", required: "True" }] },
                });
            }
            if (qs.databaseId === "factory-db") {
                return Promise.resolve({
                    message: { Items: [{ tagTypeName: "Line", required: "True" }] },
                });
            }
            return Promise.resolve({ message: { Items: [{ tagTypeName: "OtherDbType" }] } });
        });

        const result = await fetchTagTypesForAsset({ databaseId: "factory-db" });

        expect(Array.isArray(result)).toBe(true);
        expect((result as any[]).map((t) => t.tagTypeName).sort()).toEqual(["Line", "Status"]);
        // Nothing from another database may appear, or the form would require an unsatisfiable tag.
        expect((result as any[]).map((t) => t.tagTypeName)).not.toContain("OtherDbType");
    });

    it("falls back to the full list when no databaseId is given", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({
            message: { Items: [{ tagTypeName: "Status" }] },
        });

        await fetchTagTypesForAsset();

        const qs = (apiClient.get as jest.Mock).mock.calls[0][1]?.queryStringParameters || {};
        expect(qs.databaseId).toBeUndefined();
        expect(qs.scope).toBeUndefined();
    });

    it("surfaces a load failure rather than dropping a scope", async () => {
        (apiClient.get as jest.Mock).mockImplementation((_path: string, init: any) => {
            const qs = init?.queryStringParameters || {};
            if (qs.scope === "global") {
                return Promise.resolve({ message: "error: forbidden" });
            }
            return Promise.resolve({ message: { Items: [] } });
        });

        const result = await fetchTagTypesForAsset({ databaseId: "factory-db" });

        expect(Array.isArray(result)).toBe(false);
    });
});
