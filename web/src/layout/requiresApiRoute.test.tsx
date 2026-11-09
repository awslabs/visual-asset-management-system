/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Admin - Auth's "API Keys (all users)" link opens the tenant-wide API keys view. Its `web` route is
 * the same as the profile entry's, so the side navigation also asks the API's allowed-routes listing
 * (`requiresApiRoute: GET /auth/api-keys`). The link is hidden while that listing is unknown, failed
 * or does not allow the route; it is advisory only, and the API still enforces every call.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { Navigation } from "./Navigation";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";
import { appCache } from "../services/appCache";
import {
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../common/constants/authRoutes";

jest.mock("../services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

const mockCheck = checkWebRoutesAllowed as jest.MockedFunction<typeof checkWebRoutesAllowed>;
const ADMIN_LINK = "#/auth/api-keys/?mode=admin";

function allowWebRoutes(...allowedPaths: string[]) {
    mockCheck.mockImplementation(async (routes: WebRouteCheck[]) =>
        routes.filter((route) => allowedPaths.includes(route.route__path))
    );
}

function cacheApiRoutes(...routes: { path: string; methods: string[] }[]) {
    appCache.setItemWithExpiry(
        ALLOWED_API_ROUTES_CACHE_KEY,
        { userId: "u1", routes },
        ALLOWED_API_ROUTES_CACHE_TTL_MILLIS
    );
}

async function renderSideNavigation() {
    const view = render(<Navigation activeHref="#/auth/roles/" user={undefined} />);
    const nav = () => createWrapper(view.container).findSideNavigation();
    await waitFor(() => expect(nav()?.findLinkByHref("#/auth/roles/")).toBeTruthy());
    return { ...view, nav };
}

beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    allowWebRoutes("/auth/roles", "/auth/api-keys");
});

describe("requiresApiRoute in the side navigation", () => {
    it("shows the admin API keys link when the API listing allows GET /auth/api-keys", async () => {
        cacheApiRoutes(
            { path: "/auth/api-keys", methods: ["GET", "POST"] },
            { path: "/auth/user/api-keys", methods: ["GET"] }
        );
        const { nav } = await renderSideNavigation();

        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeTruthy();
        expect(nav()?.findLinkByHref(ADMIN_LINK)?.getElement()).toHaveTextContent(
            "API Keys (all users)"
        );
    });

    it("hides it for a self-service role the listing does not allow", async () => {
        cacheApiRoutes({ path: "/auth/user/api-keys", methods: ["GET"] });
        const { nav } = await renderSideNavigation();

        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeFalsy();
    });

    it("hides it while the listing is unknown (not cached, or the fetch failed)", async () => {
        const { nav } = await renderSideNavigation();

        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeFalsy();
    });

    it("hides it when the web route is not allowed, whatever the listing says", async () => {
        cacheApiRoutes({ path: "/auth/api-keys", methods: ["GET"] });
        allowWebRoutes("/auth/roles");
        const { nav } = await renderSideNavigation();

        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeFalsy();
    });

    it("shows it on the next render once the auth flow has cached the listing", async () => {
        const { nav, rerender } = await renderSideNavigation();
        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeFalsy();

        cacheApiRoutes({ path: "/auth/api-keys", methods: ["GET"] });
        rerender(<Navigation activeHref="#/auth/constraints/" user={undefined} />);

        expect(nav()?.findLinkByHref(ADMIN_LINK)).toBeTruthy();
    });
});
