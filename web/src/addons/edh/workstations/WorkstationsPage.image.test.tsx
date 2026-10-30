/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The image a workstation runs, on My Workstations (feature #536, D14): the record's own name wins
 * over the live catalog, so a workstation launched from a build the catalog no longer lists keeps
 * its name; a live workstation on an older build carries the `Newer image` badge; and the search
 * matches the name the cell shows.
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage, { CATALOG_IMAGES_ROUTE } from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import type { WorkstationRecord } from "../types";

jest.mock("../api/queries", () => ({
    WORKSTATION_POLL_TRANSITIONAL_MS: 5000,
    WORKSTATION_POLL_STABLE_MS: 20000,
    useWorkstations: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useWorkstationConnect: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useOsSignIn: jest.fn(),
    useEdhCatalogImages: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhInstanceTypes: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(() => ({
        data: [],
        isLoading: false,
        error: null,
    })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: ["EDH_WORKSTATIONS"] })) },
}));

const TOOLTIP =
    "This workstation runs v13 of Isaac Sim 6.2. v14 is the latest build. A workstation keeps the image it was launched from; launch a new workstation to use the latest build.";

const base: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Sim box",
    imageId: "31",
    osFamily: "linux",
    instanceType: "g6e.4xlarge",
    createdAt: "2026-10-20T10:00:00Z",
    updatedAt: "2026-10-20T10:05:00Z",
    connectionAvailable: true,
    activity: null,
};

// Launched from build 13 of the Isaac Sim recipe; build 14 has since retired 13 from the catalog.
const olderBuildRow: WorkstationRecord = {
    ...base,
    imageName: "isaacsim-6-2-v13",
    recipeId: "isaacsim",
    recipeDisplayName: "Isaac Sim 6.2",
    recipeVersion: 13,
    latestRecipeVersion: 14,
    imageLatest: false,
};

const latestBuildRow: WorkstationRecord = {
    ...olderBuildRow,
    bootstrapId: "b-2",
    sessionId: "s-2",
    name: "Fresh box",
    imageId: "36",
    imageName: "isaacsim-6-2-v14",
    recipeVersion: 14,
    imageLatest: true,
};

const orphanedOlderRow: WorkstationRecord = {
    ...olderBuildRow,
    bootstrapId: "b-3",
    sessionId: null,
    name: "Failed box",
    state: "ORPHANED",
    edhState: null,
    connectionAvailable: false,
    lastError: "QUOTA_EXCEEDED",
};

const baselineRow: WorkstationRecord = {
    ...base,
    bootstrapId: "b-4",
    sessionId: "s-4",
    name: "Plain box",
    imageId: "12",
    imageName: "Ubuntu 22 DCV",
    recipeId: null,
    recipeDisplayName: null,
    recipeVersion: null,
    latestRecipeVersion: null,
    imageLatest: null,
};

const list = (rows: WorkstationRecord[]) => ({
    data: rows,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const catalog = (images: Array<{ imageId: string; name: string }>) => ({
    data: images.map((image) => ({
        ...image,
        baseOs: "ubuntu2204",
        osFamily: "linux",
        rootSize: 100,
        description: null,
    })),
    isLoading: false,
    error: null,
});

const renderPage = () =>
    render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>
                <WorkstationsPage />
            </MemoryRouter>
        </QueryClientProvider>
    );

const rowOf = (name: string) => within(screen.getByText(name).closest("tr") as HTMLElement);

describe("WorkstationsPage image name and newer-image badge", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
            createWorkstation: { mutateAsync: jest.fn(), isPending: false },
            stopWorkstation: { mutateAsync: jest.fn(), isPending: false },
            startWorkstation: { mutateAsync: jest.fn(), isPending: false },
            deleteWorkstation: { mutateAsync: jest.fn(), isPending: false },
            renewWorkstation: { mutateAsync: jest.fn(), isPending: false },
        });
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(catalog([]));
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue({
            data: undefined,
            isLoading: false,
            error: null,
        });
    });

    it("names an image the catalog no longer lists from the record, with the id beside it", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([olderBuildRow]));
        renderPage();

        const row = rowOf("Sim box");
        expect(row.getByText("Isaac Sim 6.2 v13")).toBeInTheDocument();
        expect(row.getByText("31")).toBeInTheDocument();
    });

    it("prefers the record's name to the catalog's", () => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(
            catalog([
                { imageId: "36", name: "isaacsim-6-2-v14" },
                { imageId: "12", name: "Ubuntu 22 (catalog name)" },
            ])
        );
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([latestBuildRow, baselineRow]));
        renderPage();

        expect(rowOf("Fresh box").getByText("Isaac Sim 6.2 v14")).toBeInTheDocument();
        expect(rowOf("Plain box").getByText("Ubuntu 22 DCV")).toBeInTheDocument();
        expect(screen.queryByText("Ubuntu 22 (catalog name)")).not.toBeInTheDocument();
        expect(screen.queryByText("isaacsim-6-2-v14")).not.toBeInTheDocument();
    });

    it("names the image for a caller who may not read the catalog", () => {
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: (method: string, path: string) =>
                !(method === "GET" && path === CATALOG_IMAGES_ROUTE),
        });
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([olderBuildRow]));
        renderPage();

        expect(queries.useEdhCatalogImages).toHaveBeenCalledWith(false);
        expect(rowOf("Sim box").getByText("Isaac Sim 6.2 v13")).toBeInTheDocument();
    });

    it("badges a live workstation on an older build, and not one on the latest or a failed launch", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([olderBuildRow, latestBuildRow, orphanedOlderRow, baselineRow])
        );
        renderPage();

        expect(screen.getAllByText(/^Newer image:/)).toHaveLength(1);
        const badge = rowOf("Sim box").getByText("Newer image: v14");
        expect(badge).toHaveAttribute("title", TOOLTIP);
        expect(badge).toHaveAccessibleDescription(TOOLTIP);
        // The failed launch still names its image.
        expect(rowOf("Failed box").getByText("Isaac Sim 6.2 v13")).toBeInTheDocument();
    });

    it("finds a workstation by the image name its cell shows", async () => {
        const user = userEvent.setup();
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([olderBuildRow, latestBuildRow, baselineRow])
        );
        renderPage();

        const search = screen.getByRole("textbox", { name: "Search workstations" });
        await user.type(search, "Isaac Sim 6.2 v13");
        expect(screen.getByText("Sim box")).toBeInTheDocument();
        expect(screen.queryByText("Fresh box")).not.toBeInTheDocument();
        expect(screen.queryByText("Plain box")).not.toBeInTheDocument();

        await user.clear(search);
        await user.type(search, "Ubuntu 22 DCV");
        expect(screen.getByText("Plain box")).toBeInTheDocument();
        expect(screen.queryByText("Sim box")).not.toBeInTheDocument();
    });
});
