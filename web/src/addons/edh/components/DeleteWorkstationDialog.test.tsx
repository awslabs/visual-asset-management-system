/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeleteWorkstationDialog from "./DeleteWorkstationDialog";
import { expectHeldOpen } from "./heldDialogTestSupport";

describe("DeleteWorkstationDialog", () => {
    it("names the workstation and says the action cannot be undone", () => {
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={true}
                open={true}
                onConfirm={jest.fn()}
                onCancel={jest.fn()}
            />
        );
        expect(screen.getByRole("dialog")).toHaveTextContent(
            /Are you sure you want to delete Dev box\?/
        );
        expect(screen.getByRole("dialog")).toHaveTextContent(/cannot be undone/i);
    });

    it("fires onConfirm from the Delete button and onCancel from Cancel", async () => {
        const user = userEvent.setup();
        const onConfirm = jest.fn();
        const onCancel = jest.fn();
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={true}
                open={true}
                onConfirm={onConfirm}
                onCancel={onCancel}
            />
        );

        await user.click(screen.getByRole("button", { name: "Delete" }));
        expect(onConfirm).toHaveBeenCalledTimes(1);

        await user.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it("confirms in the destructive red and cancels with the module's secondary button", () => {
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={true}
                open={true}
                onConfirm={jest.fn()}
                onCancel={jest.fn()}
            />
        );
        expect(screen.getByRole("button", { name: "Delete" })).toHaveClass("bg-red-600");
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveClass("orch-outline");
        expect(screen.getByRole("button", { name: "Cancel" })).not.toHaveClass("bg-gray-200");
    });

    it("keeps the busy Delete button focused and the dialog open while the request is in flight", async () => {
        const user = userEvent.setup({ delay: null });
        const onConfirm = jest.fn();
        const onCancel = jest.fn();
        const props = {
            workstationName: "Dev box",
            hasSession: true,
            open: true,
            onConfirm,
            onCancel,
        };
        const { rerender } = render(<DeleteWorkstationDialog {...props} />);
        screen.getByRole("button", { name: "Delete" }).focus();
        rerender(<DeleteWorkstationDialog {...props} busy />);

        // Focus stays on the button, which says what is running; jsdom keeps focus on a disabled
        // control too, so the attributes carry the check.
        const deleting = screen.getByRole("button", { name: "Deleting..." });
        expect(deleting).toHaveAttribute("aria-disabled", "true");
        expect(deleting).not.toBeDisabled();
        expect(deleting).toHaveFocus();
        const cancel = screen.getByRole("button", { name: "Cancel" });
        expect(cancel).toHaveAttribute("aria-disabled", "true");
        expect(cancel).not.toBeDisabled();

        await user.keyboard("{Enter}");
        await user.click(deleting);
        await user.keyboard("{Escape}");
        await user.click(cancel);
        await user.click(screen.getByRole("button", { name: "Close dialog" }));
        expect(onConfirm).not.toHaveBeenCalled();
        expect(onCancel).not.toHaveBeenCalled();
    });

    it("holds the dialog while the request is in flight: the close button reads unavailable and the backdrop keeps focus", async () => {
        const user = userEvent.setup({ delay: null });
        const onCancel = jest.fn();
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={true}
                open={true}
                busy
                onConfirm={jest.fn()}
                onCancel={onCancel}
            />
        );
        await expectHeldOpen(user, screen.getByRole("dialog", { name: "Delete Dev box" }));
        expect(onCancel).not.toHaveBeenCalled();
    });

    it("says the workstation and its root disk are destroyed when it has a session", () => {
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={true}
                open={true}
                onConfirm={jest.fn()}
                onCancel={jest.fn()}
            />
        );
        const dialog = screen.getByRole("dialog", { name: "Delete Dev box" });
        expect(dialog).toHaveTextContent(
            /Dev box\? The workstation and its root disk are destroyed\. This cannot be undone\./
        );
        expect(dialog).not.toHaveTextContent(/launch record/i);
    });

    it("says the launch record is removed when the workstation has no session", () => {
        render(
            <DeleteWorkstationDialog
                workstationName="Dev box"
                hasSession={false}
                open={true}
                onConfirm={jest.fn()}
                onCancel={jest.fn()}
            />
        );
        const dialog = screen.getByRole("dialog", { name: "Delete Dev box" });
        expect(dialog).toHaveTextContent(
            /Dev box\? This launch has no EDH session; the launch record is removed\. This cannot be undone\./
        );
        expect(dialog).not.toHaveTextContent(/root disk/i);
        expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
    });
});
