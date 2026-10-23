/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CapacityReservationsSection, {
    CAPACITY_RESERVATIONS_ROUTE,
    CAPACITY_RESERVATION_ROUTE,
} from "./CapacityReservationsSection";
import * as adminQueries from "../api/adminQueries";
import type { CapacityReservationView } from "../types";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminCapacityReservations: jest.fn(),
    useEdhCapacityReservationMutations: jest.fn(),
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const RENDER_POOL: CapacityReservationView = {
    capacityReservationId: "cr-0123456789abcdef0",
    label: "Render pool",
    fallbackToOnDemand: true,
    enabled: true,
    instanceType: "g5.16xlarge",
    availabilityZone: "us-east-1a",
    instancePlatform: "Linux/UNIX",
    state: "active",
    totalInstanceCount: 4,
    availableInstanceCount: 3,
    instanceMatchCriteria: "targeted",
    endDate: "2026-10-24T16:00:00Z",
    reservationType: "default",
};

const GONE: CapacityReservationView = {
    ...RENDER_POOL,
    capacityReservationId: "cr-0fedcba9876543210",
    label: "Old pool",
    fallbackToOnDemand: false,
    enabled: false,
    instanceType: null,
    availabilityZone: null,
    instancePlatform: null,
    state: "notFound",
    totalInstanceCount: null,
    availableInstanceCount: null,
    endDate: null,
};

const query = (data: any, overrides: any = {}) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
    ...overrides,
});

const mutation = (impl: jest.Mock = jest.fn().mockResolvedValue({})) => ({
    mutateAsync: impl,
    isPending: false,
});

function mount(
    can: (method: string, route: string) => boolean = () => true,
    rows: CapacityReservationView[] = [RENDER_POOL, GONE]
) {
    const register = mutation();
    const update = mutation();
    const unregister = mutation();
    (adminQueries.useEdhAdminCapacityReservations as jest.Mock).mockReturnValue(query(rows));
    (adminQueries.useEdhCapacityReservationMutations as jest.Mock).mockReturnValue({
        register,
        update,
        unregister,
    });
    render(<CapacityReservationsSection can={can} />);
    return { register, update, unregister };
}

const table = () => screen.getByRole("table", { name: "Capacity reservations" });
const rowOf = (label: string) =>
    within(table())
        .getAllByRole("row")
        .find((r) => within(r).queryByText(label)) as HTMLElement;

