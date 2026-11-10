/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createAppQueryClient, isFinalStatus, shouldRetryQuery } from "./queryRetry";
import { useExecutionDetails } from "../features/orchestration/api/queries";

// The real API client, orchestration services and query hook run against a mocked fetch; only the
// session layer is stubbed, with a live session, so a 403 is a decision and not an expired login.
const mockEnsure = jest.fn();
const mockLogout = jest.fn();
jest.mock("./sessionManager", () => ({
    ensureValidSession: (...a: any[]) => mockEnsure(...a),
    logoutExpired: (...a: any[]) => mockLogout(...a),
}));
jest.mock("./authTokenUtils", () => ({
    getDualAuthorizationHeader: async () => "Bearer good",
}));

function response(status: number, body: any): Response {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: `HTTP ${status}`,
        headers: { get: () => null },
        json: async () => body,
        text: async () => JSON.stringify(body),
    } as unknown as Response;
}

describe("shouldRetryQuery", () => {
    it.each([400, 401, 403, 404, 409, 410, 422])("does not retry a %i", (status) => {
        expect(isFinalStatus(status)).toBe(true);
        expect(shouldRetryQuery(0, { status })).toBe(false);
    });

    it.each([408, 429, 500, 502, 503, 504])("retries a %i once", (status) => {
        expect(shouldRetryQuery(0, { status })).toBe(true);
        expect(shouldRetryQuery(1, { status })).toBe(false);
    });

    it("retries a failure without a status once", () => {
        expect(shouldRetryQuery(0, new TypeError("Failed to fetch"))).toBe(true);
        expect(shouldRetryQuery(0, new Error("Request failed"))).toBe(true);
        expect(shouldRetryQuery(0, null)).toBe(true);
        expect(shouldRetryQuery(1, new TypeError("Failed to fetch"))).toBe(false);
    });
});

describe("app query client on the execution details read (#1270)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockEnsure.mockResolvedValue(true);
        localStorage.setItem("api_path", "https://api.example.test/");
    });

    function renderDetails() {
        const client = createAppQueryClient();
        const wrapper = ({ children }: { children: React.ReactNode }) => (
            <QueryClientProvider client={client}>{children}</QueryClientProvider>
        );
        const hook = renderHook(
            () => useExecutionDetails("exec-1", { includeSubExecutions: true }),
            { wrapper }
        );
        return { ...hook, client };
    }

    it.each([
        [403, "Not Authorized"],
        [404, "Execution not found"],
    ])("a %i fails at once after one request", async (status, message) => {
        const fetchMock = jest.fn().mockResolvedValue(response(status, { message }));
        (global.fetch as any) = fetchMock;
        const started = Date.now();
        const { result, client } = renderDetails();

        await waitFor(() => expect(result.current.isError).toBe(true));
        // Well under the one-second delay the retry would have waited.
        expect(Date.now() - started).toBeLessThan(900);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(String(result.current.error)).toBe(`Error: ${message}`);
        expect((result.current.error as any).status).toBe(status);
        // A decision on a live session: no sign-out.
        expect(mockLogout).not.toHaveBeenCalled();
        client.clear();
    });

    it("a 503 is still re-sent once before the error shows", async () => {
        const fetchMock = jest
            .fn()
            .mockResolvedValue(response(503, { message: "Service Unavailable" }));
        (global.fetch as any) = fetchMock;
        const { result, client } = renderDetails();

        await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 });
        expect(fetchMock).toHaveBeenCalledTimes(2);
        client.clear();
    });

    it("a network failure is still re-sent, and the retry can succeed", async () => {
        const fetchMock = jest
            .fn()
            .mockRejectedValueOnce(new TypeError("Failed to fetch"))
            .mockResolvedValue(
                response(200, { message: { executionId: "exec-1", executionStatus: "SUCCEEDED" } })
            );
        (global.fetch as any) = fetchMock;
        const { result, client } = renderDetails();

        await waitFor(() => expect(result.current.isSuccess).toBe(true), { timeout: 4000 });
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(result.current.data).toMatchObject({ executionId: "exec-1" });
        client.clear();
    });
});
