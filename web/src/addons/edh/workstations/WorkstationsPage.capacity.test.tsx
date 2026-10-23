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
import { LAST_ERROR_COPY } from "../components/WorkstationStatusBadge";
import { EdhRequestError, RESERVATION_FULL_START_MESSAGE } from "../api/outcome";
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

const base: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Reserved box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.16xlarge",
    createdAt: "2026-10-23T10:00:00Z",
    updatedAt: "2026-10-23T10:05:00Z",
    connectionAvailable: true,
    capacityReservationId: "cr-0123456789abcdef0",
    capacityReservationLabel: "Render pool",
    capacityReservationFallback: false,
};

const fallbackRow: WorkstationRecord = {
    ...base,
    bootstrapId: "b-2",
    sessionId: "s-2",
    name: "Overflow box",
    capacityReservationId: null,
    capacityReservationLabel: null,
    capacityReservationFallback: true,
};

const onDemandRow: WorkstationRecord = {
    ...base,
    bootstrapId: "b-3",
    sessionId: "s-3",
    name: "Plain box",
    capacityReservationId: null,
    capacityReservationLabel: null,
    capacityReservationFallback: null,
};

const fullRow: WorkstationRecord = {
    ...base,
    bootstrapId: "b-4",
    sessionId: null,
    name: "Refused box",
    state: "ORPHANED",
    edhState: null,
    connectionAvailable: false,
    lastError: "RESERVATION_FULL",
};

const stoppedReservedRow: WorkstationRecord = {
    ...base,
    bootstrapId: "b-5",
    sessionId: "s-5",
    name: "Stopped box",
    state: "STOPPED",
    edhState: "stopped",
    connectionAvailable: false,
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

describe("WorkstationsPage capacity", () => {
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

    it("names the reservation a workstation launched into, by label and id", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([base]));
        renderPage();
        expect(
            screen.getByText("Capacity: Render pool (cr-0123456789abcdef0)")
        ).toBeInTheDocument();
    });

    it("says a workstation whose reservation was full launched on-demand", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([fallbackRow]));
        renderPage();
        expect(screen.getByText("Capacity: On-demand (reservation full)")).toBeInTheDocument();
    });

    it("shows no capacity detail for an on-demand workstation", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([onDemandRow]));
        renderPage();
        expect(screen.getByText("Plain box")).toBeInTheDocument();
        expect(screen.queryByText(/^Capacity:/)).not.toBeInTheDocument();
    });

    it("explains a launch refused because the reservation was full", () => {
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([fullRow]));
        renderPage();
        expect(LAST_ERROR_COPY.RESERVATION_FULL).toBeTruthy();
        expect(screen.getByText(LAST_ERROR_COPY.RESERVATION_FULL)).toBeInTheDocument();
    });

    it("says a start refused for a full reservation leaves the workstation stopped", async () => {
        const start = jest
            .fn()
            .mockRejectedValue(
                new EdhRequestError(
                    "The capacity reservation has no free capacity; the workstation stays stopped",
                    409,
                    { code: "RESERVATION_FULL" }
                )
            );
        (queries.useWorkstations as jest.Mock).mockReturnValue(list([stoppedReservedRow]));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations(start));
        const user = userEvent.setup();
        renderPage();
        screen.getByLabelText("Actions for Stopped box").focus();
        await user.keyboard("{Enter}");
        await user.click(await screen.findByRole("menuitem", { name: /Start/ }));

        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Start failed", {
                description: `Stopped box: ${RESERVATION_FULL_START_MESSAGE}`,
            })
        );
        expect(screen.getByRole("alert")).toHaveTextContent(
            `Failed to start Stopped box: ${RESERVATION_FULL_START_MESSAGE}`
        );
    });
});
