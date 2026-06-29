/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient, ApiError } from "./apiClient";

const mockEnsure = jest.fn();
const mockLogout = jest.fn();
jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: (...a: any[]) => mockEnsure(...a),
    logoutExpired: (...a: any[]) => mockLogout(...a),
}));
const mockHeader = jest.fn();
jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: (...a: any[]) => mockHeader(...a),
}));

function jsonResponse(status: number, body: any): Response {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: `HTTP ${status}`,
        json: async () => body,
        text: async () => JSON.stringify(body),
    } as unknown as Response;
}

describe("apiClient backstop", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.setItem("api_path", "https://api.example.test/");
        mockHeader.mockResolvedValue("Bearer good");
    });

    it("returns JSON on success", async () => {
        (global.fetch as any) = jest.fn().mockResolvedValue(jsonResponse(200, { ok: 1 }));
        await expect(apiClient.get("thing")).resolves.toEqual({ ok: 1 });
    });

    it("on 403 with a DEAD session: logs out and throws", async () => {
        (global.fetch as any) = jest
            .fn()
            .mockResolvedValue(jsonResponse(403, { message: "Not Authorized" }));
        mockEnsure.mockResolvedValue(false);
        await expect(apiClient.get("thing")).rejects.toBeInstanceOf(ApiError);
        expect(mockLogout).toHaveBeenCalledTimes(1);
    });

    it("on 403 with an ALIVE session: surfaces error, no logout, no retry", async () => {
        const fetchMock = jest
            .fn()
            .mockResolvedValue(jsonResponse(403, { message: "Not Authorized" }));
        (global.fetch as any) = fetchMock;
        mockEnsure.mockResolvedValue(true);
        await expect(apiClient.get("thing")).rejects.toMatchObject({ status: 403 });
        expect(mockLogout).not.toHaveBeenCalled();
        expect(fetchMock).toHaveBeenCalledTimes(1); // surfaced, not retried
    });

    it("treats 401 the same as 403 (dead → logout)", async () => {
        (global.fetch as any) = jest.fn().mockResolvedValue(jsonResponse(401, {}));
        mockEnsure.mockResolvedValue(false);
        await expect(apiClient.get("thing")).rejects.toBeInstanceOf(ApiError);
        expect(mockLogout).toHaveBeenCalledTimes(1);
    });

    it("when the token fetch throws and session is alive: retries once and succeeds", async () => {
        mockHeader
            .mockRejectedValueOnce(new Error("no token"))
            .mockResolvedValueOnce("Bearer refreshed");
        mockEnsure.mockResolvedValue(true);
        (global.fetch as any) = jest.fn().mockResolvedValue(jsonResponse(200, { ok: 2 }));
        await expect(apiClient.get("thing")).resolves.toEqual({ ok: 2 });
    });

    it("when the token fetch throws and session is dead: logs out and throws", async () => {
        mockHeader.mockRejectedValue(new Error("no token"));
        mockEnsure.mockResolvedValue(false);
        (global.fetch as any) = jest.fn();
        await expect(apiClient.get("thing")).rejects.toBeTruthy();
        expect(mockLogout).toHaveBeenCalledTimes(1);
    });

    it("non-auth errors (500) surface without touching the session", async () => {
        (global.fetch as any) = jest.fn().mockResolvedValue(jsonResponse(500, { message: "boom" }));
        await expect(apiClient.get("thing")).rejects.toMatchObject({ status: 500 });
        expect(mockEnsure).not.toHaveBeenCalled();
        expect(mockLogout).not.toHaveBeenCalled();
    });

    it("token fetch throws twice with an ALIVE session: retries once then throws (no third attempt)", async () => {
        mockHeader.mockRejectedValue(new Error("no token"));
        mockEnsure.mockResolvedValue(true);
        (global.fetch as any) = jest.fn();
        await expect(apiClient.get("thing")).rejects.toThrow("no token");
        expect(mockHeader).toHaveBeenCalledTimes(2); // initial attempt + exactly one retry
        expect(global.fetch as any).not.toHaveBeenCalled(); // never reached fetch
    });

    it("on 401 with an ALIVE session: surfaces error, no logout, no retry (symmetry with 403)", async () => {
        const fetchMock = jest
            .fn()
            .mockResolvedValue(jsonResponse(401, { message: "Not Authorized" }));
        (global.fetch as any) = fetchMock;
        mockEnsure.mockResolvedValue(true);
        await expect(apiClient.get("thing")).rejects.toMatchObject({ status: 401 });
        expect(mockLogout).not.toHaveBeenCalled();
        expect(fetchMock).toHaveBeenCalledTimes(1); // surfaced, not retried
    });
});
