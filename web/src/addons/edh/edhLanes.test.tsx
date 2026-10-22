/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH entries in the add-on web lanes (addons/addonRoutes.tsx, addons/addonNavigation.ts,
 * addons/addonFeatures.ts), read through the real route table and the real side navigation.
 *
 * Each route serves its page without parameters and highlights the navigation link that leads to it;
 * the Simulations route opens the Workstations page on that tab. "Virtual Workstations" sits in the
 * main menu after the core "Orchestrate & Automate" section and "Admin - Workstations" in the admin
 * area after the core admin sections and before "User"; both show only while EDH_WORKSTATIONS is
 * enabled (the Simulations link
 * also needs EDH_CONTAINERS), are filtered by the web-route check, and appear when the feature
 * switches arrive after mount.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { matchRoutes, MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { routeTable, AppRoutes } from "../../routes";
import { Navigation } from "../../layout/Navigation";
import { RuntimeConfigContext } from "../../context/RuntimeConfigContext";
import { checkWebRoutesAllowed, WebRouteCheck } from "../../services/webRoutesCheck";
import { addonFeaturesEnabled } from "../addonFeatures";
import { EDH_SIMULATIONS_PATH, EDH_WORKSTATIONS_PATH } from "./workstations/simulationsTab";

const WORKSTATIONS_HREF = "#/edh/workstations/";
const SIMULATIONS_HREF = "#/edh/workstations/simulations/";
const ADMIN_HREF = "#/edh/admin/";
const EDH_ROUTE_PATHS = ["/edh/workstations", "/edh/workstations/simulations", "/edh/admin"];
const REACT_LAZY = Symbol.for("react.lazy");

jest.mock("../../services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

// The layout is Cloudscape's; only the content slot matters for the route assertions.
jest.mock("@cloudscape-design/components/app-layout", () => ({
    __esModule: true,
    default: ({ content }: any) => <div data-testid="layout-content">{content}</div>,
}));

jest.mock("../../pages/LandingPage", () => ({
    __esModule: true,
    default: () => <div data-testid="landing-page" />,
}));

jest.mock("./pages/EdhWorkstationsPage", () => ({
    __esModule: true,
    default: () => <div data-testid="edh-workstations-page" />,
}));

jest.mock("./pages/EdhAdminPage", () => ({
    __esModule: true,
    default: () => <div data-testid="edh-admin-page" />,
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

const routeFor = (path: string) => routeTable.find((route) => route.path === path);

/** Every route path the navigation sent to the web-route check so far. */
const checkedRoutePaths = () =>
    mockCheckWebRoutesAllowed.mock.calls.flatMap(([routes]) =>
        routes.map((route) => route.route__path)
    );

function renderNavigation(features: string[]) {
    const { container } = render(
        <RuntimeConfigContext.Provider value={{ featuresEnabled: features }}>
            <Navigation activeHref={"#/databases"} user={undefined} />
        </RuntimeConfigContext.Provider>
    );
    return container;
}

const findNavLink = (container: HTMLElement, href: string) =>
    createWrapper(container).findSideNavigation()?.findLinkByHref(href);

beforeEach(() => {
    jest.clearAllMocks();
});

describe("EDH feature-switch names", () => {
    it("names the three EDH switches exactly as the deployment publishes them", () => {
        expect(addonFeaturesEnabled).toMatchObject({
            EDH_WORKSTATIONS: "EDH_WORKSTATIONS",
            EDH_CONTAINERS: "EDH_CONTAINERS",
            EDH_AMI_RECIPES: "EDH_AMI_RECIPES",
        });
    });
});

describe("EDH routes", () => {
    it.each(EDH_ROUTE_PATHS)(
        "serves %s from the add-on lane, lazily, without route parameters, highlighting its nav link",
        (path) => {
            const entry = routeFor(path);
            expect(entry).toBeDefined();
            // After the core table, which ends with the catch-all.
            expect(routeTable.indexOf(entry!)).toBeGreaterThan(
                routeTable.findIndex((route) => route.path === "*")
            );
            expect((entry!.Page as any).$$typeof).toBe(REACT_LAZY);
            // Navigation renders the link as "#<path>/"; `active` must equal that href for the side
            // navigation to highlight the page.
            expect(entry!.active).toBe(`#${path}/`);
            const matched = matchRoutes(routeTable as any, path);
            const leaf = matched![matched!.length - 1];
            expect((leaf.route as any).Page).toBe(entry!.Page);
            expect(leaf.params).toEqual({});
        }
    );

    it("opens the Workstations page at the Simulations route the page selects its tab from", () => {
        expect(routeFor(EDH_WORKSTATIONS_PATH)).toBeDefined();
        expect(routeFor(EDH_SIMULATIONS_PATH)).toBeDefined();
        expect(routeFor(EDH_SIMULATIONS_PATH)!.Page).toBe(routeFor(EDH_WORKSTATIONS_PATH)!.Page);
        expect(routeFor("/edh/admin")!.Page).not.toBe(routeFor(EDH_WORKSTATIONS_PATH)!.Page);
    });

    it("renders the Workstations page at the Simulations route when the web constraint allows it", async () => {
        allowRoutes("/edh/workstations/simulations");

        render(
            <MemoryRouter initialEntries={["/edh/workstations/simulations/"]}>
                <AppRoutes navigationOpen={true} setNavigationOpen={jest.fn()} user={undefined} />
            </MemoryRouter>
        );

        await waitFor(() =>
            expect(screen.getByTestId("edh-workstations-page")).toBeInTheDocument()
        );
    });

    it("keeps the Simulations route behind the same web-route check as any page", async () => {
        allowRoutes("/edh/admin");

        render(
            <MemoryRouter initialEntries={["/edh/workstations/simulations/"]}>
                <AppRoutes navigationOpen={true} setNavigationOpen={jest.fn()} user={undefined} />
            </MemoryRouter>
        );

        // "*" is always added back, so the unmatched path lands on the landing page.
        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeInTheDocument());
        expect(screen.queryByTestId("edh-workstations-page")).not.toBeInTheDocument();
    });
});

describe("EDH navigation sections", () => {
    it("places Virtual Workstations after Orchestrate & Automate and Admin - Workstations before User", async () => {
        allowRoutes(
            "/executions/",
            "/metadataschema/",
            "/auth/userroles/",
            "/auth/api-keys/",
            "/edh/workstations/",
            "/edh/admin/"
        );
        const container = renderNavigation([addonFeaturesEnabled.EDH_WORKSTATIONS]);

        await waitFor(() => expect(findNavLink(container, ADMIN_HREF)).toBeTruthy());
        const text = container.textContent ?? "";
        const at = (label: string) => {
            const index = text.indexOf(label);
            expect(index).toBeGreaterThan(-1);
            return index;
        };
        // Main menu: right after the core Orchestrate & Automate links, ahead of the admin area.
        expect(at("Virtual Workstations")).toBeGreaterThan(at("Executions"));
        expect(at("Metadata Schema")).toBeGreaterThan(at("Virtual Workstations"));
        // Admin area: after the core admin sections, ahead of the User section.
        expect(at("Admin - Workstations")).toBeGreaterThan(at("Users in Roles"));
        expect(at("API Key Management")).toBeGreaterThan(at("Admin - Workstations"));
    });

    it("offers My Workstations when the switch is on and the route is allowed", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/workstations/simulations/");
        const container = renderNavigation([addonFeaturesEnabled.EDH_WORKSTATIONS]);

        await waitFor(() => expect(findNavLink(container, WORKSTATIONS_HREF)).toBeTruthy());
        expect(screen.getByText("Virtual Workstations")).toBeInTheDocument();
        expect(screen.getByText("My Workstations")).toBeInTheDocument();
        // Without the containers switch the section carries the one link, whatever the route allows.
        expect(findNavLink(container, SIMULATIONS_HREF)).toBeFalsy();
        expect(screen.queryByText("Simulations")).not.toBeInTheDocument();
    });

    it("omits both sections when the switch is absent, even though the routes are allowed", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/admin/");
        const container = renderNavigation([]);

        await waitFor(() => expect(findNavLink(container, "#/databases/")).toBeTruthy());
        expect(findNavLink(container, WORKSTATIONS_HREF)).toBeFalsy();
        expect(findNavLink(container, ADMIN_HREF)).toBeFalsy();
        expect(screen.queryByText("Virtual Workstations")).not.toBeInTheDocument();
        expect(screen.queryByText("Admin - Workstations")).not.toBeInTheDocument();
        expect(checkedRoutePaths()).not.toContain("/edh/workstations/");
    });

    it("omits the section when the switch is on but the web route is not allowed", async () => {
        allowRoutes("/databases/");
        const container = renderNavigation([addonFeaturesEnabled.EDH_WORKSTATIONS]);

        await waitFor(() => expect(findNavLink(container, "#/databases/")).toBeTruthy());
        expect(findNavLink(container, WORKSTATIONS_HREF)).toBeFalsy();
        expect(screen.queryByText("Virtual Workstations")).not.toBeInTheDocument();
    });

    it("adds a Simulations link when containers are on, checked against its own route", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/workstations/simulations/");
        const container = renderNavigation([
            addonFeaturesEnabled.EDH_WORKSTATIONS,
            addonFeaturesEnabled.EDH_CONTAINERS,
        ]);

        await waitFor(() => expect(findNavLink(container, SIMULATIONS_HREF)).toBeTruthy());
        expect(screen.getByText("Simulations")).toBeInTheDocument();
        expect(findNavLink(container, WORKSTATIONS_HREF)).toBeTruthy();
        expect(checkedRoutePaths()).toContain("/edh/workstations/simulations/");
        expect(SIMULATIONS_HREF).toBe(`#${EDH_SIMULATIONS_PATH}/`);
    });

    it("omits the Simulations link when its route is not allowed", async () => {
        allowRoutes("/databases/", "/edh/workstations/");
        const container = renderNavigation([
            addonFeaturesEnabled.EDH_WORKSTATIONS,
            addonFeaturesEnabled.EDH_CONTAINERS,
        ]);

        await waitFor(() => expect(findNavLink(container, WORKSTATIONS_HREF)).toBeTruthy());
        expect(findNavLink(container, SIMULATIONS_HREF)).toBeFalsy();
    });

    it("shows nothing for the containers switch alone", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/workstations/simulations/");
        const container = renderNavigation([addonFeaturesEnabled.EDH_CONTAINERS]);

        await waitFor(() => expect(findNavLink(container, "#/databases/")).toBeTruthy());
        expect(findNavLink(container, SIMULATIONS_HREF)).toBeFalsy();
        expect(screen.queryByText("Virtual Workstations")).not.toBeInTheDocument();
    });

    it("adds the sections when the feature list arrives after mount, without a reload", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/admin/");
        // The amplify config the shell holds before the secure config lands carries no features.
        const { container, rerender } = render(
            <RuntimeConfigContext.Provider value={{ api: "https://api.example.test" }}>
                <Navigation activeHref={"#/databases"} user={undefined} />
            </RuntimeConfigContext.Provider>
        );
        await waitFor(() => expect(findNavLink(container, "#/databases/")).toBeTruthy());
        expect(findNavLink(container, WORKSTATIONS_HREF)).toBeFalsy();
        expect(screen.queryByText("Virtual Workstations")).not.toBeInTheDocument();

        rerender(
            <RuntimeConfigContext.Provider
                value={{
                    api: "https://api.example.test",
                    featuresEnabled: [addonFeaturesEnabled.EDH_WORKSTATIONS],
                }}
            >
                <Navigation activeHref={"#/databases"} user={undefined} />
            </RuntimeConfigContext.Provider>
        );

        await waitFor(() => expect(findNavLink(container, WORKSTATIONS_HREF)).toBeTruthy());
        expect(screen.getByText("Virtual Workstations")).toBeInTheDocument();
        expect(findNavLink(container, ADMIN_HREF)).toBeTruthy();
        expect(screen.getByText("Admin - Workstations")).toBeInTheDocument();
    });

    it("offers Workstation Admin under Admin - Workstations when the switch is on and the route is allowed", async () => {
        allowRoutes("/databases/", "/edh/workstations/", "/edh/admin/");
        const container = renderNavigation([addonFeaturesEnabled.EDH_WORKSTATIONS]);

        await waitFor(() => expect(findNavLink(container, ADMIN_HREF)).toBeTruthy());
        expect(screen.getByText("Admin - Workstations")).toBeInTheDocument();
        expect(screen.getByText("Workstation Admin")).toBeInTheDocument();
    });

    it("omits Admin - Workstations when the switch is on but its web route is not allowed", async () => {
        allowRoutes("/databases/", "/edh/workstations/");
        const container = renderNavigation([addonFeaturesEnabled.EDH_WORKSTATIONS]);

        await waitFor(() => expect(findNavLink(container, WORKSTATIONS_HREF)).toBeTruthy());
        expect(findNavLink(container, ADMIN_HREF)).toBeFalsy();
        expect(screen.queryByText("Admin - Workstations")).not.toBeInTheDocument();
    });
});
