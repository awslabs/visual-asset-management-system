/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsTab, { SYSTEM_USER_ID, lastChangedText } from "./SettingsTab";
import * as adminQueries from "../api/adminQueries";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminSettings: jest.fn(),
    useUpdateEdhAdminSettings: jest.fn(),
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
        expect(screen.getByLabelText("Expire workstations after a lease")).toBeChecked();
        expect(screen.getByLabelText("Lease hours")).toHaveValue(8);
        expect(screen.getByLabelText("Force on all launches")).not.toBeChecked();
        expect(screen.getByLabelText("Grace hours")).toHaveValue(24);
        expect(screen.getByText(/Last changed by admin at/)).toBeInTheDocument();
    });

    it("disables Save when the PUT route is not allowed", () => {
        render(<SettingsTab can={(method) => method !== "PUT"} />);
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
        expect(screen.getByText(/You do not have permission to save/)).toBeInTheDocument();
        expect(screen.getByLabelText("Expire workstations after a lease")).toBeDisabled();
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
        await user.click(screen.getByLabelText("Expire workstations after a lease"));
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

    it("keeps focus where Enter sent the save from, and the fields unchangeable, while it is in flight", async () => {
        let answer = () => undefined as void;
        const mutation = {
            isPending: false,
            mutateAsync: jest.fn(() => {
                mutation.isPending = true;
                return new Promise((resolve) => {
                    answer = () => {
                        mutation.isPending = false;
                        resolve({});
                    };
                });
            }),
        };
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue(mutation);
        const user = userEvent.setup({ delay: null });
        render(<SettingsTab can={() => true} />);
        const leaseHours = screen.getByLabelText("Lease hours");
        await user.click(leaseHours);
        await user.keyboard("{Enter}");
        expect(mutation.mutateAsync).toHaveBeenCalledTimes(1);

        // The page has no dialog to restore focus: a disabled field would leave it on the body.
        const fields = [
            screen.getByLabelText("Expire workstations after a lease"),
            leaseHours,
            screen.getByLabelText("Force on all launches"),
            screen.getByLabelText("Grace hours"),
        ];
        for (const field of fields) {
            expect(field).toHaveAttribute("aria-disabled", "true");
            expect(field).not.toBeDisabled();
        }
        expect(leaseHours).toHaveFocus();
        expect(leaseHours).toHaveAttribute("readonly");
        const saving = screen.getByRole("button", { name: "Saving..." });
        expect(saving).toHaveAttribute("aria-disabled", "true");
        expect(saving).not.toBeDisabled();

        await user.type(leaseHours, "0");
        expect(leaseHours).toHaveValue(8);
        await user.click(fields[2]);
        expect(fields[2]).not.toBeChecked();
        await user.keyboard("{Enter}");
        await user.click(saving);
        expect(mutation.mutateAsync).toHaveBeenCalledTimes(1);

        await act(async () => answer());
        // The same button, which the click focused, reads Save again and keeps focus.
        expect(saving).toHaveAccessibleName("Save");
        expect(saving).not.toHaveAttribute("aria-disabled");
        expect(saving).toHaveFocus();
        expect(leaseHours).not.toHaveAttribute("aria-disabled");
    });

    it("groups the lease controls under a described heading, with Save and the last change", () => {
        render(<SettingsTab can={() => true} />);
        const group = screen.getByRole("region", { name: "Workstation leases" });
        expect(group).toHaveAccessibleDescription(
            "One policy for every workstation: how long it runs before its lease expires, and how long it is kept after that."
        );
        expect(within(group).getByLabelText("Expire workstations after a lease")).toBeChecked();
        expect(within(group).getByLabelText("Lease hours")).toBeInTheDocument();
        expect(within(group).getByRole("button", { name: "Save" })).toBeInTheDocument();
        expect(within(group).getByText(/Last changed by admin at/)).toBeInTheDocument();
        // The checkbox says what it switches, so it cannot be taken for the add-on's own switch.
        expect(screen.queryByLabelText("Enabled")).not.toBeInTheDocument();
    });

    it("holds the lease policy alone, with no capacity reservations", () => {
        render(<SettingsTab can={() => true} />);
        expect(screen.queryByText(/capacity reservation/i)).not.toBeInTheDocument();
        expect(screen.getAllByRole("region")).toHaveLength(1);
    });

    it("says the deployment set a policy the system user wrote, on its date", () => {
        (adminQueries.useEdhAdminSettings as jest.Mock).mockReturnValue(
            query({ ...POLICY, updatedBy: SYSTEM_USER_ID })
        );
        render(<SettingsTab can={() => true} />);
        const date = new Date(POLICY.updatedAt).toLocaleDateString();
        expect(screen.getByText(`Set at deployment on ${date}`)).toBeInTheDocument();
        expect(screen.queryByText(/SYSTEM_USER/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Last changed by/)).not.toBeInTheDocument();
    });

    it("names the system user as the backend's seed stores it", () => {
        expect(SYSTEM_USER_ID).toBe("SYSTEM_USER");
        expect(lastChangedText("alice", "not a date")).toBe("Last changed by alice at —");
        expect(lastChangedText(SYSTEM_USER_ID, "not a date")).toBe("Set at deployment on —");
    });
});
