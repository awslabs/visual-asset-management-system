/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The start status on My Workstations: the badge follows `activity`, the actions wait while a start
 * runs, a start the service accepted (202) is reported as requested and left to the list, and a start
 * EDH refused leaves the row stopped with the reason and the AWS detail beneath its badge.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import { EdhRequestError, RESERVATION_FULL_START_MESSAGE } from "../api/outcome";
import { LAST_ERROR_COPY } from "../components/WorkstationStatusBadge";
import type { WorkstationRecord } from "../types";

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

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

const START_IN_PROGRESS_HINT = "A start of this workstation is in progress.";

const stoppedRow: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "STOPPED",
    edhState: "stopped",
    name: "Render box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.16xlarge",
    createdAt: "2026-10-23T10:00:00Z",
    updatedAt: "2026-10-23T15:19:13Z",
    connectionAvailable: false,
    activity: null,
    lastError: null,
    startFailure: null,
};

const CAPACITY_SENTENCE =
    "AWS had no capacity for g5.16xlarge in this workstation's Availability Zone (us-west-2b) when it was started. A stopped workstation keeps its disk and starts only in that zone, so it stays stopped; start it again later.";

const capacityRefusedRow: WorkstationRecord = {
    ...stoppedRow,
    lastError: "CAPACITY_UNAVAILABLE",
    startFailure: {
        errorCode: "InsufficientInstanceCapacity",
        availabilityZone: "us-west-2b",
        observedAt: "2026-10-23T15:19:30Z",
    },
};

const STALLED_SENTENCE =
    "EDH has not finished starting this workstation after 30 minutes. Stop it, then start it again; if it stalls again, ask your administrator.";

// A start EDH accepted and kept `pending` for 30 minutes: the sweep recorded the stall on the row,
// which stays CREATING (EDH may still finish), and the service reports no activity for it.
const stalledRow: WorkstationRecord = {
    ...stoppedRow,
    state: "CREATING",
    edhState: "pending",
    activity: null,
    lastError: "START_STALLED",
    startFailure: { availabilityZone: "us-west-2b", observedAt: "2026-10-24T11:40:00Z" },
};

