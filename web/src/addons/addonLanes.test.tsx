/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on web lane (addons/addonRoutes.tsx, addons/addonNavigation.ts) sits between core and
 * custom, and its entries are handled exactly like core ones.
 *
 * Each add-on file is mocked here with probe entries, independent of the add-ons it carries, and so
 * is the fork's custom lane: the route table is core, then add-on, then custom; an add-on page is lazy-loaded and
 * rendered only when the `web` constraint allows its path; an add-on path that repeats a core path
 * never replaces the core page; and the add-on navigation sections are filtered by the same check,
 * the main-menu ones placed after "Orchestrate & Automate" and the admin ones after the core admin
 * sections and before "User", with the fork's after everything. A navigation entry restricted to feature
 * switches shows only while all of them are enabled, and appears when they arrive after mount.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { routeTable, AppRoutes } from "../routes";
import { Navigation } from "../layout/Navigation";
import { RuntimeConfigContext } from "../context/RuntimeConfigContext";
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
    addonNavAdminSections: [
        {
            type: "section",
            text: "Add-on admin probe",
            items: [{ type: "link", text: "Add-on admin page", href: "#/addon-admin-probe/" }],
        },
    ],
    addonNavMainSections: [
        {
            type: "section",
            text: "Add-on probe",
            items: [{ type: "link", text: "Add-on probe page", href: "#/addon-probe/" }],
        },
        {
            type: "section",
            text: "Add-on restricted probe",
            featuresEnabledRestriction: ["ADDON_PROBE_FEATURE"],
            items: [
                { type: "link", text: "Add-on restricted page", href: "#/addon-restricted/" },
                {
                    type: "link",
                    text: "Add-on two-switch page",
                    href: "#/addon-restricted/both/",
                    featuresEnabledRestriction: ["ADDON_PROBE_FEATURE", "ADDON_PROBE_OTHER"],
                },
            ],
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

        // A route the role does not allow shows the no-access panel, not the add-on page.
        await waitFor(() =>
            expect(screen.getByText("You do not have access to this page")).toBeInTheDocument()
        );
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

    it("places the add-on main-menu section after Orchestrate & Automate, ahead of the admin area", async () => {
        allowRoutes("/executions", "/addon-probe", "/metadataschema", "/auth/api-keys");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/addon-probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const executions = text.indexOf("Executions");
        expect(executions).toBeGreaterThan(-1);
        expect(text.indexOf("Add-on probe")).toBeGreaterThan(executions);
        expect(text.indexOf("Metadata Schema")).toBeGreaterThan(text.indexOf("Add-on probe"));
        expect(text.indexOf("API Key Management")).toBeGreaterThan(text.indexOf("Add-on probe"));
    });

    it("places the add-on admin section after the core admin sections and before User", async () => {
        allowRoutes("/auth/userroles", "/addon-admin-probe", "/auth/api-keys");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/addon-admin-probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const usersInRoles = text.indexOf("Users in Roles");
        expect(usersInRoles).toBeGreaterThan(-1);
        expect(text.indexOf("Add-on admin probe")).toBeGreaterThan(usersInRoles);
        expect(text.indexOf("API Key Management")).toBeGreaterThan(
            text.indexOf("Add-on admin probe")
        );
    });

    it("keeps the fork's section after every core and add-on section", async () => {
        allowRoutes("/addon-probe", "/addon-admin-probe", "/auth/api-keys", "/custom/probe");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/custom/probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const custom = text.indexOf("Custom probe");
        expect(custom).toBeGreaterThan(text.indexOf("Add-on probe"));
        expect(custom).toBeGreaterThan(text.indexOf("Add-on admin probe"));
        expect(custom).toBeGreaterThan(text.indexOf("API Key Management"));
    });

    it("filters the add-on admin section by the same web-route check", async () => {
        allowRoutes("/auth/api-keys");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/auth/api-keys/")).toBeTruthy());
        expect(nav?.findLinkByHref("#/addon-admin-probe/")).toBeFalsy();
        expect(screen.queryByText("Add-on admin probe")).not.toBeInTheDocument();
    });

    it("filters the add-on section by the same web-route check", async () => {
        allowRoutes("/databases");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/databases/")).toBeTruthy());
        expect(nav?.findLinkByHref("#/addon-probe/")).toBeFalsy();
        expect(screen.queryByText("Add-on probe")).not.toBeInTheDocument();
    });

    it("omits an entry restricted to a feature switch that is not enabled, without checking its route", async () => {
        allowRoutes("/databases", "/addon-probe", "/addon-restricted");
        const { container } = render(
            <RuntimeConfigContext.Provider value={{ featuresEnabled: ["ADDON_PROBE_OTHER"] }}>
                <Navigation activeHref={"#/databases"} user={undefined} />
            </RuntimeConfigContext.Provider>
        );
        const nav = () => createWrapper(container).findSideNavigation();

        // Control: the unrestricted add-on section is offered.
        await waitFor(() => expect(nav()?.findLinkByHref("#/addon-probe/")).toBeTruthy());
        expect(nav()?.findLinkByHref("#/addon-restricted/")).toBeFalsy();
        expect(screen.queryByText("Add-on restricted probe")).not.toBeInTheDocument();
        const checked = mockCheckWebRoutesAllowed.mock.calls.flatMap(([routes]) =>
            routes.map((route) => route.route__path)
        );
        expect(checked).toContain("/addon-probe");
        expect(checked).not.toContain("/addon-restricted");
    });

    it("adds a restricted entry, its route checked, when its feature switch arrives after mount", async () => {
        allowRoutes("/databases", "/addon-restricted", "/addon-restricted/both");
        // The config the shell holds before the secure config lands carries no feature switches.
        const { container, rerender } = render(
            <RuntimeConfigContext.Provider value={{}}>
                <Navigation activeHref={"#/databases"} user={undefined} />
            </RuntimeConfigContext.Provider>
        );
        const nav = () => createWrapper(container).findSideNavigation();
        await waitFor(() => expect(nav()?.findLinkByHref("#/databases/")).toBeTruthy());
        expect(nav()?.findLinkByHref("#/addon-restricted/")).toBeFalsy();

        rerender(
            <RuntimeConfigContext.Provider value={{ featuresEnabled: ["ADDON_PROBE_FEATURE"] }}>
                <Navigation activeHref={"#/databases"} user={undefined} />
            </RuntimeConfigContext.Provider>
        );

        await waitFor(() => expect(nav()?.findLinkByHref("#/addon-restricted/")).toBeTruthy());
        expect(screen.getByText("Add-on restricted probe")).toBeInTheDocument();
        const lastCheck = mockCheckWebRoutesAllowed.mock.calls.at(-1)![0];
        expect(lastCheck.map((route) => route.route__path)).toContain("/addon-restricted");
        // Every switch an entry lists must be on: the second one is still absent.
        expect(nav()?.findLinkByHref("#/addon-restricted/both/")).toBeFalsy();
        expect(screen.queryByText("Add-on two-switch page")).not.toBeInTheDocument();
    });
});
