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
 * sections, with the fork's after everything. A navigation entry restricted to feature switches
 * shows only while all of them are enabled, and appears when they arrive after mount. Entries with
 * `placement: "profile"` from either lane go to the top-bar profile menu instead, filtered by the
 * same check and placed after the core profile entries.
 */

import React from "react";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { routeTable, AppRoutes } from "../routes";
import { Navigation } from "../layout/Navigation";
import { useProfileMenuItems } from "../layout/useProfileMenuItems";
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
        {
            type: "link",
            text: "Add-on restricted profile probe",
            href: "#/addon-restricted-profile/",
            placement: "profile",
            featuresEnabledRestriction: ["ADDON_PROBE_FEATURE"],
        },
    ],
    addonNavMainSections: [
        {
            type: "section",
            text: "Add-on probe",
            items: [{ type: "link", text: "Add-on probe page", href: "#/addon-probe/" }],
        },
        {
            type: "link",
            text: "Add-on profile probe",
            href: "#/addon-profile-probe/",
            placement: "profile",
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
        {
            type: "section",
            text: "Custom profile probe",
            placement: "profile",
            items: [{ type: "link", text: "Custom profile page", href: "#/custom/profile-probe/" }],
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
        allowRoutes("/executions", "/addon-probe", "/metadataschema");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/addon-probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const executions = text.indexOf("Executions");
        expect(executions).toBeGreaterThan(-1);
        expect(text.indexOf("Add-on probe")).toBeGreaterThan(executions);
        expect(text.indexOf("Metadata Schema")).toBeGreaterThan(text.indexOf("Add-on probe"));
    });

    it("places the add-on admin section after the core admin sections", async () => {
        allowRoutes("/auth/userroles", "/addon-admin-probe", "/auth/api-keys");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/addon-admin-probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const usersInRoles = text.indexOf("Users in Roles");
        expect(usersInRoles).toBeGreaterThan(-1);
        expect(text.indexOf("Add-on admin probe")).toBeGreaterThan(usersInRoles);
        // The user's own API key page is in the profile menu, so there is no "User" section below.
        expect(text).not.toContain("API Key Management");
    });

    it("keeps the fork's section after every core and add-on section", async () => {
        allowRoutes("/addon-probe", "/addon-admin-probe", "/auth/userroles", "/custom/probe");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/custom/probe/")).toBeTruthy());
        const text = container.textContent ?? "";
        const custom = text.indexOf("Custom probe");
        expect(custom).toBeGreaterThan(text.indexOf("Add-on probe"));
        expect(custom).toBeGreaterThan(text.indexOf("Add-on admin probe"));
        expect(custom).toBeGreaterThan(text.indexOf("Users in Roles"));
    });

    it("filters the add-on admin section by the same web-route check", async () => {
        allowRoutes("/auth/userroles");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/auth/userroles/")).toBeTruthy());
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

    it("keeps profile-placed add-on and custom entries out of the side navigation", async () => {
        allowRoutes("/databases", "/addon-profile-probe", "/custom/profile-probe");
        const container = await renderNavigation();
        const nav = createWrapper(container).findSideNavigation();

        await waitFor(() => expect(nav?.findLinkByHref("#/databases/")).toBeTruthy());
        expect(nav?.findLinkByHref("#/addon-profile-probe/")).toBeFalsy();
        expect(nav?.findLinkByHref("#/custom/profile-probe/")).toBeFalsy();
    });
});

describe("profile menu lanes", () => {
    function hrefs(items: any[]): string[] {
        return items.flatMap((item) => [
            ...(item.href ? [item.href] : []),
            ...hrefs(item.items ?? []),
        ]);
    }

    it("places add-on, then custom profile entries after the core ones, before Sign out", async () => {
        allowRoutes("/auth/api-keys", "/addon-profile-probe", "/custom/profile-probe");
        const { result } = renderHook(() => useProfileMenuItems());

        await waitFor(() => expect(hrefs(result.current)).toContain("#/custom/profile-probe/"));
        expect(hrefs(result.current)).toEqual([
            "#/auth/api-keys/?mode=user",
            "#/addon-profile-probe/",
            "#/custom/profile-probe/",
        ]);
        const ids = (result.current as any[]).map((item) => item.id);
        expect(ids[ids.length - 1]).toBe("signout");
        expect(ids).toContain("group:Custom profile probe");
    });

    it("filters add-on and custom profile entries by the same web-route check", async () => {
        allowRoutes("/auth/api-keys");
        const { result } = renderHook(() => useProfileMenuItems());

        await waitFor(() => expect(hrefs(result.current)).toContain("#/auth/api-keys/?mode=user"));
        expect(hrefs(result.current)).not.toContain("#/addon-profile-probe/");
        expect(hrefs(result.current)).not.toContain("#/custom/profile-probe/");
        expect((result.current as any[]).map((item) => item.id)).not.toContain(
            "group:Custom profile probe"
        );
    });

    it("adds a profile entry restricted to a feature switch only while the switch is on", async () => {
        allowRoutes(
            "/auth/api-keys",
            "/addon-profile-probe",
            "/addon-restricted-profile",
            "/custom/profile-probe"
        );
        let features: string[] = [];
        const wrapper = ({ children }: { children: React.ReactNode }) => (
            <RuntimeConfigContext.Provider value={{ featuresEnabled: features }}>
                {children}
            </RuntimeConfigContext.Provider>
        );
        const { result, rerender } = renderHook(() => useProfileMenuItems(), { wrapper });

        await waitFor(() => expect(hrefs(result.current)).toContain("#/custom/profile-probe/"));
        expect(hrefs(result.current)).not.toContain("#/addon-restricted-profile/");
        const firstCheck = mockCheckWebRoutesAllowed.mock.calls[0][0].map(
            (route) => route.route__path
        );
        expect(firstCheck).not.toContain("/addon-restricted-profile");

        features = ["ADDON_PROBE_FEATURE"];
        rerender();

        // Both add-on lists' profile entries follow the core ones, and the fork's come last.
        await waitFor(() =>
            expect(hrefs(result.current)).toEqual([
                "#/auth/api-keys/?mode=user",
                "#/addon-profile-probe/",
                "#/addon-restricted-profile/",
                "#/custom/profile-probe/",
            ])
        );
    });
});
