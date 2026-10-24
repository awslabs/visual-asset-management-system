/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsTab from "./SettingsTab";
import * as adminQueries from "../api/adminQueries";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminSettings: jest.fn(),
    useUpdateEdhAdminSettings: jest.fn(),
}));

// The section has its own tests; here only where it sits and when it is shown.
jest.mock("./CapacityReservationsSection", () => ({
    __esModule: true,
    CAPACITY_RESERVATIONS_ROUTE: "/addon/edh/admin/capacity-reservations",
    default: () => <section aria-label="Capacity reservations stub" />,
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const query = (data: any) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const POLICY = {
    enabled: true,
    leaseHours: 8,
    forced: false,
    graceHours: 24,
    updatedAt: "2026-09-17T10:00:00Z",
    updatedBy: "admin",
};

describe("SettingsTab", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (adminQueries.useEdhAdminSettings as jest.Mock).mockReturnValue(query(POLICY));
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync: jest.fn().mockResolvedValue({}),
            isPending: false,
        });
    });

    it("seeds the form from the policy", () => {
        render(<SettingsTab can={() => true} />);
        expect(screen.getByLabelText("Enabled")).toBeChecked();
        expect(screen.getByLabelText("Lease hours")).toHaveValue(8);
        expect(screen.getByLabelText("Force on all launches")).not.toBeChecked();
        expect(screen.getByLabelText("Grace hours")).toHaveValue(24);
        expect(screen.getByText(/Last changed by admin at/)).toBeInTheDocument();
    });

    it("disables Save when the PUT route is not allowed", () => {
        render(<SettingsTab can={(method) => method !== "PUT"} />);
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
        expect(screen.getByText(/You do not have permission to save/)).toBeInTheDocument();
        expect(screen.getByLabelText("Enabled")).toBeDisabled();
        expect(screen.getByLabelText("Lease hours")).toBeDisabled();
        expect(screen.getByLabelText("Force on all launches")).toBeDisabled();
        expect(screen.getByLabelText("Grace hours")).toBeDisabled();
    });

    it("blocks save when lease hours are out of range", async () => {
        const mutateAsync = jest.fn();
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        const leaseHours = screen.getByLabelText("Lease hours") as HTMLInputElement;
        fireEvent.change(leaseHours, { target: { value: "1000" } });
        fireEvent.submit(screen.getByRole("button", { name: "Save" }).closest("form")!);

        await waitFor(() => expect(mutateAsync).not.toHaveBeenCalled());
        expect(screen.getByText("Lease hours must be between 1 and 720.")).toBeInTheDocument();
        expect(leaseHours).toHaveAccessibleDescription(/must be between 1 and 720/);
    });

    it("blocks save when grace hours are out of range", async () => {
        const mutateAsync = jest.fn();
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        const graceHours = screen.getByLabelText("Grace hours") as HTMLInputElement;
        fireEvent.change(graceHours, { target: { value: "200" } });
        fireEvent.submit(screen.getByRole("button", { name: "Save" }).closest("form")!);

        await waitFor(() => expect(mutateAsync).not.toHaveBeenCalled());
        expect(screen.getByText("Grace hours must be between 0 and 168.")).toBeInTheDocument();
        expect(graceHours).toHaveAccessibleDescription(/between 0 and 168/);
    });

    it("blocks save when grace hours are blank", async () => {
        const mutateAsync = jest.fn();
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        const graceHours = screen.getByLabelText("Grace hours") as HTMLInputElement;
        fireEvent.change(graceHours, { target: { value: "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(mutateAsync).not.toHaveBeenCalled());
        expect(screen.getByText("Grace hours must be between 0 and 168.")).toBeInTheDocument();
    });

    it("calls the mutation with all four fields and shows a success toast", async () => {
        const mutateAsync = jest.fn().mockResolvedValue({});
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        const user = userEvent.setup();
        await user.click(screen.getByLabelText("Enabled"));
        await user.clear(screen.getByLabelText("Lease hours"));
        await user.type(screen.getByLabelText("Lease hours"), "12");
        await user.click(screen.getByLabelText("Force on all launches"));
        await user.clear(screen.getByLabelText("Grace hours"));
        await user.type(screen.getByLabelText("Grace hours"), "48");
        await user.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() =>
            expect(mutateAsync).toHaveBeenCalledWith({
                enabled: false,
                leaseHours: 12,
                forced: true,
                graceHours: 48,
            })
        );
        expect(mockToast.success).toHaveBeenCalledWith("Lease policy saved");
    });

    it("sends one save for a double-click on Save", async () => {
        const mutateAsync = jest.fn(() => new Promise<never>(() => undefined));
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        // Both clicks land before a re-render, as a double-click's do.
        const save = screen.getByRole("button", { name: "Save" });
        fireEvent.click(save);
        fireEvent.click(save);
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("shows an error toast when the mutation fails", async () => {
        const mutateAsync = jest.fn().mockRejectedValue(new Error("Invalid lease hours"));
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync,
            isPending: false,
        });
        render(<SettingsTab can={() => true} />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
        const errorCall = mockToast.error.mock.calls[0];
        expect(errorCall[0]).toBe("Save failed");
        expect(errorCall[1].description).toContain("Invalid lease hours");
    });

    it("disables inputs and button while the mutation is pending", () => {
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync: jest.fn(),
            isPending: true,
        });
        render(<SettingsTab can={() => true} />);
        expect(screen.getByLabelText("Enabled")).toBeDisabled();
        expect(screen.getByLabelText("Lease hours")).toBeDisabled();
        expect(screen.getByLabelText("Force on all launches")).toBeDisabled();
        expect(screen.getByLabelText("Grace hours")).toBeDisabled();
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    });

    it("shows the capacity reservations below the lease policy when their route is allowed", () => {
        render(<SettingsTab can={() => true} />);
        const section = screen.getByRole("region", { name: "Capacity reservations stub" });
        const form = screen
            .getByRole("button", { name: "Save" })
            .closest("form") as HTMLFormElement;
        expect(
            form.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
    });

    it("leaves the capacity reservations out when their route is not allowed", () => {
        render(
            <SettingsTab
                can={(method, route) => route !== "/addon/edh/admin/capacity-reservations"}
            />
        );
        expect(
            screen.queryByRole("region", { name: "Capacity reservations stub" })
        ).not.toBeInTheDocument();
        expect(screen.getByLabelText("Lease hours")).toBeInTheDocument();
    });
});
