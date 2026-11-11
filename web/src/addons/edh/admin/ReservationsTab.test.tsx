/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReservationsTab, {
    CAPACITY_RESERVATIONS_ROUTE,
    CAPACITY_RESERVATION_ROUTE,
} from "./ReservationsTab";
import * as adminQueries from "../api/adminQueries";
import type { CapacityReservationView } from "../types";
import { expectHeldOpen } from "../components/heldDialogTestSupport";

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
    render(<ReservationsTab can={can} />);
    return { register, update, unregister };
}

/**
 * Holds `held`'s next request in flight, reading as pending, until the returned function answers
 * it.
 */
const holdRequest = (held: ReturnType<typeof mutation>) => {
    let answer = () => undefined as void;
    held.mutateAsync = jest.fn(() => {
        held.isPending = true;
        return new Promise((resolve) => {
            answer = () => {
                held.isPending = false;
                resolve({});
            };
        });
    });
    return () => act(async () => answer());
};

const table = () => screen.getByRole("table", { name: "Capacity reservations" });
const rowOf = (label: string) =>
    within(table())
        .getAllByRole("row")
        .find((r) => within(r).queryByText(label)) as HTMLElement;

describe("ReservationsTab", () => {
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

    it("says what the registry is for, in full sentences", () => {
        mount();
        expect(
            screen.getByText(
                "Register EC2 capacity reservations so users can launch workstations into reserved capacity. " +
                    "A user sees a reservation when their role allows it and the reservation is enabled, active, " +
                    "and in an Availability Zone the cluster can launch into. Launches use on-demand capacity otherwise."
            )
        ).toBeInTheDocument();
    });

    it("says when nothing is registered, with the one Register button inside the empty state", async () => {
        const user = userEvent.setup();
        mount(() => true, []);
        const empty = screen
            .getByText("No reservations registered. Launches use on-demand capacity.")
            .closest("div") as HTMLElement;
        const registers = screen.getAllByRole("button", { name: "Register reservation" });
        expect(registers).toHaveLength(1);
        expect(empty).toContainElement(registers[0]);
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        await user.click(registers[0]);
        expect(screen.getByRole("dialog")).toHaveTextContent("Register capacity reservation");
    });

    it("leaves the Register button out of the empty state when the role may not register", () => {
        mount((method) => method === "GET", []);
        expect(
            screen.getByText("No reservations registered. Launches use on-demand capacity.")
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Register reservation" })
        ).not.toBeInTheDocument();
    });

    it("keeps Register in the header once a reservation is listed", () => {
        mount();
        const heading = screen.getByRole("heading", { name: "Capacity reservations" });
        const register = screen.getByRole("button", { name: "Register reservation" });
        expect(
            heading.compareDocumentPosition(register) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        expect(
            register.compareDocumentPosition(table()) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
    });

    it.each([
        ["expired", 2, 2],
        ["cancelled", 0, 1],
        ["pending", 2, 2],
    ])("shows no counts for a reservation whose state is %s", (state, available, total) => {
        mount(
            () => true,
            [
                {
                    ...RENDER_POOL,
                    state,
                    availableInstanceCount: available,
                    totalInstanceCount: total,
                },
            ]
        );
        const headers = within(table())
            .getAllByRole("columnheader")
            .map((h) => h.textContent?.trim());
        const cells = within(rowOf("Render pool")).getAllByRole("cell");
        expect(cells[headers.indexOf("Free / total")]).toHaveTextContent(/^—$/);
        expect(cells[headers.indexOf("State")]).toHaveTextContent(state);
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
        render(<ReservationsTab can={() => true} />);
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Error loading capacity reservations: boom"
        );
    });
});

describe("focus when the Unregister confirmation closes", () => {
    beforeEach(() => jest.clearAllMocks());

    // Hidden included: the table is hidden from the accessibility tree while a dialog is open.
    const unregisterButton = () =>
        screen.getByRole("button", { name: "Unregister Render pool", hidden: true });

    it.each(["Escape", "Cancel"])(
        "returns to the row's Unregister button after %s",
        async (how) => {
            const user = userEvent.setup();
            mount();
            unregisterButton().focus();
            await user.keyboard("{Enter}");
            const dialog = await screen.findByRole("dialog");
            if (how === "Escape") {
                await user.keyboard("{Escape}");
            } else {
                await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
            }
            await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
            await waitFor(() => expect(unregisterButton()).toHaveFocus());
        }
    );

    it("goes to the refresh button once the reservation is unregistered, and back to Unregister when it fails", async () => {
        const user = userEvent.setup();
        const { unregister } = mount();
        unregisterButton().focus();
        await user.keyboard("{Enter}");
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Unregister" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(unregister.mutateAsync).toHaveBeenCalledWith("cr-0123456789abcdef0");
        await waitFor(() =>
            expect(
                screen.getByRole("button", { name: "Refresh capacity reservations" })
            ).toHaveFocus()
        );

        unregister.mutateAsync.mockRejectedValue(new Error("refused"));
        unregisterButton().focus();
        await user.keyboard("{Enter}");
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Unregister" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(unregisterButton()).toHaveFocus());
    });

    it("keeps focus on Unregistering... and the confirmation open while the unregister is in flight", async () => {
        const user = userEvent.setup({ delay: null });
        const { unregister } = mount();
        const answer = holdRequest(unregister);
        unregisterButton().focus();
        await user.keyboard("{Enter}");
        const dialog = await screen.findByRole("dialog");
        within(dialog).getByRole("button", { name: "Unregister" }).focus();
        await user.keyboard("{Enter}");

        const busy = within(dialog).getByRole("button", { name: "Unregistering..." });
        expect(busy).toHaveAttribute("aria-disabled", "true");
        expect(busy).not.toBeDisabled();
        expect(busy).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        expect(screen.getByRole("dialog")).toBe(dialog);
        expect(unregister.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() =>
            expect(
                screen.getByRole("button", { name: "Refresh capacity reservations" })
            ).toHaveFocus()
        );
    });

    it("returns to Unregister when the confirmation opens again on a reservation the list still shows", async () => {
        const user = userEvent.setup({ delay: null });
        mount();
        // The list still reads the reservation once the unregister is answered.
        unregisterButton().focus();
        await user.keyboard("{Enter}");
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Unregister" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        unregisterButton().focus();
        await user.keyboard("{Enter}");
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(unregisterButton()).toHaveFocus());
    });
});

