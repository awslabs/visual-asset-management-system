/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The layout of My Workstations: below 640 px each workstation is a card carrying its name, status,
 * image, instance type, remaining lease, Connect and actions, instead of a table that scrolls them
 * off-screen; at 640 px and wider the table stays. The toolbar says when the list was read, and the
 * caption moves on as time passes. The launch wizard numbers its default name past the listed names.
 */

import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage, { WORKSTATION_TABLE_MIN_WIDTH_PX } from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import type { CatalogLeasePolicy, WorkstationRecord } from "../types";

jest.mock("../api/queries", () => ({
    WORKSTATION_POLL_TRANSITIONAL_MS: 5000,
    WORKSTATION_POLL_STABLE_MS: 20000,
    useWorkstations: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useWorkstationConnect: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useOsSignIn: jest.fn(() => ({
        mutate: jest.fn(),
        reset: jest.fn(),
        data: undefined,
        error: null,
        isPending: false,
    })),
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhCatalogLease: jest.fn(),
    useEdhCatalogCapacityReservations: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: ["EDH_WORKSTATIONS"] })) },
}));

const LEASE_ON: CatalogLeasePolicy = {
    enabled: true,
    leaseHours: 8,
    forced: false,
    graceHours: 24,
};

const activeRow: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Dev box",
    imageId: "42",
    imageName: "VAMS Isaac Sim v4",
    osFamily: "linux",
    instanceType: "g5.2xlarge",
    createdAt: "2026-11-01T10:00:00Z",
    updatedAt: "2026-11-01T10:05:00Z",
    connectionAvailable: true,
    leaseExpiresAt: new Date(Date.now() + 3 * 3600 * 1000 + 60 * 1000).toISOString(),
};

const stoppedRow: WorkstationRecord = {
    ...activeRow,
    bootstrapId: "b-2",
    sessionId: "s-2",
    name: "vams-isaac-sim-v4-1",
    state: "STOPPED",
    edhState: "stopped",
    connectionAvailable: false,
};

const mutations = () => ({
    createWorkstation: { mutateAsync: jest.fn(), isPending: false },
    stopWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    startWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    deleteWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    renewWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
});

type Listener = () => void;

/** A viewport `width` px wide, answering min-width media queries and announcing a resize. */
function viewport(width: number) {
    const listeners = new Set<Listener>();
    const state = { width };
    window.matchMedia = jest.fn((query: string) => {
        const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0);
        return {
            get matches() {
                return state.width >= min;
            },
            media: query,
            onchange: null,
            addEventListener: (_: string, listener: Listener) => listeners.add(listener),
            removeEventListener: (_: string, listener: Listener) => listeners.delete(listener),
            addListener: (listener: Listener) => listeners.add(listener),
            removeListener: (listener: Listener) => listeners.delete(listener),
            dispatchEvent: () => true,
        } as unknown as MediaQueryList;
    });
    return {
        resize(next: number) {
            state.width = next;
            act(() => listeners.forEach((listener) => listener()));
        },
    };
}

const renderPage = () =>
    render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>
                <WorkstationsPage />
            </MemoryRouter>
        </QueryClientProvider>
    );

const listed = (rows: WorkstationRecord[], dataUpdatedAt?: number) => ({
    data: rows,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
    dataUpdatedAt,
});

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
    jest.clearAllMocks();
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
        loading: false,
        can: () => true,
    });
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations());
    (queries.useWorkstations as jest.Mock).mockReturnValue(listed([activeRow, stoppedRow]));
    (queries.useEdhCatalogLease as jest.Mock).mockReturnValue({
        data: LEASE_ON,
        isLoading: false,
        error: null,
    });
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue({
        data: [],
        isLoading: false,
        error: null,
    });
});

afterEach(() => {
    window.matchMedia = originalMatchMedia;
    jest.useRealTimers();
});

describe("My Workstations below 640 px", () => {
    it("shows one card per workstation instead of the table", () => {
        viewport(WORKSTATION_TABLE_MIN_WIDTH_PX - 226);
        renderPage();

        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        const cards = within(screen.getByRole("list", { name: "Workstations" })).getAllByRole(
            "listitem"
        );
        expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual([
            "Dev box",
            "vams-isaac-sim-v4-1",
        ]);
        // Tailwind's preflight is off, so a card's border paints only with the module's outline class.
        cards.forEach((card) => expect(card).toHaveClass("orch-outline", "border"));
    });

    it("puts the name, status, image, instance type, remaining lease, Connect and actions on each card", async () => {
        const user = userEvent.setup();
        viewport(414);
        renderPage();

        const card = within(screen.getByRole("listitem", { name: "Dev box" }));
        expect(card.getByText("Dev box")).toBeInTheDocument();
        expect(card.getByText("Active")).toBeInTheDocument();
        expect(card.getByText("VAMS Isaac Sim v4")).toBeInTheDocument();
        // The registry number is not shown.
        expect(card.queryByText("42")).not.toBeInTheDocument();
        expect(card.getByText("g5.2xlarge")).toBeInTheDocument();
        expect(card.getByText("Remaining")).toBeInTheDocument();
        expect(card.getByText(/^in 3 h/)).toBeInTheDocument();
        expect(card.getByRole("button", { name: "Connect to Dev box" })).toBeInTheDocument();

        card.getByRole("button", { name: "Actions for Dev box" }).focus();
        await user.keyboard("{Enter}");
        const items = await screen.findAllByRole("menuitem");
        expect(items.map((item) => item.textContent)).toEqual(
            expect.arrayContaining([expect.stringContaining("Stop")])
        );
        expect(items[items.length - 1]).toHaveTextContent("Delete");
        await user.keyboard("{Escape}");

        const stopped = within(screen.getByRole("listitem", { name: "vams-isaac-sim-v4-1" }));
        expect(stopped.getByText("Stopped")).toBeInTheDocument();
    });

    it("leaves the Remaining line out while leases are off", () => {
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue({
            data: { ...LEASE_ON, enabled: false },
            isLoading: false,
            error: null,
        });
        viewport(414);
        renderPage();

        const card = within(screen.getByRole("listitem", { name: "Dev box" }));
        expect(card.queryByText("Remaining")).not.toBeInTheDocument();
    });

    it("switches between the cards and the table as the viewport crosses 640 px", () => {
        const screenSize = viewport(WORKSTATION_TABLE_MIN_WIDTH_PX);
        renderPage();
        expect(screen.getByRole("table", { name: "Workstations" })).toBeInTheDocument();
        expect(screen.queryByRole("list", { name: "Workstations" })).not.toBeInTheDocument();

        screenSize.resize(WORKSTATION_TABLE_MIN_WIDTH_PX - 1);
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        expect(screen.getByRole("list", { name: "Workstations" })).toBeInTheDocument();

        screenSize.resize(1600);
        expect(screen.getByRole("table", { name: "Workstations" })).toBeInTheDocument();
    });

    it("keeps the table where the browser answers no media query", () => {
        (window as any).matchMedia = undefined;
        renderPage();
        expect(screen.getByRole("table", { name: "Workstations" })).toBeInTheDocument();
    });
});

