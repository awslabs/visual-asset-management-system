/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage, {
    CATALOG_IMAGES_ROUTE,
    CATALOG_LEASE_ROUTE,
    WORKSTATIONS_ROUTE,
    WORKSTATION_CONNECT_ROUTE,
    WORKSTATION_RENEW_ROUTE,
    WORKSTATION_ROUTE,
    WORKSTATION_START_ROUTE,
    WORKSTATION_STOP_ROUTE,
    WORKSTATION_WINDOWS_SIGN_IN_ROUTE,
} from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import { LAST_ERROR_COPY } from "../components/WorkstationStatusBadge";
import type { CatalogLeasePolicy, WorkstationRecord } from "../types";

jest.mock("../api/queries", () => ({
    WORKSTATION_POLL_TRANSITIONAL_MS: 5000,
    WORKSTATION_POLL_STABLE_MS: 20000,
    useWorkstations: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useWorkstationConnect: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useWindowsSignIn: jest.fn(),
    useEdhCatalogImages: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhInstanceTypes: jest.fn(() => ({ data: [], isLoading: false, error: null })),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: {
        getItem: jest.fn(() => ({
            featuresEnabled: [],
        })),
    },
}));

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: false,
        },
    },
});

const activeRow: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Dev box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.xlarge",
    createdAt: "2026-09-08T10:00:00Z",
    updatedAt: "2026-09-08T10:05:00Z",
    connectionAvailable: true,
};

const stoppedRow: WorkstationRecord = {
    ...activeRow,
    bootstrapId: "b-2",
    sessionId: "s-2",
    name: "Render box",
    state: "STOPPED",
    edhState: "stopped",
    connectionAvailable: false,
};

const orphanedRow: WorkstationRecord = {
    ...activeRow,
    bootstrapId: "b-3",
    sessionId: null,
    name: "Old box",
    state: "ORPHANED",
    edhState: null,
    connectionAvailable: false,
    lastError: "QUOTA_EXCEEDED",
};

// A launch EDH's placement refused for capacity, with the AWS error VAMS captured from EDH.
const capacityFailedRow: WorkstationRecord = {
    ...orphanedRow,
    bootstrapId: "b-6",
    name: "Big box",
    lastError: "CAPACITY_UNAVAILABLE",
    edhFailure: {
        event: "placement_failed",
        errorCode: "InsufficientInstanceCapacity",
        operation: "CreateCapacityReservation",
        observedAt: "2026-10-22T10:00:00Z",
    },
};

// A desktop EDH placed whose setup then failed at a named checkpoint.
const setupFailedRow: WorkstationRecord = {
    ...orphanedRow,
    bootstrapId: "b-7",
    name: "Setup box",
    lastError: "LAUNCH_ERROR",
    edhFailure: {
        event: "session-failed",
        checkpoint: "dcv_session_create",
        observedAt: "2026-10-22T10:00:00Z",
    },
};

// A launch the worker is still on: no session yet, last written moments ago.
const creatingRow: WorkstationRecord = {
    ...activeRow,
    bootstrapId: "b-4",
    sessionId: null,
    name: "New box",
    state: "CREATING",
    edhState: null,
    connectionAvailable: false,
    updatedAt: new Date().toISOString(),
};

// A launch nothing has written to for longer than the page's 15-minute grace window.
const staleCreatingRow: WorkstationRecord = {
    ...creatingRow,
    bootstrapId: "b-5",
    name: "Stuck box",
    updatedAt: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
};

// A lease that runs out far beyond any test run, so its countdown reads the same shape every time.
const leasedRow: WorkstationRecord = {
    ...activeRow,
    leaseExpiresAt: "2100-01-01T00:00:00+00:00",
};

// A lease that ran out long ago, on a desktop the reconciler has stopped and the grace window has
// already passed for.
const expiredRow: WorkstationRecord = {
    ...stoppedRow,
    leaseExpiresAt: "2020-01-01T00:00:00+00:00",
    leaseStoppedAt: "2020-01-01T00:05:00+00:00",
    lastError: "LEASE_EXPIRED",
};

const LEASE_ON: CatalogLeasePolicy = {
    enabled: true,
    leaseHours: 8,
    forced: false,
    graceHours: 24,
};

