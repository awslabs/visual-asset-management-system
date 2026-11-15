/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import { EdhRequestError, OUTCOME_UNKNOWN_MESSAGE } from "../api/outcome";
import type { WorkstationRecord } from "../types";

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };

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
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: ["EDH_WORKSTATIONS"] })) },
}));

jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

// The bodies the add-on's handlers answer with, as the service layer rejects with them.
const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const UNAVAILABLE = "EDH is unavailable. Try again later.";
const IN_PROGRESS = "A start of this workstation is already in progress";

const unknown502 = () =>
    new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true });
const gateway504 = () =>
    new EdhRequestError("Endpoint request timed out", 504, {
        message: "Endpoint request timed out",
    });
const plain502 = () => new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE });
const inProgress409 = () =>
    new EdhRequestError(IN_PROGRESS, 409, { message: IN_PROGRESS, startInProgress: true });

const stoppedRow: WorkstationRecord = {
    bootstrapId: "b-2",
    sessionId: "s-2",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "STOPPED",
    edhState: "stopped",
    name: "Render box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.xlarge",
    createdAt: "2026-09-08T10:00:00Z",
    updatedAt: "2026-09-08T10:05:00Z",
    connectionAvailable: false,
};

const activeRow: WorkstationRecord = {
    ...stoppedRow,
    state: "ACTIVE",
    edhState: "running",
    connectionAvailable: true,
};

type Action = "Stop" | "Start" | "Delete";

const MUTATION: Record<Action, "stopWorkstation" | "startWorkstation" | "deleteWorkstation"> = {
    Stop: "stopWorkstation",
    Start: "startWorkstation",
    Delete: "deleteWorkstation",
};

/** Mounts the page over one row whose `action` rejects with `error`; returns that mutation. */
function mountPage(action: Action, error: Error) {
    const row = action === "Start" ? stoppedRow : activeRow;
    const rejected = jest.fn().mockRejectedValue(error);
    const mutation = (name: string) => ({
        mutateAsync: name === MUTATION[action] ? rejected : jest.fn(),
        isPending: false,
    });
    (queries.useWorkstations as jest.Mock).mockReturnValue({
        data: [row],
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        isFetching: false,
    });
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
        createWorkstation: mutation("createWorkstation"),
        stopWorkstation: mutation("stopWorkstation"),
        startWorkstation: mutation("startWorkstation"),
        deleteWorkstation: mutation("deleteWorkstation"),
        renewWorkstation: mutation("renewWorkstation"),
    });
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
        loading: false,
        can: () => true,
    });
    render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <WorkstationsPage />
            </MemoryRouter>
        </QueryClientProvider>
    );
    return rejected;
}

async function choose(user: ReturnType<typeof userEvent.setup>, action: Action) {
    // The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
    screen.getByLabelText("Actions for Render box").focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: action }));
    if (action === "Delete") {
        await user.click(await screen.findByRole("button", { name: "Delete" }));
    }
}

beforeEach(() => {
    jest.clearAllMocks();
});

describe("WorkstationsPage when EDH does not confirm an action", () => {
    it.each(["Stop", "Start", "Delete"] as const)(
        "warns '%s not confirmed' with the backend's message for a flagged 502, and reports no failure",
        async (action) => {
            const user = userEvent.setup();
            const rejected = mountPage(action, unknown502());
            await choose(user, action);
            await waitFor(() =>
                expect(mockToast.warning).toHaveBeenCalledWith(`${action} not confirmed`, {
                    description: `Render box: ${UNCONFIRMED}`,
                })
            );
            expect(rejected).toHaveBeenCalledWith({ sessionId: "s-2" });
            expect(mockToast.error).not.toHaveBeenCalled();
            expect(mockToast.success).not.toHaveBeenCalled();
            expect(screen.queryByRole("alert")).not.toBeInTheDocument();
            expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        }
    );

    it.each(["Stop", "Start", "Delete"] as const)(
        "treats the gateway's 504 on %s the same way, in the handlers' words",
        async (action) => {
            const user = userEvent.setup();
            mountPage(action, gateway504());
            await choose(user, action);
            await waitFor(() =>
                expect(mockToast.warning).toHaveBeenCalledWith(`${action} not confirmed`, {
                    description: `Render box: ${OUTCOME_UNKNOWN_MESSAGE}`,
                })
            );
            expect(mockToast.error).not.toHaveBeenCalled();
            expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        }
    );

    it("still reports a plain 502 as a failed start, with the banner", async () => {
        const user = userEvent.setup();
        mountPage("Start", plain502());
        await choose(user, "Start");
        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Start failed", {
                description: `Render box: ${UNAVAILABLE}`,
            })
        );
        expect(screen.getByRole("alert")).toHaveTextContent(
            `Failed to start Render box: ${UNAVAILABLE}`
        );
        expect(mockToast.warning).not.toHaveBeenCalled();
    });

    it("warns 'Start already in progress' for the 409 a repeated start answers", async () => {
        const user = userEvent.setup();
        mountPage("Start", inProgress409());
        await choose(user, "Start");
        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Start already in progress", {
                description: `Render box: ${IN_PROGRESS}`,
            })
        );
        expect(mockToast.error).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("reports a 409 without the flag as a failed start, whatever its words", async () => {
        const user = userEvent.setup();
        mountPage("Start", new EdhRequestError(IN_PROGRESS, 409, { message: IN_PROGRESS }));
        await choose(user, "Start");
        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Start failed", {
                description: `Render box: ${IN_PROGRESS}`,
            })
        );
        expect(mockToast.warning).not.toHaveBeenCalled();
    });
});