describe("the row actions button and the search box", () => {
    it("draws the row actions button as an icon in the table and on the cards", () => {
        const screenSize = viewport(1600);
        renderPage();

        const inTable = screen.getByRole("button", { name: "Actions for Dev box" });
        // An SVG icon, not a text glyph that renders blank without a font carrying it.
        expect(inTable.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
        expect(inTable.textContent).toBe("");

        screenSize.resize(414);
        const onCard = within(screen.getByRole("listitem", { name: "Dev box" })).getByRole(
            "button",
            { name: "Actions for Dev box" }
        );
        expect(onCard.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
        expect(onCard.textContent).toBe("");
    });

    it("says in the search box what it searches", () => {
        renderPage();
        expect(screen.getByRole("textbox", { name: "Search workstations" })).toHaveAttribute(
            "placeholder",
            "Search workstations"
        );
    });
});

describe("the toolbar's Updated caption", () => {
    it("says when the list was read and moves on as time passes", () => {
        jest.useFakeTimers();
        jest.setSystemTime(new Date("2026-11-03T12:00:00Z"));
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            listed([activeRow], new Date("2026-11-03T12:00:00Z").getTime())
        );
        renderPage();
        expect(screen.getByText("Updated just now")).toBeInTheDocument();

        act(() => {
            jest.advanceTimersByTime(30 * 1000);
        });
        expect(screen.getByText("Updated 30 seconds ago")).toBeInTheDocument();

        act(() => {
            jest.advanceTimersByTime(2 * 60 * 1000);
        });
        expect(screen.getByText("Updated 2 minutes ago")).toBeInTheDocument();
    });

    it("says nothing before the list has been read", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(listed([activeRow], 0));
        renderPage();
        expect(screen.queryByText(/^Updated/)).not.toBeInTheDocument();
    });
});

describe("the launch wizard's default name", () => {
    it("numbers past the names the list holds", async () => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue({
            data: [
                {
                    imageId: "90",
                    name: "VAMS Isaac Sim v4",
                    baseOs: "ubuntu2404",
                    osFamily: "linux",
                    architecture: "x86_64",
                    rootSize: 150,
                    description: null,
                    category: "amiRecipe",
                    recipeVersion: 4,
                },
            ],
            isLoading: false,
            error: null,
        });
        renderPage();

        fireEvent.click(screen.getByRole("button", { name: "Launch Workstation" }));
        fireEvent.click(await screen.findByRole("radio", { name: /VAMS Isaac Sim v4/ }));
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Workstation name")).toHaveValue("vams-isaac-sim-v4-2");
    });
});

describe("a search that matches no workstation", () => {
    const search = (text: string) =>
        fireEvent.change(screen.getByRole("textbox", { name: "Search workstations" }), {
            target: { value: text },
        });

    it("says so beneath the table's header row, and shows the rows again once cleared", () => {
        viewport(1600);
        renderPage();

        search("zz-no-such-row");
        expect(screen.getByRole("status")).toHaveTextContent(
            'No workstations match "zz-no-such-row".'
        );
        expect(screen.getByRole("table", { name: "Workstations" })).toBeInTheDocument();
        expect(screen.queryByText("Dev box")).toBeNull();

        search("");
        expect(screen.queryByRole("status")).toBeNull();
        expect(screen.getByText("Dev box")).toBeInTheDocument();
        expect(screen.getByText("vams-isaac-sim-v4-1")).toBeInTheDocument();
    });

    it("says so in place of the cards, and shows the cards again once cleared", () => {
        viewport(414);
        renderPage();

        search("zz-no-such-row");
        expect(screen.getByRole("status")).toHaveTextContent(
            'No workstations match "zz-no-such-row".'
        );
        expect(screen.queryByRole("list", { name: "Workstations" })).toBeNull();

        search("");
        expect(screen.queryByRole("status")).toBeNull();
        expect(
            within(screen.getByRole("list", { name: "Workstations" })).getAllByRole("listitem")
        ).toHaveLength(2);
    });
});
