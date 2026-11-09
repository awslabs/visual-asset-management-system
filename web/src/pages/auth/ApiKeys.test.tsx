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
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { appCache } from "../../services/appCache";
import {
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../../common/constants/authRoutes";
import { API_KEY_LISTING_PAGE_SIZE } from "../../common/constants/apiKeys";
import ApiKeys, { apiKeyStatus, requestedApiKeyMode, resolveApiKeyMode } from "./ApiKeys";
import { ActiveNavHrefProvider } from "../../layout/activeNavHref";

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

/** The page sits under the HashRouter; `entry` is the route plus any `?mode=` query. */
const renderApiKeys = (entry = "/auth/api-keys/") =>
    render(
        <MemoryRouter initialEntries={[entry]}>
            <ApiKeys />
        </MemoryRouter>
    );

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

        renderApiKeys();

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

        renderApiKeys();

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

        renderApiKeys();

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(screen.getByText("API Keys")).toBeInTheDocument();
        expect(screen.queryByText("Could not determine your permissions")).not.toBeInTheDocument();
    });

    it("positive control: a self-service-only user gets user mode, not admin", async () => {
        cacheRoutes("/auth/user/api-keys");

        renderApiKeys();

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
        apiKeyName: `name-${id}`, // pragma: allowlist secret
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

        renderApiKeys();

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

        renderApiKeys();

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

        renderApiKeys();

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());
        expect(
            screen.getByText(/the filter and column sorting apply to the page shown/)
        ).toBeInTheDocument();
    });

    it("negative control: a complete listing offers no next page and issues one request", async () => {
        mockFetchApiKeys.mockResolvedValue({ Items: [key("k1"), key("k2")] });

        renderApiKeys();

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

        renderApiKeys();

        await waitFor(() => expect(screen.getByText("name-k1")).toBeInTheDocument());

        await userEvent.click(screen.getByText("My Keys"));

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchUserApiKeys).toHaveBeenLastCalledWith({
            pageSize: API_KEY_LISTING_PAGE_SIZE,
            startingToken: undefined,
        });
    });
});

/**
 * The Active column follows the authorizer: an active key whose expiration has passed is refused,
 * so it reads Expired rather than Active.
 */
describe("ApiKeys status column", () => {
    const NOW = Date.parse("2026-06-15T12:00:00Z");

    it.each([
        [{ isActive: "false", expiresAt: "2020-01-01" }, "Inactive"],
        [{ isActive: "true" }, "Active"],
        [{ isActive: "true", expiresAt: "2026-06-15T11:59:59Z" }, "Expired"],
        [{ isActive: "true", expiresAt: "2026-06-15T12:00:01Z" }, "Active"],
        // Date-only and offset-less values are read as UTC, as the authorizer reads them.
        [{ isActive: "true", expiresAt: "2026-06-15" }, "Expired"],
        [{ isActive: "true", expiresAt: "2026-06-16" }, "Active"],
        [{ isActive: "true", expiresAt: "2026-06-15T11:00:00" }, "Expired"],
        [{ isActive: "true", expiresAt: "2026-06-15T13:00:00" }, "Active"],
        [{ isActive: "true", expiresAt: "2026-06-15T14:00:00+01:00" }, "Active"],
        [{ isActive: "true", expiresAt: "not-a-date" }, "Expired"],
    ])("reads %j as %s", (item, expected) => {
        expect(apiKeyStatus(item as any, NOW)).toBe(expected);
    });

    it("shows an active key past its expiry as Expired in the table", async () => {
        localStorage.clear();
        cacheRoutes("/auth/api-keys");
        mockFetchUserApiKeys.mockResolvedValue({ Items: [] });
        mockFetchApiKeys.mockResolvedValue({
            Items: [
                {
                    apiKeyId: "k1", // pragma: allowlist secret
                    apiKeyName: "old-key", // pragma: allowlist secret
                    userId: "u1",
                    isActive: "true",
                    expiresAt: "2020-01-01T00:00:00Z",
                },
            ],
        });

        renderApiKeys();

        expect(await screen.findByText("old-key")).toBeInTheDocument();
        expect(screen.getByText("Expired")).toBeInTheDocument();
    });
});

/**
 * The profile menu opens `?mode=user` and Admin - Auth opens `?mode=admin`. The query is a
 * preference validated against the modes the API's allowed-routes listing allowed: a URL never opens
 * the tenant-wide admin mode for a role the listing does not allow.
 */
