/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The layout of the admin Sessions tab: below 640 px each session is a card carrying its name,
 * status, owner, instance type, created time, session id and actions, as My Workstations does,
 * instead of a table that scrolls them off-screen; at 640 px and wider the table stays. Search, the
 * View filter, the count and paging work the same in both. The row actions button is an icon, and
 * the search box says what it searches.
 */

import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InstancesTab from "./InstancesTab";
import { WORKSTATION_TABLE_MIN_WIDTH_PX } from "../components/RecordCards";
import * as adminQueries from "../api/adminQueries";
import type { EdhAdminInstance } from "../api/admin";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminInstances: jest.fn(),
    useEdhStaleInstances: jest.fn(),
    useEdhAdminMutations: jest.fn(),
}));

const SESSION_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0";
const CREATED_AT = "2026-10-23T10:00:00Z";

const session = (overrides: Partial<EdhAdminInstance>): EdhAdminInstance => ({
    sessionId: SESSION_ID,
    edhState: "running",
    owner: "vabcdefghij23456",
    name: "cad-1",
    createdAt: CREATED_AT,
    updatedAt: "2026-10-23T10:05:00Z",
    userId: "user@example.com",
    bootstrapId: "b-1",
    state: "ACTIVE",
    lastError: null,
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.2xlarge",
    ...overrides,
});

const RESERVED = session({
    capacityReservationId: "cr-0123456789abcdef0",
    capacityReservationLabel: "Render pool",
    capacityReservationFallback: false,
});
const STOPPED = session({
    sessionId: "s-2",
    bootstrapId: "b-2",
    name: "cad-2",
    state: "STOPPED",
    edhState: "stopped",
    userId: "other@example.com",
});

const query = (data: EdhAdminInstance[]) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

type Listener = () => void;

/** A viewport `width` px wide, answering min-width media queries and announcing a resize. */
function viewport(width: number) {
    const listeners = new Set<Listener>();
    const state = { width };
    window.matchMedia = jest.fn((mediaQuery: string) => {
        const min = Number(/min-width:\s*(\d+)px/.exec(mediaQuery)?.[1] ?? 0);
        return {
            get matches() {
                return state.width >= min;
            },
            media: mediaQuery,
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

const cards = () =>
    within(screen.getByRole("list", { name: "EDH sessions" })).getAllByRole("listitem");
const cardNames = () => cards().map((card) => card.getAttribute("aria-label"));

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
    jest.clearAllMocks();
    (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(query([RESERVED, STOPPED]));
    (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query([]));
    (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue({
        stopInstance: { mutateAsync: jest.fn(), isPending: false },
        deactivateInstance: { mutateAsync: jest.fn(), isPending: false },
    });
});

afterEach(() => {
    window.matchMedia = originalMatchMedia;
});

describe("the Sessions tab below 640 px", () => {
    it("shows one outlined card per session instead of the table", () => {
        viewport(WORKSTATION_TABLE_MIN_WIDTH_PX - 226);
        render(<InstancesTab can={() => true} />);

        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        expect(cardNames()).toEqual(["cad-1", "cad-2"]);
        // Tailwind's preflight is off, so a card's border paints only with the module's outline class.
        cards().forEach((card) => expect(card).toHaveClass("orch-outline", "border"));
    });

    it("puts the name, status, owner, instance type, capacity, created time, session id and actions on each card", async () => {
        const user = userEvent.setup();
        viewport(414);
        render(<InstancesTab can={() => true} />);

        const card = within(screen.getByRole("listitem", { name: "cad-1" }));
        expect(card.getByText("cad-1")).toBeInTheDocument();
        expect(card.getByText("Active")).toBeInTheDocument();
        expect(card.getByText("EDH state: Running")).toBeInTheDocument();
        expect(card.getByText("Owner")).toBeInTheDocument();
        expect(card.getByText("user@example.com")).toHaveAttribute(
            "title",
            "EDH user id: vabcdefghij23456"
        );
        expect(
            card.getByRole("button", { name: "Copy EDH user id: vabcdefghij23456" })
        ).toBeInTheDocument();
        expect(card.getByText("g5.2xlarge")).toBeInTheDocument();
        expect(card.getByText("Capacity: Render pool (cr-0123456789abcdef0)")).toBeInTheDocument();
        expect(card.getByText("Created")).toBeInTheDocument();
        expect(card.getByText(new Date(CREATED_AT).toLocaleString())).toBeInTheDocument();
        expect(card.getByText("0f1e2d3c…")).toBeInTheDocument();
        expect(
            card.getByRole("button", { name: `Copy Session id: ${SESSION_ID}` })
        ).toBeInTheDocument();
        // The stale view's reason is not on a card of the full list.
        expect(card.queryByText("Why stale")).not.toBeInTheDocument();

        const actions = card.getByRole("button", { name: "Actions for cad-1" });
        actions.focus();
        await user.keyboard("{Enter}");
        const items = await screen.findAllByRole("menuitem");
        expect(items.map((item) => item.textContent)).toEqual(["Stop", "Deactivate"]);
        await user.keyboard("{Escape}");

        // A session launched on demand has no capacity line.
        const stopped = within(screen.getByRole("listitem", { name: "cad-2" }));
        expect(stopped.getByText("Stopped")).toBeInTheDocument();
        expect(stopped.getByText("EDH state: Stopped")).toBeInTheDocument();
        expect(stopped.queryByText(/^Capacity:/)).not.toBeInTheDocument();
    });

    it("switches between the cards and the table as the viewport crosses 640 px", () => {
        const screenSize = viewport(WORKSTATION_TABLE_MIN_WIDTH_PX);
        render(<InstancesTab can={() => true} />);
        expect(screen.getByRole("table", { name: "EDH sessions" })).toBeInTheDocument();
        expect(screen.queryByRole("list", { name: "EDH sessions" })).not.toBeInTheDocument();

        screenSize.resize(WORKSTATION_TABLE_MIN_WIDTH_PX - 1);
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        expect(screen.getByRole("list", { name: "EDH sessions" })).toBeInTheDocument();

        screenSize.resize(1600);
        expect(screen.getByRole("table", { name: "EDH sessions" })).toBeInTheDocument();
    });

    it("searches the cards from the toolbar and counts the sessions it shows of every session", () => {
        viewport(414);
        render(<InstancesTab can={() => true} />);
        expect(screen.getByText("2 sessions")).toBeInTheDocument();

        fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), {
            target: { value: "other@example.com" },
        });
        expect(cardNames()).toEqual(["cad-2"]);
        expect(screen.getByText("1 of 2 sessions")).toBeInTheDocument();
    });

    it("shows why a session is stale on its card in the Stale sessions view", () => {
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(
            query([
                { ...STOPPED, staleReason: "stopped" },
                session({
                    sessionId: "s-3",
                    bootstrapId: "b-3",
                    name: "cad-3",
                    edhState: null,
                    staleReason: "missingInEdh",
                }),
            ])
        );
        viewport(414);
        render(<InstancesTab can={() => true} />);

        fireEvent.change(screen.getByRole("combobox", { name: "Session view" }), {
            target: { value: "stale" },
        });
        const list = within(screen.getByRole("list", { name: "Stale EDH sessions" }));
        const stopped = within(list.getByRole("listitem", { name: "cad-2" }));
        expect(stopped.getByText("Why stale")).toBeInTheDocument();
        expect(stopped.getByText("Stopped in EDH")).toBeInTheDocument();
        const missing = within(list.getByRole("listitem", { name: "cad-3" }));
        expect(missing.getByText("Not listed by EDH")).toBeInTheDocument();
    });

    it("pages the cards ten at a time, as the table pages its rows, and starts a new search on the first page", () => {
        const many = Array.from({ length: 12 }, (_, i) =>
            session({
                sessionId: `s-${i + 1}`,
                bootstrapId: `b-${i + 1}`,
                name: `cad-${String(i + 1).padStart(2, "0")}`,
            })
        );
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(query(many));
        viewport(414);
        render(<InstancesTab can={() => true} />);

        expect(cards()).toHaveLength(10);
        expect(cardNames()[0]).toBe("cad-01");
        expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        expect(cardNames()).toEqual(["cad-11", "cad-12"]);
        expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

        fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), {
            target: { value: "cad" },
        });
        expect(cardNames()[0]).toBe("cad-01");
        expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    });
});

