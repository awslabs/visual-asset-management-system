/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An add-on's web routes exist only on a deployment that publishes the add-on's feature switches.
 *
 * Each add-on route carries the switches it needs (`featuresEnabledRestriction`). Without them the
 * route is not mounted (its URL reaches the not-found panel), is not sent to the web-route check, and
 * is not offered in the Create Constraint editor's route list; with them it is. The object types the
 * editor offers are the API's list (`GET /auth/constraints/permissionObjects`), which leaves out an
 * add-on that is not deployed. A stored constraint whose type the API no longer offers opens with its
 * stored name and a notice, and keeps its criteria.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes, routeTableFor } from "../routes";
import { RuntimeConfigContext } from "../context/RuntimeConfigContext";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";
import CreateConstraint, { webRouteOptionsFor } from "../pages/auth/CreateConstraint";
import { addonFeaturesEnabled } from "./addonFeatures";

jest.mock("../services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

jest.mock("@cloudscape-design/components/app-layout", () => ({
    __esModule: true,
    default: ({ content }: any) => <div data-testid="layout-content">{content}</div>,
}));

jest.mock("../pages/LandingPage", () => ({
    __esModule: true,
    default: () => <div data-testid="landing-page" />,
}));

jest.mock("./edh/pages/EdhWorkstationsPage", () => ({
    __esModule: true,
    default: () => <div data-testid="edh-workstations-page" />,
}));

jest.mock("../services/APIService", () => ({
    createConstraint: jest.fn(),
    fetchRoles: jest.fn().mockResolvedValue([]),
    fetchApiRoutes: jest.fn().mockResolvedValue([true, { routes: [] }]),
    fetchConstraintPermissionObjects: jest.fn(),
}));

const EDH = [addonFeaturesEnabled.EDH_WORKSTATIONS];
const EDH_ALL = [...EDH, addonFeaturesEnabled.EDH_CONTAINERS];
const EDH_PATHS = ["/edh/workstations", "/edh/workstations/simulations", "/edh/admin"];
const isEdh = (path: string) => path.startsWith("/edh/");

const mockCheck = checkWebRoutesAllowed as jest.MockedFunction<typeof checkWebRoutesAllowed>;

beforeEach(() => {
    jest.clearAllMocks();
    mockCheck.mockImplementation(async (routes: WebRouteCheck[]) => routes);
});

describe("the route table follows the deployment's switches", () => {
    it("has no EDH route with the add-on off, and each one with its switches", () => {
        const off = routeTableFor([]).map((r) => r.path);
        expect(off.filter(isEdh)).toEqual([]);
        expect(off).toContain("/databases");
        expect(
            routeTableFor(EDH)
                .map((r) => r.path)
                .filter(isEdh)
        ).toEqual(["/edh/workstations", "/edh/admin"]);
        expect(
            routeTableFor(EDH_ALL)
                .map((r) => r.path)
                .filter(isEdh)
        ).toEqual(EDH_PATHS);
    });
});

describe("the web-route check and the mounted pages", () => {
    const renderAt = (url: string, features: string[]) =>
        render(
            <RuntimeConfigContext.Provider value={{ featuresEnabled: features }}>
                <MemoryRouter initialEntries={[url]}>
                    <AppRoutes
                        navigationOpen={true}
                        setNavigationOpen={jest.fn()}
                        user={undefined}
                    />
                </MemoryRouter>
            </RuntimeConfigContext.Provider>
        );
    const checked = () =>
        mockCheck.mock.calls.flatMap(([routes]) => routes.map((route) => route.route__path));

    it("with the add-on off: no EDH route is checked and its URL is not found", async () => {
        renderAt("/edh/workstations/", []);
        expect(await screen.findByText("Page not found")).toBeInTheDocument();
        expect(checked().length).toBeGreaterThan(0);
        expect(checked().filter(isEdh)).toEqual([]);
        expect(screen.queryByTestId("edh-workstations-page")).not.toBeInTheDocument();
    });

    it("with the add-on on: its routes are checked and its page renders", async () => {
        renderAt("/edh/workstations/", EDH_ALL);
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
        expect(checked().filter(isEdh)).toEqual(EDH_PATHS);
    });

    it("checks the add-on's routes again once its switches arrive after the first check", async () => {
        const view = (features: string[]) => (
            <RuntimeConfigContext.Provider value={{ featuresEnabled: features }}>
                <MemoryRouter initialEntries={["/edh/workstations/"]}>
                    <AppRoutes
                        navigationOpen={true}
                        setNavigationOpen={jest.fn()}
                        user={undefined}
                    />
                </MemoryRouter>
            </RuntimeConfigContext.Provider>
        );
        const { rerender } = render(view([]));
        expect(await screen.findByText("Page not found")).toBeInTheDocument();
        rerender(view(EDH));
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
        expect(mockCheck).toHaveBeenCalledTimes(2);
        expect(mockCheck.mock.calls[1][0].map((r) => r.route__path).filter(isEdh)).toEqual([
            "/edh/workstations",
            "/edh/admin",
        ]);
    });
});

describe("Create Constraint", () => {
    const permissionObjects = (types: { label: string; value: string; fields: any[] }[]) => [
        true,
        {
            objectTypes: types,
            operators: [{ label: "Contains", value: "contains" }],
            permissions: [{ label: "View/GET", value: "GET" }],
            permissionTypes: [{ label: "Allow", value: "allow" }],
        },
    ];
    const ASSET = {
        label: "Asset",
        value: "asset",
        fields: [{ label: "Asset Name", value: "assetName" }],
    };

    it("offers no EDH web route with the add-on off, and its routes with it on", () => {
        const off = webRouteOptionsFor([]).map((o) => o.value);
        expect(off[0]).toBe(".*");
        expect(off).toContain("/databases");
        expect(off.filter(isEdh)).toEqual([]);
        expect(
            webRouteOptionsFor(EDH_ALL)
                .map((o) => o.value)
                .filter(isEdh)
        ).toEqual(EDH_PATHS);
    });

    it("opens a stored constraint of a type the API no longer offers with its name and a notice", async () => {
        const { fetchConstraintPermissionObjects } = require("../services/APIService");
        fetchConstraintPermissionObjects.mockResolvedValue(permissionObjects([ASSET]));
        render(
            <RuntimeConfigContext.Provider value={{ featuresEnabled: [] }}>
                <MemoryRouter>
                    <CreateConstraint
                        open={true}
                        setOpen={jest.fn()}
                        setReload={jest.fn()}
                        initState={{
                            constraintId: "edh_ws_admin",
                            name: "edh_ws_admin",
                            description: "EDH workstations for the admin role",
                            objectType: "edhWorkstation",
                            criteriaAnd: [
                                {
                                    id: "c1",
                                    field: "edhImageId",
                                    operator: "contains",
                                    value: ".*",
                                },
                            ],
                            criteriaOr: [],
                            groupPermissions: [
                                {
                                    id: "g1",
                                    groupId: "admin",
                                    permission: "GET",
                                    permissionType: "allow",
                                },
                            ],
                            userPermissions: [],
                        }}
                    />
                </MemoryRouter>
            </RuntimeConfigContext.Provider>
        );
        expect(
            await screen.findByText("This object type is not offered on this deployment")
        ).toBeInTheDocument();
        expect(
            screen.getByText("edhWorkstation (not offered on this deployment)")
        ).toBeInTheDocument();
        // The criterion stays, shown by its stored field name.
        expect(screen.getByText("edhImageId")).toBeInTheDocument();
    });

    it("shows no notice for a constraint whose type the API offers (control)", async () => {
        const { fetchConstraintPermissionObjects } = require("../services/APIService");
        fetchConstraintPermissionObjects.mockResolvedValue(permissionObjects([ASSET]));
        render(
            <MemoryRouter>
                <CreateConstraint
                    open={true}
                    setOpen={jest.fn()}
                    setReload={jest.fn()}
                    initState={{
                        constraintId: "c",
                        name: "c",
                        description: "a valid description",
                        objectType: "asset",
                        criteriaAnd: [
                            { id: "c1", field: "assetName", operator: "contains", value: "x" },
                        ],
                        criteriaOr: [],
                        groupPermissions: [],
                        userPermissions: [],
                    }}
                />
            </MemoryRouter>
        );
        await waitFor(() => expect(fetchConstraintPermissionObjects).toHaveBeenCalled());
        expect(await screen.findByText("Asset Name")).toBeInTheDocument();
        expect(
            screen.queryByText("This object type is not offered on this deployment")
        ).not.toBeInTheDocument();
    });
});
