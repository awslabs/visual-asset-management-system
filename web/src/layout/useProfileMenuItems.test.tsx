/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The top-bar profile menu lists the registry's profile pages the API allowed, then Sign out. It
 * uses the side navigation's `web`-constraint check, shares its batched `auth/routes` call, and
 * fails closed: no page links while the check is loading or after it failed, and Sign out always.
 */

import React from "react";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import { useProfileMenuItems, SIGN_OUT_ITEM_ID, toMenuItems } from "./useProfileMenuItems";
import { entriesFor } from "./navigationRegistry";
import { Navigation } from "./Navigation";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";
import { webRoutes } from "../services/APIService";

// The real batched helper stays available (the shared-call test uses it); the other tests replace
// its implementation with a fixed decision.
jest.mock("../services/webRoutesCheck", () => {
    const actual = jest.requireActual("../services/webRoutesCheck");
    return { ...actual, checkWebRoutesAllowed: jest.fn(actual.checkWebRoutesAllowed) };
});
jest.mock("../services/APIService", () => ({ webRoutes: jest.fn() }));

const actualCheck = jest.requireActual("../services/webRoutesCheck").checkWebRoutesAllowed;
const mockCheck = checkWebRoutesAllowed as jest.MockedFunction<typeof checkWebRoutesAllowed>;
const mockWebRoutes = webRoutes as jest.MockedFunction<any>;

function allowRoutes(...allowedPaths: string[]) {
    mockCheck.mockImplementation(async (routes: WebRouteCheck[]) =>
        routes.filter((route) => allowedPaths.includes(route.route__path))
    );
}

/** Every link href in the menu items, groups included. */
function hrefs(items: any[]): string[] {
    return items.flatMap((item) => [...(item.href ? [item.href] : []), ...hrefs(item.items ?? [])]);
}

beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
});

