/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on web lane (addons/addonRoutes.tsx, addons/addonNavigation.ts) sits between core and
 * custom, and its entries are handled exactly like core ones.
 *
 * Both add-on files ship empty, so each is mocked here with a probe entry, and so is the fork's
 * custom lane: the route table is core, then add-on, then custom; an add-on page is lazy-loaded and
 * rendered only when the `web` constraint allows its path; an add-on path that repeats a core path
 * never replaces the core page; and the add-on navigation section is filtered by the same check and
 * placed after the core sections and before the fork's.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { routeTable, AppRoutes } from "../routes";
import { Navigation } from "../layout/Navigation";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";

jest.mock("../services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

jest.mock("./addonRoutes", () => {
    const mockReact = jest.requireActual("react");
    const page = (testId: string) => () =>
        mockReact.createElement("div", { "data-testid": testId });
    return {
        addonRouteTable: [
            {
                path: "/addon-probe",
                Page: mockReact.lazy(async () => ({ default: page("addon-probe-page") })),
                active: "#/addon-probe/",
            },
            // Repeats a core path: the core page must keep matching.
            { path: "/pipelines", Page: page("addon-shadow-page"), active: "#/pipelines/" },
        ],
    };
});

jest.mock("../custom/customRoutes", () => {
    const mockReact = jest.requireActual("react");
    return {
        customRouteTable: [
            {
                path: "/custom/probe",
                Page: () => mockReact.createElement("div", { "data-testid": "custom-probe-page" }),
                active: "#/custom/probe/",
            },
        ],
    };
});

jest.mock("./addonNavigation", () => ({
    addonNavSections: [
        {
            type: "section",
            text: "Add-on probe",
            items: [{ type: "link", text: "Add-on probe page", href: "#/addon-probe/" }],
        },
    ],
}));

jest.mock("../custom/customNavigation", () => ({
    customNavSections: [
        {
            type: "section",
            text: "Custom probe",
            items: [{ type: "link", text: "Custom probe page", href: "#/custom/probe/" }],
        },
    ],
}));

// The layout is Cloudscape's; only the content slot matters for the route assertions.
jest.mock("@cloudscape-design/components/app-layout", () => ({
    __esModule: true,
    default: ({ content }: any) => <div data-testid="layout-content">{content}</div>,
}));

jest.mock("../pages/LandingPage", () => ({
    __esModule: true,
    default: () => <div data-testid="landing-page" />,
}));

jest.mock("../pages/PipelinesPage2", () => ({
    __esModule: true,
    default: () => <div data-testid="pipelines-page" />,
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

function renderAt(pathname: string) {
    return render(
        <MemoryRouter initialEntries={[pathname]}>
            <AppRoutes navigationOpen={true} setNavigationOpen={jest.fn()} user={undefined} />
        </MemoryRouter>
    );
}

beforeEach(() => {
    jest.clearAllMocks();
});

describe("route table lanes", () => {
    it("composes core, then add-on, then custom routes", () => {
        const paths = routeTable.map((route) => route.path);
        const lastCore = paths.indexOf("*");
        const addon = paths.indexOf("/addon-probe");
        const custom = paths.indexOf("/custom/probe");
        expect(lastCore).toBeGreaterThan(0);
        expect(addon).toBeGreaterThan(lastCore);
        expect(custom).toBeGreaterThan(addon);
    });

    it("renders a lazy add-on page when the web constraint allows its path", async () => {
        allowRoutes("/addon-probe");

        renderAt("/addon-probe");

        await waitFor(() => expect(screen.getByTestId("addon-probe-page")).toBeInTheDocument());
    });

    it("does not render the add-on page when the web constraint does not allow its path", async () => {
        allowRoutes("/pipelines");

        renderAt("/addon-probe");

        // "*" is always added back, so the unmatched path lands on the landing page.
        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeInTheDocument());
        expect(screen.queryByTestId("addon-probe-page")).not.toBeInTheDocument();
    });

    it("keeps the core page on a path an add-on route repeats", async () => {
        allowRoutes("/pipelines");

        renderAt("/pipelines");

        await waitFor(() => expect(screen.getByTestId("pipelines-page")).toBeInTheDocument());
        expect(screen.queryByTestId("addon-shadow-page")).not.toBeInTheDocument();
    });
});

describe("navigation lanes", () => {
    async function renderNavigation() {
        const { container } = render(<Navigation activeHref={"#/databases"} user={undefined} />);
        await waitFor(() => expect(mockCheckWebRoutesAllowed).toHaveBeenCalled());
        return container;
    }

    it("places the add-on section after the core sections and before the fork's", async () => {
        allowRoutes("/databases", "/addon-probe", "/custom/probe");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/addon-probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        expect(text.indexOf("Add-on probe")).toBeGreaterThan(text.indexOf("Databases"));
        expect(text.indexOf("Custom probe")).toBeGreaterThan(text.indexOf("Add-on probe"));
    });

    it("filters the add-on section by the same web-route check", async () => {
        allowRoutes("/databases");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/databases/")).toBeTruthy());
        expect(nav?.findLinkByHref("#/addon-probe/")).toBeFalsy();
        expect(screen.queryByText("Add-on probe")).not.toBeInTheDocument();
    });
});