describe("CapacityReservationsSection", () => {
    beforeEach(() => jest.clearAllMocks());

    it("gates its routes on the addonApiRoutes templates", () => {
        expect(CAPACITY_RESERVATIONS_ROUTE).toBe("/addon/edh/admin/capacity-reservations");
        expect(CAPACITY_RESERVATION_ROUTE).toBe(
            "/addon/edh/admin/capacity-reservations/{capacityReservationId}"
        );
    });

    it("lists each reservation with the registry's and EC2's fields", () => {
        mount();
        expect(screen.getByRole("heading", { name: "Capacity reservations" })).toBeInTheDocument();
        const headers = within(table())
            .getAllByRole("columnheader")
            .map((h) => h.textContent?.trim());
        expect(headers).toEqual([
            "Label",
            "Reservation",
            "Instance type",
            "Availability Zone",
            "Platform",
            "State",
            "Free / total",
            "End date",
            "Fallback",
            "Enabled",
            "Actions",
        ]);
        const pool = within(rowOf("Render pool"));
        expect(pool.getByText("cr-0123456789abcdef0")).toBeInTheDocument();
        expect(pool.getByText("g5.16xlarge")).toBeInTheDocument();
        expect(pool.getByText("us-east-1a")).toBeInTheDocument();
        expect(pool.getByText("Linux/UNIX")).toBeInTheDocument();
        expect(pool.getByText("active")).toBeInTheDocument();
        expect(pool.getByText("3/4")).toBeInTheDocument();
        expect(pool.getByText("On-demand")).toBeInTheDocument();
        expect(pool.getByText("Yes")).toBeInTheDocument();

        const gone = within(rowOf("Old pool"));
        expect(gone.getByText("Not found in EC2")).toBeInTheDocument();
        expect(gone.getByText("No end date")).toBeInTheDocument();
        expect(gone.getByText("Refuse")).toBeInTheDocument();
        expect(gone.getByText("No")).toBeInTheDocument();
    });

    it("says when nothing is registered", () => {
        mount(() => true, []);
        expect(screen.getByText(/No capacity reservations are registered/)).toBeInTheDocument();
    });

    it("registers a reservation with its label and switches", async () => {
        const user = userEvent.setup();
        const { register } = mount();
        await user.click(screen.getByRole("button", { name: "Register reservation" }));
        const dialog = within(screen.getByRole("dialog"));
        await user.type(dialog.getByLabelText("Capacity reservation ID"), "cr-00000000000000abc");
        await user.type(dialog.getByLabelText("Label"), "Demo pool");
        await user.click(dialog.getByLabelText("Fall back to on-demand when full"));
        await user.click(dialog.getByRole("button", { name: "Register" }));

        await waitFor(() =>
            expect(register.mutateAsync).toHaveBeenCalledWith({
                capacityReservationId: "cr-00000000000000abc",
                label: "Demo pool",
                fallbackToOnDemand: false,
                enabled: true,
            })
        );
        expect(mockToast.success).toHaveBeenCalledWith("Capacity reservation registered", {
            description: "Demo pool",
        });
    });

    it("refuses an id that is not an EC2 reservation id, and an empty label, before any request", async () => {
        const user = userEvent.setup();
        const { register } = mount();
        await user.click(screen.getByRole("button", { name: "Register reservation" }));
        const dialog = within(screen.getByRole("dialog"));
        await user.type(dialog.getByLabelText("Capacity reservation ID"), "cr-123");
        await user.click(dialog.getByRole("button", { name: "Register" }));

        expect(
            dialog.getByText("Enter an EC2 capacity reservation ID (cr- and 17 hex digits).")
        ).toBeInTheDocument();
        expect(
            dialog.getByText(
                "Label must be 1-64 letters, digits, spaces, hyphens, periods or underscores."
            )
        ).toBeInTheDocument();
        expect(register.mutateAsync).not.toHaveBeenCalled();
    });

    it("keeps the dialog open and reports a refused registration", async () => {
        const user = userEvent.setup();
        const { register } = mount();
        register.mutateAsync.mockRejectedValue(
            new Error("Capacity reservation not found in this account and Region")
        );
        await user.click(screen.getByRole("button", { name: "Register reservation" }));
        const dialog = within(screen.getByRole("dialog"));
        await user.type(dialog.getByLabelText("Capacity reservation ID"), "cr-00000000000000abc");
        await user.type(dialog.getByLabelText("Label"), "Demo pool");
        await user.click(dialog.getByRole("button", { name: "Register" }));

        expect(await dialog.findByRole("alert")).toHaveTextContent(
            "Capacity reservation not found in this account and Region"
        );
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("edits the label and switches of a reservation, never its id", async () => {
        const user = userEvent.setup();
        const { update } = mount();
        await user.click(screen.getByRole("button", { name: "Edit Render pool" }));
        const dialog = within(screen.getByRole("dialog"));
        expect(dialog.getByLabelText("Capacity reservation ID")).toHaveValue(
            "cr-0123456789abcdef0"
        );
        expect(dialog.getByLabelText("Capacity reservation ID")).toBeDisabled();
        expect(dialog.getByLabelText("Fall back to on-demand when full")).toBeChecked();
        await user.clear(dialog.getByLabelText("Label"));
        await user.type(dialog.getByLabelText("Label"), "Render farm");
        await user.click(dialog.getByLabelText("Enabled"));
        await user.click(dialog.getByRole("button", { name: "Save" }));

        await waitFor(() =>
            expect(update.mutateAsync).toHaveBeenCalledWith({
                capacityReservationId: "cr-0123456789abcdef0",
                body: { label: "Render farm", fallbackToOnDemand: true, enabled: false },
            })
        );
        expect(mockToast.success).toHaveBeenCalledWith("Capacity reservation saved", {
            description: "Render farm",
        });
    });

    it("unregisters only after the confirmation, which says EC2 is not touched", async () => {
        const user = userEvent.setup();
        const { unregister } = mount();
        await user.click(screen.getByRole("button", { name: "Unregister Render pool" }));
        const dialog = within(screen.getByRole("dialog"));
        expect(dialog.getByText(/The EC2 reservation itself is not changed/)).toBeInTheDocument();
        expect(unregister.mutateAsync).not.toHaveBeenCalled();
        await user.click(dialog.getByRole("button", { name: "Unregister" }));

        await waitFor(() =>
            expect(unregister.mutateAsync).toHaveBeenCalledWith("cr-0123456789abcdef0")
        );
        expect(mockToast.success).toHaveBeenCalledWith("Capacity reservation unregistered", {
            description: "Render pool",
        });
    });

    it("cancels an unregister without a request", async () => {
        const user = userEvent.setup();
        const { unregister } = mount();
        await user.click(screen.getByRole("button", { name: "Unregister Render pool" }));
        await user.click(
            within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(unregister.mutateAsync).not.toHaveBeenCalled();
    });

    it("offers each action only where its route is allowed", () => {
        mount((method) => method === "GET");
        expect(
            screen.queryByRole("button", { name: "Register reservation" })
        ).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Edit Render pool" })).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Unregister Render pool" })
        ).not.toBeInTheDocument();
        expect(
            within(table())
                .getAllByRole("columnheader")
                .map((h) => h.textContent?.trim())
        ).not.toContain("Actions");
    });

    it("checks each action against its own route", () => {
        const can = jest.fn(
            (method: string, route: string) =>
                method === "GET" || (method === "DELETE" && route === CAPACITY_RESERVATION_ROUTE)
        );
        mount(can);
        expect(can).toHaveBeenCalledWith("POST", CAPACITY_RESERVATIONS_ROUTE);
        expect(can).toHaveBeenCalledWith("PUT", CAPACITY_RESERVATION_ROUTE);
        expect(screen.getByRole("button", { name: "Unregister Render pool" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Edit Render pool" })).not.toBeInTheDocument();
    });

    it("reports a failed listing", () => {
        (adminQueries.useEdhAdminCapacityReservations as jest.Mock).mockReturnValue(
            query(undefined, { error: new Error("boom") })
        );
        (adminQueries.useEdhCapacityReservationMutations as jest.Mock).mockReturnValue({
            register: mutation(),
            update: mutation(),
            unregister: mutation(),
        });
        render(<CapacityReservationsSection can={() => true} />);
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Error loading capacity reservations: boom"
        );
    });
});
