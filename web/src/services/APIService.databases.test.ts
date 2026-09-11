/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchAllDatabases } from "./APIService";
import { apiClient, ApiError } from "./apiClient";

// Mock the apiClient so we can simulate API success/failure without auth/network.
jest.mock("./apiClient", () => {
    class MockApiError extends Error {
        status: number;
        body: any;
        constructor(message: string, status: number, body?: any) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.body = body;
        }
    }
    return {
        ApiError: MockApiError,
        apiClient: { get: jest.fn() },
    };
});

const mockGet = apiClient.get as jest.Mock;

describe("fetchAllDatabases error contract", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("returns the array of items on success", async () => {
        mockGet.mockResolvedValue({ Items: [{ databaseId: "db1" }, { databaseId: "db2" }] });
        const result = await fetchAllDatabases();
        expect(result).toEqual([{ databaseId: "db1" }, { databaseId: "db2" }]);
    });

    it("returns an empty array when there are genuinely zero databases", async () => {
        mockGet.mockResolvedValue({ Items: [] });
        const result = await fetchAllDatabases();
        expect(Array.isArray(result)).toBe(true);
        expect(result).toHaveLength(0);
    });

    it("returns the error message STRING (not []) when the API throws a 403", async () => {
        mockGet.mockRejectedValue(new ApiError("Forbidden", 403, { message: "Forbidden" }));
        const result = await fetchAllDatabases();
        // ListPage routes a non-empty string to setError(); an array would be treated as data.
        expect(typeof result).toBe("string");
        expect(result).toBe("Forbidden");
    });

    it("returns a non-empty error string for a generic thrown error", async () => {
        mockGet.mockRejectedValue(new Error("Network down"));
        const result = await fetchAllDatabases();
        expect(typeof result).toBe("string");
        expect((result as string).length).toBeGreaterThan(0);
    });
});
