/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render, screen, waitFor } from "@testing-library/react";

import createWrapper from "@cloudscape-design/components/test-utils/dom";

import { Navigation } from "./Navigation";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";

// Navigation filters its items through the server-side web route permission
// check (auth/routes via the batched webRoutesCheck helper).
jest.mock("../services/webRoutesCheck", () => ({
    checkWebRoutesAllowed: jest.fn(),
}));

const mockCheckWebRoutesAllowed = checkWebRoutesAllowed as jest.MockedFunction<
    typeof checkWebRoutesAllowed
>;

/** Resolve the permission check by allowing only the given route paths. */
function allowRoutes(...allowedPaths: string[]) {
    mockCheckWebRoutesAllowed.mockImplementation(async (routes: WebRouteCheck[]) =>
        routes.filter((route) => allowedPaths.includes(route.route__path))
    );
}

async function renderNavigation() {
    const { container } = render(<Navigation activeHref={"#/databases"} user={undefined} />);
    // Wait for the async permission check to resolve and the nav to render
    await waitFor(() => {
        expect(mockCheckWebRoutesAllowed).toHaveBeenCalled();
    });
    return container;
}

function findNavLink(container: HTMLElement, href: string) {
    return createWrapper(container).findSideNavigation()?.findLinkByHref(href);
}

describe("Navigation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("renders only the links the user has access to", async () => {
        allowRoutes("/databases/", "/assets/");
        const container = await renderNavigation();

        await waitFor(() => {
            expect(findNavLink(container, "#/databases/")).toBeTruthy();
        });
        expect(findNavLink(container, "#/assets/")).toBeTruthy();
        expect(findNavLink(container, "#/pipelines/")).toBeFalsy();
        expect(findNavLink(container, "#/workflows/")).toBeFalsy();
        expect(findNavLink(container, "#/auth/constraints/")).toBeFalsy();
    });

    it("renders admin auth links when allowed", async () => {
        allowRoutes("/auth/constraints/", "/auth/roles/", "/auth/userroles/");
        const container = await renderNavigation();

        await waitFor(() => {
            expect(findNavLink(container, "#/auth/constraints/")).toBeTruthy();
        });
        expect(findNavLink(container, "#/auth/roles/")).toBeTruthy();
        expect(findNavLink(container, "#/auth/userroles/")).toBeTruthy();
        expect(findNavLink(container, "#/databases/")).toBeFalsy();
    });

    it("renders the User section with API Key Management when allowed", async () => {
        allowRoutes("/auth/api-keys/");
        const container = await renderNavigation();

        await waitFor(() => {
            expect(findNavLink(container, "#/auth/api-keys/")).toBeTruthy();
        });
    });

    it("hides the User section when API Key Management is not allowed", async () => {
        allowRoutes("/databases/");
        const container = await renderNavigation();

        await waitFor(() => {
            expect(findNavLink(container, "#/databases/")).toBeTruthy();
        });
        expect(findNavLink(container, "#/auth/api-keys/")).toBeFalsy();
        expect(screen.queryByText("User")).not.toBeInTheDocument();
    });

    it("shows the no-access message when no routes are allowed", async () => {
        allowRoutes(/* none */);
        await renderNavigation();

        await waitFor(() => {
            expect(screen.getByText(/don't have access/i)).toBeInTheDocument();
        });
    });

    it("shows the no-access message when the permission check fails", async () => {
        mockCheckWebRoutesAllowed.mockRejectedValue(new Error("auth/routes failed"));
        await renderNavigation();

        await waitFor(() => {
            expect(screen.getByText(/don't have access/i)).toBeInTheDocument();
        });
    });
});
