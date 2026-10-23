/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import WorkstationsPage from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";

jest.mock("../api/queries", () => ({
    WORKSTATION_POLL_TRANSITIONAL_MS: 5000,
    WORKSTATION_POLL_STABLE_MS: 20000,
    useWorkstations: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useWorkstationConnect: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useEdhCatalogImages: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhInstanceTypes: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

jest.mock("../containers/SimulationsTab", () => ({
    __esModule: true,
    default: () => {
        const React = require("react");
        return React.createElement(
            "div",
            { "data-testid": "simulations-panel" },
            "Simulations panel"
        );
    },
}));

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

/** Shows the router's path, so a tab change can be checked against the URL it writes. */
const LocationProbe: React.FC = () => {
    const location = useLocation();
    return <span data-testid="location-pathname">{location.pathname}</span>;
};

const currentPath = () => screen.getByTestId("location-pathname").textContent;

const renderPage = (initialEntry = "/edh/workstations/") =>
    render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={[initialEntry]}>
                <WorkstationsPage />
                <LocationProbe />
            </MemoryRouter>
        </QueryClientProvider>
    );

const withFeatures = (features: string[]) => {
    const { appCache } = require("../../../services/appCache");
    appCache.getItem.mockReturnValue({ featuresEnabled: features });
};

const allow = (can: (method: string, path: string) => boolean, loading = false) =>
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({ loading, can });

const isContainerRoute = (path: string) => path.startsWith("/addon/edh/containers");

describe("WorkstationsPage with the containers feature", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (queries.useWorkstations as jest.Mock).mockReturnValue({
            data: [],
            isLoading: false,
            error: null,
            refetch: jest.fn(),
            isFetching: false,
        });
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
            createWorkstation: { mutateAsync: jest.fn(), isPending: false },
            stopWorkstation: { mutateAsync: jest.fn(), isPending: false },
            startWorkstation: { mutateAsync: jest.fn(), isPending: false },
            deleteWorkstation: { mutateAsync: jest.fn(), isPending: false },
            renewWorkstation: { mutateAsync: jest.fn(), isPending: false },
        });
        allow(() => true);
    });

    it("renders no tab strip when only EDH_WORKSTATIONS is on", () => {
        withFeatures(["EDH_WORKSTATIONS"]);
        renderPage();
        expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
        expect(screen.getByRole("heading", { level: 1, name: "Workstations" })).toBeInTheDocument();
        expect(screen.queryByTestId("simulations-panel")).not.toBeInTheDocument();
    });

    it("renders the heading above the strip with Workstations selected when EDH_CONTAINERS is on too", () => {
        withFeatures(["EDH_WORKSTATIONS", "EDH_CONTAINERS"]);
        renderPage();
        const heading = screen.getByRole("heading", { level: 1, name: "Workstations" });
        const tablist = screen.getByRole("tablist", { name: "EDH features" });
        expect(
            heading.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        expect(screen.getByRole("tab", { name: "Workstations" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        // One page heading: the board renders none of its own under the strip.
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
        // On the toolbar row, and repeated in the empty state.
        expect(screen.getAllByRole("button", { name: "Launch Workstation" })).toHaveLength(2);
        expect(screen.queryByTestId("simulations-panel")).not.toBeInTheDocument();
    });

    it("switches to the Simulations panel and back, keeping the page heading and writing the tab to the URL", async () => {
        const user = userEvent.setup();
        withFeatures(["EDH_WORKSTATIONS", "EDH_CONTAINERS"]);
        renderPage();

        await user.click(screen.getByRole("tab", { name: "Simulations" }));
        expect(screen.getByTestId("simulations-panel")).toBeInTheDocument();
        expect(screen.getByRole("heading", { level: 1, name: "Workstations" })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(currentPath()).toBe("/edh/workstations/simulations");
        expect(screen.getByRole("tabpanel")).toHaveAttribute(
            "aria-labelledby",
            "edh-tab-simulations"
        );

        await user.click(screen.getByRole("tab", { name: "Workstations" }));
        expect(screen.queryByTestId("simulations-panel")).not.toBeInTheDocument();
        expect(screen.getByRole("heading", { level: 1, name: "Workstations" })).toBeInTheDocument();
        expect(currentPath()).toBe("/edh/workstations");
    });

    it("opens on the Simulations tab at the route the navigation links to", () => {
        withFeatures(["EDH_WORKSTATIONS", "EDH_CONTAINERS"]);
        renderPage("/edh/workstations/simulations/");
        expect(screen.getByTestId("simulations-panel")).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByRole("heading", { level: 1, name: "Workstations" })).toBeInTheDocument();
    });

    it("shows the Simulations tab to a caller without any container route; the panel gates access", () => {
        withFeatures(["EDH_WORKSTATIONS", "EDH_CONTAINERS"]);
        allow((_method, path) => !isContainerRoute(path));
        const { unmount } = renderPage("/edh/workstations/simulations/");
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByTestId("simulations-panel")).toBeInTheDocument();
        unmount();

        // The strip does not wait for the permissions either.
        allow(() => false, true);
        renderPage();
        expect(screen.getByRole("tab", { name: "Simulations" })).toBeInTheDocument();
    });

    it("keeps the not-enabled panel when EDH_WORKSTATIONS is absent, whatever EDH_CONTAINERS says", () => {
        withFeatures(["EDH_CONTAINERS"]);
        renderPage();
        expect(screen.getByRole("status")).toHaveTextContent(
            "EDH Workstations is not enabled in this deployment."
        );
        expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    });
});
