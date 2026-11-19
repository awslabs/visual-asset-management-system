/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../../../../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
jest.mock("../../../../services/apiClient", () => ({
    ...jest.requireActual("../../../../services/apiClient"),
    apiClient: { get: jest.fn() },
}));

import { renderHook, waitFor } from "@testing-library/react";
import { apiClient, NETWORK_FAILURE_MESSAGE } from "../../../../services/apiClient";
import { useAssetVersions } from "./useAssetVersions";

/** The Versions tab shows the reason the versions list could not be read. */
describe("useAssetVersions", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => jest.restoreAllMocks());

    it("shows the network message when the versions request gets no response", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));

        const { result } = renderHook(() => useAssetVersions("db1", "a1"));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.error).toBe(NETWORK_FAILURE_MESSAGE);
        expect(result.current.versions).toEqual([]);
    });

    it("shows the service's message when the API refuses the request", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new Error("Not Authorized"));

        const { result } = renderHook(() => useAssetVersions("db1", "a1"));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.error).toBe("Not Authorized");
    });

    it("lists the versions the API returns", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({
            versions: [{ Version: "1", DateModified: "2026-11-01T00:00:00Z" }],
        });

        const { result } = renderHook(() => useAssetVersions("db1", "a1"));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.error).toBeNull();
        expect(result.current.versions.map((version: any) => version.Version)).toEqual(["1"]);
    });
});