describe("focus when the Register or Edit dialog closes", () => {
    beforeEach(() => jest.clearAllMocks());

    // Hidden included: the table is hidden from the accessibility tree while a dialog is open.
    const button = (name: string) => screen.getByRole("button", { name, hidden: true });
    const refreshButton = () => button("Refresh capacity reservations");

    const open = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
        button(name).focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog");
    };

    const closed = () =>
        waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    it.each([
        ["Register reservation", "Escape"],
        ["Register reservation", "Cancel"],
        ["Edit Render pool", "Escape"],
        ["Edit Render pool", "Cancel"],
    ])("returns to %s after %s", async (name, how) => {
        const user = userEvent.setup({ delay: null });
        mount();
        const dialog = await open(user, name);
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        }
        await closed();
        await waitFor(() => expect(button(name)).toHaveFocus());
    });

    it("returns to the empty state's Register button after Cancel", async () => {
        const user = userEvent.setup({ delay: null });
        mount(() => true, []);
        const dialog = await open(user, "Register reservation");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(button("Register reservation")).toHaveFocus());
    });

    it("goes to the refresh button once a reservation is registered", async () => {
        const user = userEvent.setup({ delay: null });
        const { register } = mount();
        const dialog = within(await open(user, "Register reservation"));
        await user.click(dialog.getByLabelText("Capacity reservation ID"));
        await user.paste("cr-00000000000000abc");
        await user.click(dialog.getByLabelText("Label"));
        await user.paste("Demo pool");
        await user.click(dialog.getByRole("button", { name: "Register" }));
        await closed();
        expect(register.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(refreshButton()).toHaveFocus());
    });

    it("returns to the row's Edit button once an edit is saved, the row staying", async () => {
        const user = userEvent.setup({ delay: null });
        const { update } = mount();
        const dialog = await open(user, "Edit Render pool");
        await user.click(within(dialog).getByRole("button", { name: "Save" }));
        await closed();
        expect(update.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(button("Edit Render pool")).toHaveFocus());
    });

    it("keeps the form open, Saving... and its fields focusable but unchangeable while an edit is saved", async () => {
        const user = userEvent.setup({ delay: null });
        const { update } = mount();
        const answer = holdRequest(update);
        const dialog = await open(user, "Edit Render pool");
        within(dialog).getByRole("button", { name: "Save" }).focus();
        await user.keyboard("{Enter}");

        const saving = within(dialog).getByRole("button", { name: "Saving..." });
        expect(saving).toHaveAttribute("aria-disabled", "true");
        expect(saving).not.toBeDisabled();
        expect(saving).toHaveFocus();
        const label = within(dialog).getByLabelText("Label");
        const enabled = within(dialog).getByLabelText("Enabled");
        for (const field of [label, enabled]) {
            expect(field).toHaveAttribute("aria-disabled", "true");
            expect(field).not.toBeDisabled();
        }
        // An edit never changes the id, whatever the save is doing.
        expect(within(dialog).getByLabelText("Capacity reservation ID")).toBeDisabled();
        await user.type(label, "x");
        expect(label).toHaveValue("Render pool");
        await user.click(enabled);
        expect(enabled).toBeChecked();

        saving.focus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        expect(screen.getByRole("dialog")).toBe(dialog);
        expect(update.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() => expect(button("Edit Render pool")).toHaveFocus());
    });

    it("holds the register form while the registration is in flight: the close button reads unavailable and the backdrop keeps focus", async () => {
        const user = userEvent.setup({ delay: null });
        const { register } = mount();
        const answer = holdRequest(register);
        const dialog = await open(user, "Register reservation");
        await user.click(within(dialog).getByLabelText("Capacity reservation ID"));
        await user.paste("cr-00000000000000abc");
        await user.click(within(dialog).getByLabelText("Label"));
        await user.paste("Demo pool");
        await user.click(within(dialog).getByRole("button", { name: "Register" }));

        await expectHeldOpen(user, dialog);
        expect(register.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
    });

    it("keeps focus in the field a click on Register left it in, while the registration is in flight", async () => {
        const user = userEvent.setup({ delay: null });
        const { register } = mount();
        const answer = holdRequest(register);
        const dialog = within(await open(user, "Register reservation"));
        await user.click(dialog.getByLabelText("Capacity reservation ID"));
        await user.paste("cr-00000000000000abc");
        await user.click(dialog.getByLabelText("Label"));
        await user.paste("Demo pool");
        // A browser that does not focus a clicked button (Safari) leaves focus in the field.
        fireEvent.click(dialog.getByRole("button", { name: "Register" }));

        const label = dialog.getByLabelText("Label");
        expect(dialog.getByRole("button", { name: "Registering..." })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(label).toHaveAttribute("aria-disabled", "true");
        expect(label).toHaveAttribute("readonly");
        expect(label).not.toBeDisabled();
        expect(label).toHaveFocus();
        expect(register.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() => expect(refreshButton()).toHaveFocus());
    });
});
