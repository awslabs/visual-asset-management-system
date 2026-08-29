/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The page resolves two management modes from the allowed-API-routes list. When that list is
 * unavailable the gate must fail closed: opening the tenant-wide admin surface for an
 * undeterminable user renders the admin form, calls the admin API (which 403s), and leaves a
 * self-service user with no "My Keys" segment to switch to — no way to their own keys at all.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { appCache } from "../../services/appCache";
import {
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../../common/constants/authRoutes";
import ApiKeys from "./ApiKeys";

const mockFetchApiKeys = jest.fn();
const mockFetchUserApiKeys = jest.fn();
const mockFetchAllowedApiRoutes = jest.fn();
jest.mock("../../services/APIService", () => ({
    fetchApiKeys: (...args: any[]) => mockFetchApiKeys(...args),
    fetchUserApiKeys: (...args: any[]) => mockFetchUserApiKeys(...args),
    deleteApiKey: jest.fn(),
    deleteUserApiKey: jest.fn(),
    fetchAllowedApiRoutes: (...args: any[]) => mockFetchAllowedApiRoutes(...args),
}));

jest.mock("./CreateApiKey", () => ({ __esModule: true, default: () => null }));
jest.mock("./UpdateApiKey", () => ({ __esModule: true, default: () => null }));

const cacheRoutes = (...paths: string[]) =>
    appCache.setItemWithExpiry(
        ALLOWED_API_ROUTES_CACHE_KEY,
        { userId: "u1", routes: paths.map((path) => ({ path, methods: ["GET"] })) },
        ALLOWED_API_ROUTES_CACHE_TTL_MILLIS
    );

describe("ApiKeys mode resolution", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        mockFetchApiKeys.mockResolvedValue({ Items: [] });
        mockFetchUserApiKeys.mockResolvedValue({ Items: [] });
    });

    it("fails closed when the allowed-routes list is unavailable", async () => {
        // Cache empty and the direct fetch failed with a real HTTP error, so nothing is known
        // about this user's access.
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error", 500]);

        render(<ApiKeys />);

        await waitFor(() =>
            expect(screen.getByText("Could not determine your permissions")).toBeInTheDocument()
        );
        // No key listing was attempted in either mode, and no admin controls were offered.
        expect(mockFetchApiKeys).not.toHaveBeenCalled();
        expect(mockFetchUserApiKeys).not.toHaveBeenCalled();
        expect(screen.queryByTestId("create-api-key-button")).not.toBeInTheDocument();
        expect(screen.queryByText("API Keys")).not.toBeInTheDocument();
    });

    it("retries the permission check when asked, and shows the keys once it resolves", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValueOnce([false, "Internal Server Error", 500]);
        mockFetchAllowedApiRoutes.mockResolvedValue([
            true,
            {
                userId: "u1",
                routes: [
                    { path: "/auth/api-keys", methods: ["GET"] },
                    { path: "/auth/user/api-keys", methods: ["GET"] },
                ],
            },
        ]);

        render(<ApiKeys />);

        const retry = await screen.findByTestId("retry-resolve-api-key-modes-button");
        await userEvent.click(retry);

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(screen.queryByText("Could not determine your permissions")).not.toBeInTheDocument();
    });

    it("positive control: an absent allowed-routes endpoint (404) keeps the admin default", async () => {
        // A backend that does not serve the listing at all has nothing to gate on, so the
        // historical behaviour stands. This is what distinguishes "endpoint absent" from
        // "check failed" — without it, the fail-closed assertion above would also hold here.
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Not Found", 404]);

        render(<ApiKeys />);

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(screen.getByText("API Keys")).toBeInTheDocument();
        expect(screen.queryByText("Could not determine your permissions")).not.toBeInTheDocument();
    });

    it("positive control: a self-service-only user gets user mode, not admin", async () => {
        cacheRoutes("/auth/user/api-keys");

        render(<ApiKeys />);

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchApiKeys).not.toHaveBeenCalled();
        expect(mockFetchAllowedApiRoutes).not.toHaveBeenCalled(); // answered from cache
        expect(screen.getByText("My API Keys")).toBeInTheDocument();
    });
});
