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
import { API_KEY_LISTING_PAGE_SIZE } from "../../common/constants/apiKeys";
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

/**
 * The listing follows the endpoint's paging contract: a page that reports a NextToken offers a
 * next page, and moving to it issues a fresh request carrying that token. Without this the page
 * would show whatever the first response happened to hold and call the listing complete.
 */
describe("ApiKeys server-side paging", () => {
    const key = (id: string) => ({
        apiKeyId: id,
        apiKeyName: `name-${id}`,
        userId: "u1",
        isActive: "true",
    });

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        cacheRoutes("/auth/api-keys");
        mockFetchUserApiKeys.mockResolvedValue({ Items: [] });
    });

    it("requests the backend's page size and no token on the first page", async () => {
        mockFetchApiKeys.mockResolvedValue({ Items: [key("k1")] });

        render(<ApiKeys />);

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(mockFetchApiKeys).toHaveBeenCalledWith({
            pageSize: API_KEY_LISTING_PAGE_SIZE,
            startingToken: undefined,
        });
    });

    it("moving to page two re-requests with the NextToken from page one", async () => {
        mockFetchApiKeys
            .mockResolvedValueOnce({ Items: [key("k1")], NextToken: "tok-2", truncated: true })
            .mockResolvedValueOnce({ Items: [key("k2")] });

        render(<ApiKeys />);

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());

        const nextPage = await screen.findByLabelText("Next page of API keys");
        await userEvent.click(nextPage);

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalledTimes(2));
        expect(mockFetchApiKeys).toHaveBeenLastCalledWith({
            pageSize: API_KEY_LISTING_PAGE_SIZE,
            startingToken: "tok-2",
        });
        await waitFor(() => expect(screen.getByText("name-k2")).toBeInTheDocument());
        expect(screen.queryByText("name-k1")).not.toBeInTheDocument();
    });

    it("says the filter and sort are page-scoped only while more keys remain", async () => {
        mockFetchApiKeys.mockResolvedValue({
            Items: [key("k1")],
            NextToken: "tok-2",
            truncated: true,
        });

        render(<ApiKeys />);

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());
        expect(
            screen.getByText(/the filter and column sorting apply to the page shown/)
        ).toBeInTheDocument();
    });

    it("negative control: a complete listing offers no next page and issues one request", async () => {
        mockFetchApiKeys.mockResolvedValue({ Items: [key("k1"), key("k2")] });

        render(<ApiKeys />);

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());
        expect(screen.getByText("name-k2")).toBeInTheDocument();
        expect(mockFetchApiKeys).toHaveBeenCalledTimes(1);
        expect(
            screen.queryByText(/the filter and column sorting apply to the page shown/)
        ).not.toBeInTheDocument();
        // pagesCount stays at 1, so no page-two control is rendered at all.
        expect(screen.queryByLabelText("Page 2 of API keys")).not.toBeInTheDocument();
    });

    it("switching to My Keys restarts the walk from page one", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");
        mockFetchApiKeys.mockResolvedValue({
            Items: [key("k1")],
            NextToken: "tok-2",
            truncated: true,
        });
        mockFetchUserApiKeys.mockResolvedValue({ Items: [key("mine")] });

        render(<ApiKeys />);

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());

        await userEvent.click(screen.getByText("My Keys"));

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchUserApiKeys).toHaveBeenLastCalledWith({
            pageSize: API_KEY_LISTING_PAGE_SIZE,
            startingToken: undefined,
        });
    });
});