describe("useProfileMenuItems", () => {
    it("lists only the page links the API allowed, then Sign out", async () => {
        allowRoutes("/auth/api-keys");
        const { result } = renderHook(() => useProfileMenuItems());

        await waitFor(() => expect(hrefs(result.current)).toContain("#/auth/api-keys/?mode=user"));
        const items = result.current as any[];
        expect(items[0]).toEqual({
            id: "group:Your pages",
            text: "Your pages",
            items: [
                {
                    id: "page:#/auth/api-keys/?mode=user",
                    text: "API Key Management",
                    href: "#/auth/api-keys/?mode=user",
                },
            ],
        });
        expect(items[items.length - 1]).toEqual({ id: SIGN_OUT_ITEM_ID, text: "Sign out" });
        // The admin view is an Admin - Auth (side) entry, never a profile one.
        expect(hrefs(items)).not.toContain("#/auth/api-keys/?mode=admin");
    });

    it("lists Sign out only when the role has no profile page", async () => {
        allowRoutes("/databases");
        const { result } = renderHook(() => useProfileMenuItems());

        await waitFor(() => expect(mockCheck).toHaveBeenCalled());
        await act(async () => undefined);
        expect(result.current).toEqual([{ id: SIGN_OUT_ITEM_ID, text: "Sign out" }]);
    });

    it("lists no page links while the check is loading", () => {
        mockCheck.mockImplementation(() => new Promise(() => undefined));
        const { result } = renderHook(() => useProfileMenuItems());

        expect(result.current).toEqual([{ id: SIGN_OUT_ITEM_ID, text: "Sign out" }]);
    });

    it("fails closed after a failed check and retries on the next hashchange", async () => {
        const error = jest.spyOn(console, "error").mockImplementation(() => undefined);
        try {
            mockCheck.mockRejectedValueOnce(new Error("auth/routes failed"));
            const { result } = renderHook(() => useProfileMenuItems());

            await waitFor(() => expect(error).toHaveBeenCalled());
            expect(result.current).toEqual([{ id: SIGN_OUT_ITEM_ID, text: "Sign out" }]);

            allowRoutes("/auth/api-keys");
            act(() => {
                window.dispatchEvent(new HashChangeEvent("hashchange"));
            });

            await waitFor(() =>
                expect(hrefs(result.current)).toContain("#/auth/api-keys/?mode=user")
            );
            expect(mockCheck).toHaveBeenCalledTimes(2);
            const items = result.current as any[];
            expect(items[items.length - 1]).toEqual({
                id: SIGN_OUT_ITEM_ID,
                text: "Sign out",
            });
        } finally {
            error.mockRestore();
        }
    });

    describe("the current page", () => {
        const pageItems = (items: any[]) =>
            (items.find((item) => item.id === "group:Your pages") as any).items as any[];

        afterEach(() => {
            window.location.hash = "";
        });

        it("shows the page the user is on as checked, matching on the path and ignoring the query", async () => {
            allowRoutes("/auth/api-keys");
            window.location.hash = "#/auth/api-keys/?mode=user";
            const { result } = renderHook(() => useProfileMenuItems());
            await waitFor(() => expect(pageItems(result.current as any[])).toHaveLength(1));

            const [item] = pageItems(result.current as any[]);
            expect(item).toMatchObject({
                id: "page:#/auth/api-keys/?mode=user",
                itemType: "checkbox",
                checked: true,
            });
            // A checkbox item takes no href; App.tsx follows it through onItemClick.
            expect(item.href).toBeUndefined();
        });

        it("matches a path without the trailing slash or query", async () => {
            allowRoutes("/auth/api-keys");
            window.location.hash = "#/auth/api-keys";
            const { result } = renderHook(() => useProfileMenuItems());
            await waitFor(() => expect(pageItems(result.current as any[])).toHaveLength(1));
            expect(pageItems(result.current as any[])[0].checked).toBe(true);
        });

        it("leaves the page a link while the user is elsewhere, and updates on hashchange", async () => {
            allowRoutes("/auth/api-keys");
            window.location.hash = "#/databases/";
            const { result } = renderHook(() => useProfileMenuItems());
            await waitFor(() => expect(pageItems(result.current as any[])).toHaveLength(1));
            expect(pageItems(result.current as any[])[0]).toEqual({
                id: "page:#/auth/api-keys/?mode=user",
                text: "API Key Management",
                href: "#/auth/api-keys/?mode=user",
            });

            act(() => {
                window.location.hash = "#/auth/api-keys/?mode=user";
                window.dispatchEvent(new HashChangeEvent("hashchange"));
            });
            await waitFor(() => expect(pageItems(result.current as any[])[0].checked).toBe(true));

            act(() => {
                window.location.hash = "#/pipelines/";
                window.dispatchEvent(new HashChangeEvent("hashchange"));
            });
            await waitFor(() =>
                expect(pageItems(result.current as any[])[0].checked).toBeUndefined()
            );
        });

        it("marks My Download Queue on its page and leaves API Key Management a link (combined: both profile entries)", () => {
            const entries = entriesFor("profile", ["ZIPDOWNLOADS"]);
            const section = (path: string) => (toMenuItems(entries, path)[0] as any).items as any[];
            const onQueue = section("/zipdownloads");
            expect(onQueue.find((i) => i.text === "My Download Queue")).toMatchObject({
                itemType: "checkbox",
                checked: true,
            });
            expect(onQueue.find((i) => i.text === "API Key Management").href).toBe(
                "#/auth/api-keys/?mode=user"
            );
            const elsewhere = section("/databases");
            expect(elsewhere.every((i) => !i.checked && i.href)).toBe(true);
        });

        it("never marks Sign out", async () => {
            allowRoutes("/auth/api-keys");
            window.location.hash = "#/";
            const { result } = renderHook(() => useProfileMenuItems());
            await waitFor(() => expect(pageItems(result.current as any[])).toHaveLength(1));
            const items = result.current as any[];
            expect(items[items.length - 1]).toEqual({ id: SIGN_OUT_ITEM_ID, text: "Sign out" });
        });
    });

    it("does not re-check after a successful check when the hash changes", async () => {
        allowRoutes("/auth/api-keys");
        const { result } = renderHook(() => useProfileMenuItems());
        await waitFor(() => expect(hrefs(result.current)).toContain("#/auth/api-keys/?mode=user"));

        act(() => {
            window.dispatchEvent(new HashChangeEvent("hashchange"));
        });

        expect(mockCheck).toHaveBeenCalledTimes(1);
    });

    it("shares one batched auth/routes call with the side navigation", async () => {
        mockCheck.mockImplementation(actualCheck);
        mockWebRoutes.mockImplementation(async ({ routes }: { routes: WebRouteCheck[] }) => ({
            allowedRoutes: routes.filter((route) =>
                ["/databases", "/auth/api-keys"].includes(route.route__path)
            ),
        }));

        let menuItems: any[] = [];
        function Shell() {
            menuItems = useProfileMenuItems() as any[];
            return <Navigation activeHref="#/databases/" user={undefined} />;
        }
        const { container } = render(<Shell />);

        await waitFor(() => expect(hrefs(menuItems)).toContain("#/auth/api-keys/?mode=user"));
        await waitFor(() => expect(container.querySelector('a[href="#/databases/"]')).toBeTruthy());
        expect(mockWebRoutes).toHaveBeenCalledTimes(1);
    });
});