describe("ApiKeys ?mode= preference", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        mockFetchApiKeys.mockResolvedValue({ Items: [] });
        mockFetchUserApiKeys.mockResolvedValue({ Items: [] });
    });

    it("ignores ?mode=admin when the listing does not allow admin mode", async () => {
        cacheRoutes("/auth/user/api-keys");

        renderApiKeys("/auth/api-keys/?mode=admin");

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchApiKeys).not.toHaveBeenCalled();
        expect(screen.getByText("My API Keys")).toBeInTheDocument();
        expect(screen.queryByText("All Keys (Admin)")).not.toBeInTheDocument();
    });

    it("ignores ?mode=admin when the listing is unavailable, and fails closed", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error", 500]);

        renderApiKeys("/auth/api-keys/?mode=admin");

        await waitFor(() =>
            expect(screen.getByText("Could not determine your permissions")).toBeInTheDocument()
        );
        expect(mockFetchApiKeys).not.toHaveBeenCalled();
    });

    it("opens the user's own keys for ?mode=user when both modes are allowed", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        renderApiKeys("/auth/api-keys/?mode=user");

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchApiKeys).not.toHaveBeenCalled();
        expect(screen.getByText("My API Keys")).toBeInTheDocument();
    });

    it("opens the admin view for ?mode=admin when the listing allows it", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        renderApiKeys("/auth/api-keys/?mode=admin");

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(mockFetchUserApiKeys).not.toHaveBeenCalled();
    });

    it("positive control: with no ?mode= and both modes allowed, admin stays the default", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        renderApiKeys();

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        expect(mockFetchUserApiKeys).not.toHaveBeenCalled();
    });

    it("follows a later ?mode= change while the page stays open, within the allowed modes", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");
        let go: (to: string) => void = () => undefined;
        function Navigator() {
            const navigate = useNavigate();
            go = (to) => navigate(to);
            return null;
        }
        render(
            <MemoryRouter initialEntries={["/auth/api-keys/?mode=user"]}>
                <Navigator />
                <ApiKeys />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(mockFetchApiKeys).not.toHaveBeenCalled();

        // The Admin - Auth link, followed from the user view.
        act(() => go("/auth/api-keys/?mode=admin"));

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
    });

    it("titles the document like its h1", async () => {
        cacheRoutes("/auth/user/api-keys");

        renderApiKeys("/auth/api-keys/?mode=user");

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("API Key Management");
        expect(document.title).toMatch(/ - API Key Management$/);
    });

    it("resolves the mode from the request and the allowed modes", () => {
        expect(requestedApiKeyMode("?mode=admin")).toBe("admin");
        expect(requestedApiKeyMode("?mode=user")).toBe("user");
        expect(requestedApiKeyMode("?mode=root")).toBeNull();
        expect(requestedApiKeyMode("")).toBeNull();

        expect(resolveApiKeyMode("admin", false, true)).toBe("user");
        expect(resolveApiKeyMode("admin", true, true)).toBe("admin");
        expect(resolveApiKeyMode("user", true, false)).toBe("admin");
        expect(resolveApiKeyMode("user", true, true)).toBe("user");
        expect(resolveApiKeyMode(null, true, true)).toBe("admin");
        expect(resolveApiKeyMode(null, false, true)).toBe("user");
    });
});

/**
 * The page is one route with two views, so it tells the side navigation which link to highlight
 * from its resolved mode (checked against the API listing), not from the raw ?mode= value:
 * the admin view highlights Admin - Auth > "API Keys (all users)", the user view (opened from
 * the profile menu) highlights nothing.
 */
describe("ApiKeys side-navigation highlight", () => {
    const ADMIN_HREF = "#/auth/api-keys/?mode=admin";

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        mockFetchApiKeys.mockResolvedValue({ Items: [] });
        mockFetchUserApiKeys.mockResolvedValue({ Items: [] });
    });

    function renderWithHighlight(entry: string) {
        const setActiveNavHref = jest.fn();
        const view = render(
            <MemoryRouter initialEntries={[entry]}>
                <ActiveNavHrefProvider setActiveNavHref={setActiveNavHref}>
                    <ApiKeys />
                </ActiveNavHrefProvider>
            </MemoryRouter>
        );
        const last = () => setActiveNavHref.mock.calls[setActiveNavHref.mock.calls.length - 1]?.[0];
        return { ...view, setActiveNavHref, last };
    }

    it("highlights API Keys (all users) on the admin view", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        const { last } = renderWithHighlight("/auth/api-keys/?mode=admin");

        await waitFor(() => expect(mockFetchApiKeys).toHaveBeenCalled());
        await waitFor(() => expect(last()).toBe(ADMIN_HREF));
    });

    it("highlights no side link on the user view", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        const { last, setActiveNavHref } = renderWithHighlight("/auth/api-keys/?mode=user");

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(last()).toBe("");
        expect(setActiveNavHref).not.toHaveBeenCalledWith(ADMIN_HREF);
    });

    it("does not highlight the admin link for ?mode=admin when the API allows only own keys", async () => {
        cacheRoutes("/auth/user/api-keys");

        const { last, setActiveNavHref } = renderWithHighlight("/auth/api-keys/?mode=admin");

        await waitFor(() => expect(mockFetchUserApiKeys).toHaveBeenCalled());
        expect(last()).toBe("");
        expect(setActiveNavHref).not.toHaveBeenCalledWith(ADMIN_HREF);
    });

    it("does not highlight the admin link while the permission check failed", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error", 500]);

        const { setActiveNavHref } = renderWithHighlight("/auth/api-keys/?mode=admin");

        await waitFor(() =>
            expect(screen.getByText("Could not determine your permissions")).toBeInTheDocument()
        );
        expect(setActiveNavHref).not.toHaveBeenCalledWith(ADMIN_HREF);
    });

    it("hands the highlight back to the route when the page closes", async () => {
        cacheRoutes("/auth/api-keys", "/auth/user/api-keys");

        const { last, unmount } = renderWithHighlight("/auth/api-keys/?mode=admin");
        await waitFor(() => expect(last()).toBe(ADMIN_HREF));

        unmount();
        expect(last()).toBeNull();
    });
});