const list = (rows: WorkstationRecord[]) => ({
    data: rows,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const mutations = () => ({
    createWorkstation: { mutateAsync: jest.fn(), isPending: false },
    stopWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    startWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    deleteWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    renewWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
});

const lease = (policy?: CatalogLeasePolicy) => ({ data: policy, isLoading: false, error: null });

// A request that stays in flight for the rest of the test.
const neverAnswers = () => new Promise<never>(() => undefined);

const allow = (can: (method: string, path: string) => boolean) =>
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({ loading: false, can });

// A fresh element per render: React bails out of re-rendering an element it is handed twice, so a
// re-render with the same object would never reach the board.
const page = () => (
    <QueryClientProvider client={queryClient}>
        <MemoryRouter>
            <WorkstationsPage />
        </MemoryRouter>
    </QueryClientProvider>
);

const renderPage = () => render(page());

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

async function openActionsFor(user: ReturnType<typeof userEvent.setup>, name: string) {
    // The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
    screen.getByLabelText(`Actions for ${name}`).focus();
    await user.keyboard("{Enter}");
    return screen.findAllByRole("menuitem");
}

// The Windows sign-in hook the page's dialog reads; re-seeded per test.
const signInHook = () => ({
    mutate: jest.fn(),
    reset: jest.fn(),
    data: undefined,
    error: null,
    isPending: true,
});
let signIn = signInHook();

describe("WorkstationsPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: ["EDH_WORKSTATIONS"] });
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([activeRow, stoppedRow]));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations());
        // Every hook the page reads is re-seeded here: clearAllMocks keeps a test's mockReturnValue,
        // so without this the tests would depend on their order.
        (queries.useWorkstationConnect as jest.Mock).mockReturnValue({
            mutateAsync: jest.fn(),
            isPending: false,
        });
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(catalog([]));
        (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(catalog([]));
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease());
        signIn = signInHook();
        (queries.useWindowsSignIn as jest.Mock).mockReturnValue(signIn);
        allow(() => true);
    });

    it("exposes the master-plan route templates verbatim for can()", () => {
        expect(WORKSTATIONS_ROUTE).toBe("/addon/edh/workstations");
        expect(WORKSTATION_ROUTE).toBe("/addon/edh/workstations/{sessionId}");
        expect(WORKSTATION_STOP_ROUTE).toBe("/addon/edh/workstations/{sessionId}/stop");
        expect(WORKSTATION_START_ROUTE).toBe("/addon/edh/workstations/{sessionId}/start");
        expect(WORKSTATION_CONNECT_ROUTE).toBe("/addon/edh/workstations/{sessionId}/connect");
        expect(WORKSTATION_RENEW_ROUTE).toBe("/addon/edh/workstations/{sessionId}/renew");
        expect(WORKSTATION_WINDOWS_SIGN_IN_ROUTE).toBe(
            "/addon/edh/workstations/{sessionId}/windows-sign-in"
        );
        expect(CATALOG_IMAGES_ROUTE).toBe("/addon/edh/catalog/images");
        expect(CATALOG_LEASE_ROUTE).toBe("/addon/edh/catalog/lease");
    });

    it("renders the caller's workstations with their status when the flag is on", () => {
        renderPage();

        expect(screen.getByRole("heading", { level: 1, name: "Workstations" })).toBeInTheDocument();
        expect(screen.getByRole("table", { name: "Workstations" })).toBeInTheDocument();
        expect(screen.getByText("Dev box")).toBeInTheDocument();
        expect(screen.getByText("Active")).toBeInTheDocument();
        expect(screen.getByText("Render box")).toBeInTheDocument();
        expect(screen.getByText("Stopped")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Launch Workstation" })).toBeInTheDocument();
    });

    it("renders the not-enabled panel and reads nothing when the flag is absent", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: [] });

        renderPage();

        const panel = screen.getByRole("status");
        expect(panel).toHaveTextContent("EDH Workstations is not enabled in this deployment.");
        expect(panel).toHaveTextContent("Ask a VAMS administrator to enable the add-on.");
        // The neutral tone of an unavailable view, not the warning tone of a data caveat.
        expect(panel).toHaveClass("bg-surface-container");
        expect(panel).not.toHaveClass("bg-yellow-100");
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Launch Workstation" })
        ).not.toBeInTheDocument();
        expect(queries.useWorkstations).not.toHaveBeenCalled();
    });

    it("shows an empty state with a call to action, not an empty table, when the caller has no workstations", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([]));
        const { unmount } = renderPage();
        expect(
            screen.getByText("You have no workstations yet. Launch one to get started.")
        ).toBeInTheDocument();
        // The primary action is repeated where the eye lands.
        expect(screen.getAllByRole("button", { name: "Launch Workstation" })).toHaveLength(2);
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        unmount();

        // Without the launch route the copy invites nothing the caller cannot do.
        allow((method, path) => !(method === "POST" && path === WORKSTATIONS_ROUTE));
        renderPage();
        expect(screen.getByText("You have no workstations yet.")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Launch Workstation" })).toBeNull();
    });

    it("labels the OS family, states the poll cadence and lets the list be searched", async () => {
        const user = userEvent.setup();
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([activeRow, { ...stoppedRow, osFamily: "windows" }])
        );
        renderPage();
        expect(screen.getByText("Linux")).toBeInTheDocument();
        expect(screen.getByText("Windows")).toBeInTheDocument();
        expect(screen.queryByText("linux")).not.toBeInTheDocument();
        expect(
            screen.getByText(
                "Refreshes every 20 seconds, and every 5 seconds while a workstation is being created or started."
            )
        ).toBeInTheDocument();

        // One search box, on the toolbar row as the admin tabs place theirs -- not the table's own.
        const search = screen.getByRole("textbox", { name: "Search workstations" });
        expect(screen.queryByRole("textbox", { name: "Search" })).not.toBeInTheDocument();
        expect(
            search.compareDocumentPosition(screen.getByRole("table")) &
                Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        await user.type(search, "Render");
        expect(screen.getByText("Render box")).toBeInTheDocument();
        expect(screen.queryByText("Dev box")).not.toBeInTheDocument();
    });

    it("shows an active workstation EDH does not yet report connectable as Starting", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...activeRow, connectionAvailable: false, edhState: "pending" }])
        );
        renderPage();
        expect(screen.getByText("Starting")).toBeInTheDocument();
        expect(screen.queryByText("Active")).not.toBeInTheDocument();
    });

    it("shows a stopped workstation being started again as Starting, not Creating", () => {
        // A start leaves the row CREATING with its session id still on it; only a launch has none.
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...stoppedRow, state: "CREATING", edhState: "pending" }])
        );
        renderPage();
        expect(screen.getByText("Starting")).toBeInTheDocument();
        expect(screen.queryByText("Creating")).not.toBeInTheDocument();
    });

    it("hides Connect when the connect route is not allowed, and renders it as a click-driven button when it is", () => {
        const connect = { mutateAsync: jest.fn(), isPending: false };
        (queries.useWorkstationConnect as jest.Mock).mockReturnValue(connect);
        allow((method, path) => !(method === "GET" && path === WORKSTATION_CONNECT_ROUTE));

        const { unmount } = renderPage();
        expect(screen.queryByRole("button", { name: /Connect to/ })).not.toBeInTheDocument();
        expect(screen.queryByRole("columnheader", { name: "Connect" })).not.toBeInTheDocument();
        unmount();

        // Positive control: the same rows with the route allowed do render the control — and
        // rendering it requests nothing; the URL is asked for on the click.
        allow(() => true);
        renderPage();
        expect(screen.getByRole("button", { name: "Connect to Dev box" })).toBeInTheDocument();
        expect(screen.queryByRole("link", { name: /Connect to/ })).not.toBeInTheDocument();
        expect(connect.mutateAsync).not.toHaveBeenCalled();
    });

    it("names the image a workstation runs from the catalog and keeps the id as secondary text", () => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(
            catalog([{ imageId: "42", name: "Ubuntu GPU desktop" }])
        );
        renderPage();

        expect(queries.useEdhCatalogImages).toHaveBeenCalledWith(true);
        // Both rows run image 42.
        expect(screen.getAllByText("Ubuntu GPU desktop")).toHaveLength(2);
        expect(screen.getAllByText("42")).toHaveLength(2);
    });

    it("falls back to the image id when the catalog no longer lists it", () => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(
            catalog([{ imageId: "7", name: "Windows CAD desktop" }])
        );
        renderPage();

        expect(screen.queryByText("Windows CAD desktop")).not.toBeInTheDocument();
        expect(screen.getAllByText("42")).toHaveLength(2);
    });

    it("reads no catalog when the caller may not, and shows the ids", () => {
        allow((method, path) => !(method === "GET" && path === CATALOG_IMAGES_ROUTE));
        renderPage();

        expect(queries.useEdhCatalogImages).toHaveBeenCalledWith(false);
        expect(screen.getAllByText("42")).toHaveLength(2);
    });

    it("reads no lease policy when the caller may not", () => {
        allow((method, path) => !(method === "GET" && path === CATALOG_LEASE_ROUTE));
        renderPage();

        expect(queries.useEdhCatalogLease).toHaveBeenCalledWith(false);
    });

    it("keeps an open row menu open across a poll tick", async () => {
        const user = userEvent.setup();
        const { rerender } = renderPage();

        await openActionsFor(user, "Dev box");
        expect(screen.getByRole("menu")).toBeInTheDocument();

        // A tick: the list re-reads (isFetching on, then off) and hands back fresh row objects
        // with the same identities.
        (queries.useWorkstations as jest.Mock).mockReturnValue({
            ...list([{ ...activeRow }, { ...stoppedRow }]),
            isFetching: true,
        });
        rerender(page());
        expect(screen.getByRole("menu")).toBeInTheDocument();

        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...activeRow }, { ...stoppedRow }])
        );
        rerender(page());
        expect(screen.getByRole("menu")).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: "Stop" })).toBeInTheDocument();
    });

    it("hides Launch Workstation when the caller may not POST the collection route", () => {
        allow((method, path) => !(method === "POST" && path === WORKSTATIONS_ROUTE));
        renderPage();
        expect(
            screen.queryByRole("button", { name: "Launch Workstation" })
        ).not.toBeInTheDocument();
        // The list itself is still shown; only the action is gated.
        expect(screen.getByText("Dev box")).toBeInTheDocument();
    });

    it("opens a confirm on Delete and only calls the mutation once confirmed", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        const items = await openActionsFor(user, "Dev box");
        await user.click(items.find((item) => item.textContent === "Delete")!);

        expect(screen.getByRole("dialog")).toHaveTextContent(
            /Are you sure you want to delete Dev box\?/
        );
        // A row with a session destroys its desktop.
        expect(screen.getByRole("dialog")).toHaveTextContent(
            "The workstation and its root disk are destroyed."
        );
        expect(m.deleteWorkstation.mutateAsync).not.toHaveBeenCalled();

        await user.click(screen.getByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(m.deleteWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "s-1" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("offers Stop to an active workstation and Start to a stopped one", async () => {
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Dev box");
        const stop = screen.getByRole("menuitem", { name: "Stop" });
        expect(stop).not.toHaveAttribute("aria-disabled", "true");
        expect(stop).not.toHaveAttribute("aria-describedby");
        // A disabled item says why, as visible text and as its description.
        const start = screen.getByRole("menuitem", { name: "Start" });
        expect(start).toHaveAttribute("aria-disabled", "true");
        expect(start).toHaveAccessibleDescription("Only a stopped workstation can be started.");
        expect(screen.getByText("Only a stopped workstation can be started.")).toBeVisible();
        await user.keyboard("{Escape}");

        await openActionsFor(user, "Render box");
        expect(screen.getByRole("menuitem", { name: "Start" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("menuitem", { name: "Stop" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("menuitem", { name: "Stop" })).toHaveAccessibleDescription(
            "Only an active workstation can be stopped."
        );
    });

    it("keeps Start disabled on a stopped workstation EDH still lists stopping, naming EDH's state", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...stoppedRow, edhState: "stopping" }])
        );
        renderPage();

        await openActionsFor(user, "Render box");
        const start = screen.getByRole("menuitem", { name: "Start" });
        expect(start).toHaveAttribute("aria-disabled", "true");
        expect(start).toHaveAccessibleDescription(
            "The workstation is still stopping; it can be started once it has stopped."
        );
        await user.click(start);
        expect(m.startWorkstation.mutateAsync).not.toHaveBeenCalled();
    });

    it.each([["stopped"], [null], [""]])(
        "offers Start to a stopped workstation EDH lists as %p",
        async (edhState) => {
            const user = userEvent.setup();
            (queries.useWorkstations as jest.Mock).mockReturnValue(
                list([{ ...stoppedRow, edhState }])
            );
            renderPage();

            await openActionsFor(user, "Render box");
            const start = screen.getByRole("menuitem", { name: "Start" });
            expect(start).not.toHaveAttribute("aria-disabled", "true");
            expect(start).not.toHaveAttribute("aria-describedby");
        }
    );

    it("disables Start on a workstation while its start is in flight, so a second Start sends nothing", async () => {
        const user = userEvent.setup();
        const m = mutations();
        let answer: (record: WorkstationRecord) => void = () => undefined;
        m.startWorkstation.mutateAsync = jest.fn(
            () =>
                new Promise<WorkstationRecord>((resolve) => {
                    answer = resolve;
                })
        );
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        await openActionsFor(user, "Render box");
        await user.click(screen.getByRole("menuitem", { name: "Start" }));
        await openActionsFor(user, "Render box");
        const start = screen.getByRole("menuitem", { name: "Start" });
        expect(start).toHaveAttribute("aria-disabled", "true");
        expect(start).toHaveAccessibleDescription("A start of this workstation is in progress.");
        await user.click(start);
        expect(m.startWorkstation.mutateAsync).toHaveBeenCalledTimes(1);
        // Delete waits too: a delete now would remove the desktop while the start binds its key.
        const del = screen.getByRole("menuitem", { name: "Delete" });
        expect(del).toHaveAttribute("aria-disabled", "true");
        expect(del).toHaveAccessibleDescription("A start of this workstation is in progress.");
        await user.click(del);
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        await user.keyboard("{Escape}");

        // The guard holds for that workstation only.
        await openActionsFor(user, "Dev box");
        expect(screen.getByRole("menuitem", { name: "Stop" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("menuitem", { name: "Delete" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.keyboard("{Escape}");

        await act(async () => answer(stoppedRow));
        await openActionsFor(user, "Render box");
        expect(screen.getByRole("menuitem", { name: "Start" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("menuitem", { name: "Delete" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("disables Stop on a workstation while its stop is in flight, and offers it again once the stop fails", async () => {
        const user = userEvent.setup();
        const m = mutations();
        let refuse: (err: Error) => void = () => undefined;
        m.stopWorkstation.mutateAsync = jest.fn(
            () =>
                new Promise<WorkstationRecord>((_resolve, reject) => {
                    refuse = reject;
                })
        );
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        await openActionsFor(user, "Dev box");
        await user.click(screen.getByRole("menuitem", { name: "Stop" }));
        await openActionsFor(user, "Dev box");
        const stop = screen.getByRole("menuitem", { name: "Stop" });
        expect(stop).toHaveAttribute("aria-disabled", "true");
        expect(stop).toHaveAccessibleDescription("A stop of this workstation is in progress.");
        await user.click(stop);
        expect(m.stopWorkstation.mutateAsync).toHaveBeenCalledTimes(1);
        const del = screen.getByRole("menuitem", { name: "Delete" });
        expect(del).toHaveAttribute("aria-disabled", "true");
        expect(del).toHaveAccessibleDescription("A stop of this workstation is in progress.");
        await user.keyboard("{Escape}");

        await act(async () => refuse(new Error("Forbidden")));
        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent("Failed to stop Dev box: Forbidden")
        );
        await openActionsFor(user, "Dev box");
        expect(screen.getByRole("menuitem", { name: "Stop" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("disables Stop and Delete on a workstation while its delete is in flight, with the dialog dismissed", async () => {
        const user = userEvent.setup();
        const m = mutations();
        let answer: (record: WorkstationRecord) => void = () => undefined;
        m.deleteWorkstation.mutateAsync = jest.fn(
            () =>
                new Promise<WorkstationRecord>((resolve) => {
                    answer = resolve;
                })
        );
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        await openActionsFor(user, "Dev box");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(screen.getByRole("button", { name: "Delete" }));
        // A second confirm while the first delete is out sends nothing.
        await user.click(screen.getByRole("button", { name: "Delete" }));
        expect(m.deleteWorkstation.mutateAsync).toHaveBeenCalledTimes(1);
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        await openActionsFor(user, "Dev box");
        const stop = screen.getByRole("menuitem", { name: "Stop" });
        expect(stop).toHaveAttribute("aria-disabled", "true");
        expect(stop).toHaveAccessibleDescription("A delete of this workstation is in progress.");
        const del = screen.getByRole("menuitem", { name: "Delete" });
        expect(del).toHaveAttribute("aria-disabled", "true");
        expect(del).toHaveAccessibleDescription("A delete of this workstation is in progress.");
        await user.keyboard("{Escape}");

        await act(async () => answer(activeRow));
        await openActionsFor(user, "Dev box");
        expect(screen.getByRole("menuitem", { name: "Stop" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("sends one delete for a double-click on the confirmation", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.deleteWorkstation.mutateAsync = jest.fn(neverAnswers);
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        await openActionsFor(user, "Dev box");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        // Both clicks land before a re-render, as a double-click's do.
        const confirm = screen.getByRole("button", { name: "Delete" });
        fireEvent.click(confirm);
        fireEvent.click(confirm);
        await waitFor(() =>
            expect(m.deleteWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "s-1" })
        );
        expect(m.deleteWorkstation.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("hides Stop, Start and Delete when their routes are not allowed", () => {
        allow((method, path) => method === "GET" || path === WORKSTATIONS_ROUTE);
        renderPage();

        // With every row action gated off, the actions cell carries a placeholder, not a menu.
        expect(screen.queryByLabelText("Actions for Dev box")).not.toBeInTheDocument();
        expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });

    it("reports a failed stop as a banner and keeps the row", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.stopWorkstation.mutateAsync = jest.fn().mockRejectedValue(new Error("Forbidden"));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        renderPage();

        const items = await openActionsFor(user, "Dev box");
        await user.click(items.find((item) => item.textContent === "Stop")!);

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent("Failed to stop Dev box: Forbidden")
        );
        expect(screen.getByText("Dev box")).toBeInTheDocument();
    });

    it("reports what a lease has left and offers Renew lease while the policy applies", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([leasedRow]));
        renderPage();

        expect(queries.useEdhCatalogLease).toHaveBeenCalledWith(true);
        expect(screen.getByRole("columnheader", { name: "Remaining" })).toBeInTheDocument();
        // The countdown names its units, and the cell carries the expiry itself.
        const remaining = screen.getByText(/^in \d+ d/);
        expect(remaining).toHaveAttribute(
            "title",
            new Date("2100-01-01T00:00:00+00:00").toLocaleString()
        );

        await openActionsFor(user, "Dev box");
        const renew = screen.getByRole("menuitem", { name: "Renew lease" });
        expect(renew).not.toHaveAttribute("aria-disabled", "true");
        // The action extends to the policy's window, not to the one the launch was stamped with.
        expect(renew).toHaveAccessibleDescription("Extends the lease to 8 hours from now");
    });

    it("shows a placeholder and no Renew for a workstation no lease covers", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([activeRow]));
        renderPage();

        expect(screen.getByRole("columnheader", { name: "Remaining" })).toBeInTheDocument();
        expect(screen.getByText("—")).toBeInTheDocument();

        const items = await openActionsFor(user, "Dev box");
        expect(items.length).toBeGreaterThan(0);
        expect(screen.queryByRole("menuitem", { name: "Renew lease" })).not.toBeInTheDocument();
    });

    it("reports nothing about leases when the policy is off or has not answered yet", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(
            lease({ ...LEASE_ON, enabled: false })
        );
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([leasedRow]));
        const { unmount } = renderPage();

        expect(screen.queryByRole("columnheader", { name: "Remaining" })).not.toBeInTheDocument();
        expect(screen.queryByText(/^in \d/)).not.toBeInTheDocument();
        await openActionsFor(user, "Dev box");
        expect(screen.queryByRole("menuitem", { name: "Renew lease" })).not.toBeInTheDocument();
        unmount();

        // An unanswered policy reads as no policy: the same row carries nothing lease-related.
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease());
        renderPage();
        expect(screen.queryByRole("columnheader", { name: "Remaining" })).not.toBeInTheDocument();
        expect(screen.queryByText(/^in \d/)).not.toBeInTheDocument();
    });

    it("hides Renew when the caller may not POST the renew route", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([leasedRow]));
        allow((method, path) => !(method === "POST" && path === WORKSTATION_RENEW_ROUTE));
        renderPage();

        // The lease is still reported; only the action is gated.
        expect(screen.getByRole("columnheader", { name: "Remaining" })).toBeInTheDocument();
        await openActionsFor(user, "Dev box");
        expect(screen.queryByRole("menuitem", { name: "Renew lease" })).not.toBeInTheDocument();
    });

    it("renews a lease straight from the row menu", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([leasedRow]));
        renderPage();

        await openActionsFor(user, "Dev box");
        await user.click(screen.getByRole("menuitem", { name: "Renew lease" }));

        await waitFor(() =>
            expect(m.renewWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "s-1" })
        );
        // Extending a lease asks nothing of the user, as stopping and starting do not.
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("reports a failed renew as a banner and keeps the row", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.renewWorkstation.mutateAsync = jest
            .fn()
            .mockRejectedValue(new Error("Workstation leases are disabled"));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([leasedRow]));
        renderPage();

        await openActionsFor(user, "Dev box");
        await user.click(screen.getByRole("menuitem", { name: "Renew lease" }));

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent(
                "Failed to renew Dev box: Workstation leases are disabled"
            )
        );
        expect(screen.getByText("Dev box")).toBeInTheDocument();
    });

    it("reads an expired lease as its next step and says why the desktop was stopped", () => {
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([expiredRow]));
        renderPage();

        expect(screen.getByText("Expired, removing")).toBeInTheDocument();
        expect(screen.getByText(LAST_ERROR_COPY.LEASE_EXPIRED)).toBeInTheDocument();
        expect(screen.getByTitle(LAST_ERROR_COPY.LEASE_EXPIRED)).toHaveTextContent("Stopped");
    });

    it("shows the AWS error EDH reported beneath the reason, and nothing for a row without one", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([
                capacityFailedRow,
                setupFailedRow,
                { ...orphanedRow, edhFailure: null },
                activeRow,
            ])
        );
        renderPage();

        // The reason stays, with the captured detail as its own line of text beneath it.
        const reason = screen.getByText(LAST_ERROR_COPY.CAPACITY_UNAVAILABLE);
        const detail = screen.getByText(
            "AWS: InsufficientInstanceCapacity (CreateCapacityReservation)"
        );
        expect(reason.parentElement).toContainElement(detail);
        expect(screen.getByText("Desktop setup failed at dcv_session_create")).toBeInTheDocument();
        // The row without a captured failure shows its reason and no detail line.
        expect(screen.getByText(LAST_ERROR_COPY.QUOTA_EXCEEDED)).toBeInTheDocument();
        expect(screen.getAllByText(/^AWS: /)).toHaveLength(1);
        expect(screen.getAllByText(/^Desktop setup failed at /)).toHaveLength(1);
    });

    it("shows no failure detail when no row carries one", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([orphanedRow, activeRow]));
        renderPage();

        expect(screen.getByText(LAST_ERROR_COPY.QUOTA_EXCEEDED)).toBeInTheDocument();
        expect(screen.queryByText(/^AWS: /)).not.toBeInTheDocument();
        expect(screen.queryByText(/^Desktop setup failed at /)).not.toBeInTheDocument();
    });

    it("shows why a launch was orphaned, offers only Delete, and deletes it by its bootstrap id", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([orphanedRow]));
        renderPage();

        expect(screen.getByText("Orphaned")).toBeInTheDocument();
        expect(screen.getByText(LAST_ERROR_COPY.QUOTA_EXCEEDED)).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Connect to Old box" })
        ).not.toBeInTheDocument();

        // Stop and Start address a session the row never received, so the menu carries neither.
        const items = await openActionsFor(user, "Old box");
        expect(items.map((item) => item.textContent)).toEqual(["Delete"]);
        expect(screen.getByRole("menuitem", { name: "Delete" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.click(items[0]);

        expect(screen.getByRole("dialog")).toHaveTextContent(
            /Are you sure you want to delete Old box\?/
        );
        // A row without a session has no desktop to destroy; only the launch record goes.
        expect(screen.getByRole("dialog")).toHaveTextContent("the launch record is removed");
        await user.click(screen.getByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(m.deleteWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "b-3" })
        );
    });

    it("renders a creating launch with Delete disabled and explained, no Stop or Start, and an inert Connect", async () => {
        const user = userEvent.setup();
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([creatingRow]));
        renderPage();

        expect(screen.getByText("Creating")).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Connect to New box" })
        ).not.toBeInTheDocument();
        // The inert Connect control names its reason.
        expect(screen.getByRole("button", { name: "Connect" })).toHaveAccessibleDescription(
            "Available once the workstation is running."
        );

        const items = await openActionsFor(user, "New box");
        expect(items).toHaveLength(1);
        const remove = screen.getByRole("menuitem", { name: "Delete" });
        expect(remove).toHaveAttribute("aria-disabled", "true");
        expect(remove).toHaveAccessibleDescription(
            "Available once the launch has been creating for 15 minutes."
        );
        expect(
            screen.getByText("Available once the launch has been creating for 15 minutes.")
        ).toBeVisible();
    });

    it("offers Delete for a creating launch whose last update is older than 15 minutes", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([staleCreatingRow]));
        renderPage();

        const items = await openActionsFor(user, "Stuck box");
        expect(items.map((item) => item.textContent)).toEqual(["Delete"]);
        expect(screen.getByRole("menuitem", { name: "Delete" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.click(items[0]);
        await user.click(screen.getByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(m.deleteWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "b-5" })
        );
    });

    it("reports a delete the service refuses with its message and keeps the row", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.deleteWorkstation.mutateAsync = jest
            .fn()
            .mockRejectedValue(new Error("Workstation is still being created"));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([staleCreatingRow]));
        renderPage();

        const items = await openActionsFor(user, "Stuck box");
        await user.click(items.find((item) => item.textContent === "Delete")!);
        await user.click(screen.getByRole("button", { name: "Delete" }));

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent(
                "Failed to delete Stuck box: Workstation is still being created"
            )
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(screen.getByText("Stuck box")).toBeInTheDocument();
    });

    describe("Show Windows password", () => {
        const windowsRow: WorkstationRecord = {
            ...activeRow,
            bootstrapId: "b-6",
            sessionId: "s-6",
            name: "Win box",
            imageId: "43",
            osFamily: "windows",
        };
        const secondWindowsRow: WorkstationRecord = {
            ...windowsRow,
            bootstrapId: "b-7",
            sessionId: "s-7",
            name: "Win box 2",
        };
        const stoppedWindowsRow: WorkstationRecord = {
            ...windowsRow,
            bootstrapId: "b-8",
            sessionId: "s-8",
            name: "Win stopped",
            state: "STOPPED",
            edhState: "stopped",
            connectionAvailable: false,
        };
        const showItem = () => screen.queryByRole("menuitem", { name: "Show Windows password" });

        it("is not offered on a Linux workstation", async () => {
            const user = userEvent.setup();
            (queries.useWorkstations as jest.Mock).mockReturnValue(list([activeRow]));
            renderPage();

            await openActionsFor(user, "Dev box");

            expect(showItem()).not.toBeInTheDocument();
        });

        it("opens the sign-in dialog of an active Windows workstation", async () => {
            const user = userEvent.setup();
            (queries.useWorkstations as jest.Mock).mockReturnValue(list([windowsRow]));
            renderPage();

            await openActionsFor(user, "Win box");
            const item = showItem() as HTMLElement;
            expect(item).toBeInTheDocument();
            expect(item).not.toHaveAttribute("aria-disabled", "true");
            await user.click(item);

            expect(
                await screen.findByRole("dialog", { name: "Windows sign-in for Win box" })
            ).toBeInTheDocument();
            expect(signIn.mutate).toHaveBeenCalledWith("s-6", expect.anything());
        });

        it("is disabled, with the reason, while the workstation cannot be connected to", async () => {
            const user = userEvent.setup();
            (queries.useWorkstations as jest.Mock).mockReturnValue(list([stoppedWindowsRow]));
            renderPage();

            await openActionsFor(user, "Win stopped");
            const item = showItem() as HTMLElement;

            expect(item).toBeInTheDocument();
            expect(item).toHaveAttribute("aria-disabled", "true");
            expect(item).toHaveAccessibleDescription("Start the workstation to connect.");
        });

        it("is hidden from a caller who may not read the sign-in", async () => {
            const user = userEvent.setup();
            const signInRoute = (method: string, path: string) =>
                method === "GET" && path === WORKSTATION_WINDOWS_SIGN_IN_ROUTE;
            allow((method, path) => !signInRoute(method, path));
            (queries.useWorkstations as jest.Mock).mockReturnValue(list([windowsRow]));
            renderPage();

            await openActionsFor(user, "Win box");

            expect(showItem()).not.toBeInTheDocument();
        });

        it("drops one workstation's sign-in before showing another's", async () => {
            const user = userEvent.setup();
            (queries.useWorkstations as jest.Mock).mockReturnValue(
                list([windowsRow, secondWindowsRow])
            );
            renderPage();

            await openActionsFor(user, "Win box");
            await user.click(showItem() as HTMLElement);
            await screen.findByRole("dialog", { name: "Windows sign-in for Win box" });
            await user.click(screen.getByRole("button", { name: "Close" }));
            await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
            expect(signIn.reset).toHaveBeenCalledTimes(1);

            await openActionsFor(user, "Win box 2");
            await user.click(showItem() as HTMLElement);

            expect(
                await screen.findByRole("dialog", { name: "Windows sign-in for Win box 2" })
            ).toBeInTheDocument();
            expect(signIn.mutate).toHaveBeenLastCalledWith("s-7", expect.anything());
        });
    });
});