describe("the Sessions tab's row actions button and search box", () => {
    it("draws the row actions button as an icon in the table and on the cards", () => {
        const screenSize = viewport(1600);
        render(<InstancesTab can={() => true} />);

        const inTable = screen.getByRole("button", { name: "Actions for cad-1" });
        // An SVG icon, not a text glyph that renders blank without a font carrying it.
        expect(inTable.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
        expect(inTable.textContent).toBe("");

        screenSize.resize(414);
        const onCard = within(screen.getByRole("listitem", { name: "cad-1" })).getByRole("button", {
            name: "Actions for cad-1",
        });
        expect(onCard.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
        expect(onCard.textContent).toBe("");
    });

    it("says in the search box what it searches", () => {
        render(<InstancesTab can={() => true} />);
        expect(screen.getByRole("textbox", { name: "Search sessions" })).toHaveAttribute(
            "placeholder",
            "Search sessions"
        );
    });
});

describe("a search that matches no session", () => {
    const search = (text: string) =>
        fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), {
            target: { value: text },
        });

    it("says so beneath the table's header row, and shows the rows again once cleared", () => {
        viewport(1600);
        render(<InstancesTab can={() => true} />);

        search("zz-no-such-row");
        expect(screen.getByRole("status")).toHaveTextContent('No sessions match "zz-no-such-row".');
        expect(screen.getByRole("table", { name: "EDH sessions" })).toBeInTheDocument();
        expect(screen.queryByText("cad-1")).toBeNull();
        // The count says how many of the sessions the search shows.
        expect(screen.getByText("0 of 2 sessions")).toBeInTheDocument();

        search("");
        expect(screen.queryByRole("status")).toBeNull();
        expect(screen.getByText("cad-1")).toBeInTheDocument();
        expect(screen.getByText("cad-2")).toBeInTheDocument();
        expect(screen.getByText("2 sessions")).toBeInTheDocument();
    });

    it("says so in place of the cards, and shows the cards again once cleared", () => {
        viewport(414);
        render(<InstancesTab can={() => true} />);

        search("zz-no-such-row");
        expect(screen.getByRole("status")).toHaveTextContent('No sessions match "zz-no-such-row".');
        expect(screen.queryByRole("list", { name: "EDH sessions" })).toBeNull();

        search("");
        expect(screen.queryByRole("status")).toBeNull();
        expect(cardNames()).toEqual(["cad-1", "cad-2"]);
    });
});