const list = (rows: WorkstationRecord[]) => ({
    data: rows,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const mutations = (start = jest.fn().mockResolvedValue({})) => ({
    createWorkstation: { mutateAsync: jest.fn(), isPending: false },
    stopWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    startWorkstation: { mutateAsync: start, isPending: false },
    deleteWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    renewWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
});

const renderPage = () =>
    render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>
                <WorkstationsPage />
            </MemoryRouter>
        </QueryClientProvider>
    );

async function openActionsFor(user: ReturnType<typeof userEvent.setup>, name: string) {
    screen.getByLabelText(`Actions for ${name}`).focus();
    await user.keyboard("{Enter}");
    return screen.findAllByRole("menuitem");
}

const statusCell = (name: string) => {
    const row = screen.getByText(name).closest("tr") as HTMLElement;
    return within(row);
};

describe("WorkstationsPage start status", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useWindowsSignIn as jest.Mock).mockReturnValue({
            mutate: jest.fn(),
            reset: jest.fn(),
            data: undefined,
            error: null,
            isPending: false,
        });
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations());
    });

    it.each<[string, Partial<WorkstationRecord>]>([
        ["a stopped row whose start is in progress", { activity: "starting" }],
        [
            "a creating row EDH has accepted the start of",
            { state: "CREATING", edhState: "pending", activity: "starting" },
        ],
        [
            "an active row that is not connectable yet",
            { state: "ACTIVE", edhState: "pending", activity: "starting" },
        ],
    ])(
        "%s reads Starting and keeps Start, Stop and Delete disabled with the hint",
        async (_label, overrides) => {
            const m = mutations();
            (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
            (queries.useWorkstations as jest.Mock).mockReturnValue(
                list([{ ...stoppedRow, ...overrides }])
            );
            const user = userEvent.setup();
            renderPage();

            expect(statusCell("Render box").getByText("Starting")).toBeInTheDocument();
            await openActionsFor(user, "Render box");
            for (const label of ["Start", "Stop", "Delete"]) {
                const item = screen.getByRole("menuitem", { name: label });
                expect(item).toHaveAttribute("aria-disabled", "true");
                expect(item).toHaveAccessibleDescription(START_IN_PROGRESS_HINT);
                await user.click(item);
            }
            expect(m.startWorkstation.mutateAsync).not.toHaveBeenCalled();
            expect(m.stopWorkstation.mutateAsync).not.toHaveBeenCalled();
            expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        }
    );

    it("reads Stopping while EDH stops the desktop, and offers no start until it has stopped", async () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...stoppedRow, edhState: "stopping", activity: "stopping" }])
        );
        const user = userEvent.setup();
        renderPage();

        expect(statusCell("Render box").getByText("Stopping")).toBeInTheDocument();
        expect(statusCell("Render box").queryByText("Stopped")).not.toBeInTheDocument();
        await openActionsFor(user, "Render box");
        expect(screen.getByRole("menuitem", { name: "Start" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("offers Start again on a stopped row whose start EDH refused", async () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([capacityRefusedRow]));
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Render box");
        const start = screen.getByRole("menuitem", { name: "Start" });
        expect(start).not.toHaveAttribute("aria-disabled", "true");
        expect(start).not.toHaveAttribute("aria-describedby");
    });

    it.each<[string, Partial<WorkstationRecord>, string, string | null]>([
        [
            "CAPACITY_UNAVAILABLE",
            {},
            CAPACITY_SENTENCE,
            "AWS: InsufficientInstanceCapacity in us-west-2b",
        ],
        [
            "RESERVATION_FULL",
            {
                lastError: "RESERVATION_FULL",
                startFailure: {
                    errorCode: "ReservationCapacityExceeded",
                    availabilityZone: "us-west-2b",
                    observedAt: "2026-10-23T15:19:30Z",
                },
            },
            RESERVATION_FULL_START_MESSAGE,
            "AWS: ReservationCapacityExceeded in us-west-2b",
        ],
        [
            "START_FAILED",
            { lastError: "START_FAILED", startFailure: { observedAt: "2026-10-23T15:29:13Z" } },
            "The workstation did not start; it is still stopped with its disk. Start it again, or ask an administrator.",
            null,
        ],
    ])(
        "shows a start refused with %s beneath the Stopped badge, with the AWS detail line",
        (_code, overrides, sentence, detail) => {
            (queries.useWorkstations as jest.Mock).mockReturnValue(
                list([{ ...capacityRefusedRow, ...overrides }])
            );
            renderPage();

            const cell = statusCell("Render box");
            expect(cell.getByText("Stopped")).toBeInTheDocument();
            expect(cell.getByText(sentence)).toBeInTheDocument();
            // The badge's title carries the sentence too, as an orphaned badge's does.
            expect(cell.getByTitle(sentence)).toHaveTextContent("Stopped");
            if (detail) {
                expect(cell.getByText(detail)).toBeInTheDocument();
            } else {
                expect(cell.queryByText(/^AWS:/)).not.toBeInTheDocument();
            }
            // A start failure is not a launch failure: the orphaned launch sentence is not shown.
            expect(cell.queryByText(LAST_ERROR_COPY.CAPACITY_UNAVAILABLE)).not.toBeInTheDocument();
        }
    );

    it("hides an earlier start failure while a new start of the workstation runs", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...capacityRefusedRow, activity: "starting" }])
        );
        renderPage();

        const cell = statusCell("Render box");
        expect(cell.getByText("Starting")).toBeInTheDocument();
        expect(cell.queryByText(CAPACITY_SENTENCE)).not.toBeInTheDocument();
        expect(cell.queryByText(/^AWS:/)).not.toBeInTheDocument();
    });

    it("reports a start the service accepted (202) as requested and leaves the progress to the list", async () => {
        const start = jest.fn().mockResolvedValue({
            ...stoppedRow,
            activity: "starting",
            sessionBootstrap: "provisioned",
        });
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations(start));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([stoppedRow]));
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Render box");
        await user.click(screen.getByRole("menuitem", { name: "Start" }));

        await waitFor(() => expect(start).toHaveBeenCalledWith({ sessionId: "s-1" }));
        await waitFor(() =>
            expect(mockToast.success).toHaveBeenCalledWith("Workstation start requested", {
                description: "Render box",
            })
        );
        expect(mockToast.error).not.toHaveBeenCalled();
        expect(mockToast.warning).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("shows the service's own message for a 409 a start answers, whatever its code", async () => {
        // A start no longer answers RESERVATION_FULL itself: the worker records it on the row. A 409
        // that still carries the code is an ordinary refusal, reported in the service's words.
        const start = jest
            .fn()
            .mockRejectedValue(
                new EdhRequestError("Workstation is not stopped", 409, { code: "RESERVATION_FULL" })
            );
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations(start));
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([stoppedRow]));
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Render box");
        await user.click(screen.getByRole("menuitem", { name: "Start" }));

        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Start failed", {
                description: "Render box: Workstation is not stopped",
            })
        );
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Failed to start Render box: Workstation is not stopped"
        );
    });
});

