/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage from "./WorkstationsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import type { SessionBootstrapOutcome, WorkstationRecord } from "../types";

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

// A running desktop under a lease the policy still governs, so its menu offers Renew lease.
const leasedRow: WorkstationRecord = {
    ...stoppedRow,
    state: "ACTIVE",
    edhState: "running",
    connectionAvailable: true,
    leaseExpiresAt: "2100-01-01T00:00:00+00:00",
};

type Action = "start" | "renew";

function mountPage(
    sessionBootstrap: SessionBootstrapOutcome | undefined,
    action: Action = "start"
) {
    const row = action === "start" ? stoppedRow : leasedRow;
    const answer =
        action === "start"
            ? { ...stoppedRow, state: "CREATING", edhState: "pending" }
            : { ...leasedRow, leaseExpiresAt: "2100-01-02T00:00:00+00:00" };
    const mutateAsync = jest.fn().mockResolvedValue({
        ...answer,
        ...(sessionBootstrap ? { sessionBootstrap } : {}),
    });
    (queries.useWorkstations as jest.Mock).mockReturnValue({
        data: [row],
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        isFetching: false,
    });
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
        createWorkstation: { mutateAsync: jest.fn(), isPending: false },
        stopWorkstation: { mutateAsync: jest.fn(), isPending: false },
        startWorkstation: {
            mutateAsync: action === "start" ? mutateAsync : jest.fn(),
            isPending: false,
        },
        deleteWorkstation: { mutateAsync: jest.fn(), isPending: false },
        renewWorkstation: {
            mutateAsync: action === "renew" ? mutateAsync : jest.fn(),
            isPending: false,
        },
    });
    // Seeded on every mount: clearAllMocks keeps a test's mockReturnValue.
    (queries.useEdhCatalogLease as jest.Mock).mockReturnValue({
        data:
            action === "renew"
                ? { enabled: true, leaseHours: 8, forced: false, graceHours: 24 }
                : undefined,
        isLoading: false,
        error: null,
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
    return mutateAsync;
}

async function start(user: ReturnType<typeof userEvent.setup>) {
    // The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
    screen.getByLabelText("Actions for Render box").focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: "Start" }));
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
    screen.getByLabelText("Actions for Render box").focus();
    await user.keyboard("{Enter}");
}

async function renew(user: ReturnType<typeof userEvent.setup>) {
    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Renew lease" }));
}

beforeEach(() => {
    jest.clearAllMocks();
});

describe("WorkstationsPage session-bootstrap notice on start", () => {
    it.each([
        ["skipped", /no roles/],
        ["unavailable", /could not be provisioned/],
    ] as const)("warns after a start whose credential was %s", async (outcome, expected) => {
        const user = userEvent.setup();
        const mutateAsync = mountPage(outcome);
        await start(user);
        await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
        expect(mutateAsync).toHaveBeenCalledWith({ sessionId: "s-2" });
        expect(mockToast.warning).toHaveBeenCalledWith(
            "Workstation sign-in not provisioned",
            expect.objectContaining({
                description: expect.stringMatching(expected),
                duration: 0,
            })
        );
    });

    it.each(["provisioned", "disabled", undefined] as const)(
        "stays quiet after a start whose outcome is %s",
        async (outcome) => {
            const user = userEvent.setup();
            mountPage(outcome);
            await start(user);
            await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
            expect(mockToast.warning).not.toHaveBeenCalled();
        }
    );
});

describe("WorkstationsPage session-bootstrap notice on renew", () => {
    // The desktop is running: the notice points to another renew, not to a stop and start that
    // would end the session.
    it.each([
        ["skipped", /no roles.*renew the lease again/],
        ["unavailable", /still holds keeps working until it expires; renew the lease again/],
    ] as const)(
        "warns after a renew whose re-issued credential was %s",
        async (outcome, expected) => {
            const user = userEvent.setup();
            const mutateAsync = mountPage(outcome, "renew");
            await renew(user);
            await waitFor(() =>
                expect(mockToast.success).toHaveBeenCalledWith(
                    "Workstation lease renewed",
                    expect.objectContaining({ description: "Render box" })
                )
            );
            expect(mutateAsync).toHaveBeenCalledWith({ sessionId: "s-2" });
            expect(mockToast.warning).toHaveBeenCalledWith(
                "Workstation sign-in not provisioned",
                expect.objectContaining({
                    description: expect.stringMatching(expected),
                    duration: 0,
                })
            );
            expect(mockToast.warning).not.toHaveBeenCalledWith(
                expect.anything(),
                expect.objectContaining({
                    description: expect.stringMatching(/still starts|stop and start/),
                })
            );
        }
    );

    it("disables Renew lease while a renew is in flight, so a second Renew sends nothing", async () => {
        const user = userEvent.setup();
        const mutateAsync = mountPage("provisioned", "renew");
        let answer: (record: WorkstationRecord) => void = () => undefined;
        mutateAsync.mockImplementation(
            () =>
                new Promise<WorkstationRecord>((resolve) => {
                    answer = resolve;
                })
        );
        await renew(user);
        await openMenu(user);
        const again = await screen.findByRole("menuitem", { name: "Renew lease" });
        expect(again).toHaveAttribute("aria-disabled", "true");
        expect(again).toHaveAccessibleDescription("A renew of this workstation is in progress.");
        await user.click(again);
        expect(mutateAsync).toHaveBeenCalledTimes(1);
        await user.keyboard("{Escape}");

        await act(async () => answer({ ...leasedRow, sessionBootstrap: "provisioned" }));
        await openMenu(user);
        expect(await screen.findByRole("menuitem", { name: "Renew lease" })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(mockToast.warning).not.toHaveBeenCalled();
    });

    it.each(["provisioned", "disabled", undefined] as const)(
        "shows only the renew flash after a renew whose outcome is %s",
        async (outcome) => {
            const user = userEvent.setup();
            mountPage(outcome, "renew");
            await renew(user);
            await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
            expect(mockToast.success).toHaveBeenCalledWith(
                "Workstation lease renewed",
                expect.objectContaining({ description: "Render box" })
            );
            expect(mockToast.warning).not.toHaveBeenCalled();
        }
    );
});
