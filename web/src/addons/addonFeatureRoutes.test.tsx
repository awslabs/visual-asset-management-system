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
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes, routeTableFor } from "../routes";
import { RuntimeConfigContext } from "../context/RuntimeConfigContext";
import { checkWebRoutesAllowed, WebRouteCheck } from "../services/webRoutesCheck";
import CreateConstraint, { webRouteOptionsFor } from "../pages/auth/CreateConstraint";
import { fetchConstraintPermissionObjects } from "../services/APIService";
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

describe("answers to the web-route check that arrive out of order", () => {
    // The check sent before the switches arrived can answer after the check sent with them (a cold
    // Lambda, a slow network). Only the latest check's answer may decide what the page shows.
    const view = (features: string[]) => (
        <RuntimeConfigContext.Provider value={{ featuresEnabled: features }}>
            <MemoryRouter initialEntries={["/edh/workstations/"]}>
                <AppRoutes navigationOpen={true} setNavigationOpen={jest.fn()} user={undefined} />
            </MemoryRouter>
        </RuntimeConfigContext.Provider>
    );
    const FAILED = "Page access could not be checked";
    let answers: Array<{ allow: () => void; fail: () => void }>;
    let consoleError: jest.SpyInstance;

    beforeEach(() => {
        answers = [];
        consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
        mockCheck.mockImplementation(
            (routes: WebRouteCheck[]) =>
                new Promise((resolve, reject) =>
                    answers.push({
                        allow: () => resolve(routes),
                        fail: () => reject(new Error("routes check unavailable")),
                    })
                )
        );
    });

    afterEach(() => consoleError.mockRestore());

    // Renders with no switches, then with the add-on's: two checks, both still unanswered.
    const twoChecksInFlight = () => {
        const { rerender } = render(view([]));
        rerender(view(EDH));
        expect(mockCheck).toHaveBeenCalledTimes(2);
        expect(answers).toHaveLength(2);
    };

    it("keeps the add-on's page when the earlier check succeeds after the later one", async () => {
        twoChecksInFlight();
        await act(async () => answers[1].allow());
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
        await act(async () => answers[0].allow());
        expect(screen.getByTestId("edh-workstations-page")).toBeInTheDocument();
    });

    it("keeps the add-on's page when the earlier check fails after the later one succeeded", async () => {
        twoChecksInFlight();
        await act(async () => answers[1].allow());
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
        await act(async () => answers[0].fail());
        expect(screen.getByTestId("edh-workstations-page")).toBeInTheDocument();
        expect(screen.queryByText(FAILED)).not.toBeInTheDocument();
    });

    it("opens the page when a later check succeeds after the earlier one failed", async () => {
        const { rerender } = render(view([]));
        await act(async () => answers[0].fail());
        expect(await screen.findByText(FAILED)).toBeInTheDocument();
        rerender(view(EDH));
        expect(answers).toHaveLength(2);
        await act(async () => answers[1].allow());
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
        expect(screen.queryByText(FAILED)).not.toBeInTheDocument();
    });

    it("control: shows the page when the answers arrive in order", async () => {
        twoChecksInFlight();
        await act(async () => answers[0].allow());
        await act(async () => answers[1].allow());
        expect(await screen.findByTestId("edh-workstations-page")).toBeInTheDocument();
    });

    it("control: still shows the could-not-check panel when the latest check fails", async () => {
        twoChecksInFlight();
        await act(async () => answers[0].allow());
        await act(async () => answers[1].fail());
        expect(await screen.findByText(FAILED)).toBeInTheDocument();
        expect(screen.queryByTestId("edh-workstations-page")).not.toBeInTheDocument();
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
        (fetchConstraintPermissionObjects as jest.Mock).mockResolvedValue(
            permissionObjects([ASSET])
        );
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

    it("names an optional add-on feature as a cause when the add-on itself is on", async () => {
        // EDH is on and container jobs are off: the API offers the add-on's types but not
        // edhContainer, so the notice must not say the add-on is off.
        const EDH_WORKSTATION = {
            label: "EDH Workstation",
            value: "edhWorkstation",
            fields: [{ label: "EDH Image ID", value: "edhImageId" }],
        };
        (fetchConstraintPermissionObjects as jest.Mock).mockResolvedValue(
            permissionObjects([ASSET, EDH_WORKSTATION])
        );
        render(
            <RuntimeConfigContext.Provider value={{ featuresEnabled: EDH }}>
                <MemoryRouter>
                    <CreateConstraint
                        open={true}
                        setOpen={jest.fn()}
                        setReload={jest.fn()}
                        initState={{
                            constraintId: "initial_admin_allow_all_edhcontainers",
                            name: "admin-allow-all-edhcontainers",
                            description: "Allow GET/PUT/POST/DELETE on all EDH container jobs",
                            objectType: "edhContainer",
                            criteriaAnd: [
                                {
                                    id: "c1",
                                    field: "edhJobDefinition",
                                    operator: "contains",
                                    value: ".*",
                                },
                            ],
                            criteriaOr: [],
                            groupPermissions: [],
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
            screen.getByText(
                /because the add-on, or the optional add-on feature, that declares it is not enabled\. .*applies again once that add-on or feature is enabled\./
            )
        ).toBeInTheDocument();
        expect(
            screen.getByText("edhContainer (not offered on this deployment)")
        ).toBeInTheDocument();
    });

    it("shows no notice for a constraint whose type the API offers (control)", async () => {
        (fetchConstraintPermissionObjects as jest.Mock).mockResolvedValue(
            permissionObjects([ASSET])
        );
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