describe("WorkstationsPage stalled start", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useWindowsSignIn as jest.Mock).mockReturnValue({
            mutate: jest.fn(),
            reset: jest.fn(),
            data: undefined,
            error: null,
            isPending: false,
        });
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations());
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([stalledRow]));
    });

    it("reads Start stalled with the sentence beneath the badge and no AWS line", () => {
        renderPage();

        const cell = statusCell("Render box");
        expect(cell.getByText("Start stalled")).toBeInTheDocument();
        expect(cell.queryByText("Starting")).not.toBeInTheDocument();
        expect(cell.queryByText("Creating")).not.toBeInTheDocument();
        expect(cell.getByText(STALLED_SENTENCE)).toBeInTheDocument();
        expect(cell.getByTitle(STALLED_SENTENCE)).toHaveTextContent("Start stalled");
        expect(cell.queryByText(/^AWS:/)).not.toBeInTheDocument();
    });

    it("offers Stop and Delete, keeps Start disabled, and leaves Connect inert", async () => {
        const user = userEvent.setup();
        renderPage();

        // Connect stays inert until the desktop runs.
        expect(statusCell("Render box").getByRole("button", { name: "Connect" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await openActionsFor(user, "Render box");
        const stop = screen.getByRole("menuitem", { name: "Stop" });
        expect(stop).not.toHaveAttribute("aria-disabled", "true");
        expect(screen.getByRole("menuitem", { name: "Delete" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        const start = screen.getByRole("menuitem", { name: "Start" });
        expect(start).toHaveAttribute("aria-disabled", "true");
        expect(start).toHaveAccessibleDescription("Only a stopped workstation can be started.");
        expect(screen.queryByText(START_IN_PROGRESS_HINT)).not.toBeInTheDocument();
    });

    it("stops the stalled workstation when Stop is chosen", async () => {
        const m = mutations();
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(m);
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Render box");
        await user.click(screen.getByRole("menuitem", { name: "Stop" }));

        await waitFor(() =>
            expect(m.stopWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "s-1" })
        );
        await waitFor(() =>
            expect(mockToast.success).toHaveBeenCalledWith("Workstation stop requested", {
                description: "Render box",
            })
        );
        expect(m.startWorkstation.mutateAsync).not.toHaveBeenCalled();
    });

    it("opens the delete confirmation when Delete is chosen", async () => {
        const user = userEvent.setup();
        renderPage();

        await openActionsFor(user, "Render box");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));

        expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("keeps Start, Stop and Delete disabled while the start is still under way (control)", async () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(
            list([{ ...stalledRow, lastError: null, startFailure: null, activity: "starting" }])
        );
        const user = userEvent.setup();
        renderPage();

        expect(statusCell("Render box").getByText("Starting")).toBeInTheDocument();
        await openActionsFor(user, "Render box");
        for (const label of ["Start", "Stop", "Delete"]) {
            expect(screen.getByRole("menuitem", { name: label })).toHaveAttribute(
                "aria-disabled",
                "true"
            );
        }
    });
});
