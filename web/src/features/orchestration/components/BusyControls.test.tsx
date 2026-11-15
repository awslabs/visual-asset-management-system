/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A busy control keeps focus: it is `aria-disabled`, not `disabled` (the browser moves focus
 * from a control that becomes disabled to the page body), ignores what would send its request
 * again, and names the action under way. jsdom keeps focus on a disabled control, so the tests
 * assert the attributes and the focus directly.
 */

import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BUSY_LOOK, BusyButton, BusyInput, openWhileBusy } from "./BusyControls";

describe("BusyButton", () => {
    it("keeps focus as it turns busy, says so, and ignores a click, Enter and Space", async () => {
        const user = userEvent.setup({ delay: null });
        const onClick = jest.fn();
        const { rerender } = render(
            <BusyButton busy={false} busyLabel="Deleting..." onClick={onClick}>
                Delete
            </BusyButton>
        );
        const button = screen.getByRole("button", { name: "Delete" });
        button.focus();
        await user.keyboard("{Enter}");
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(button).not.toHaveAttribute("aria-disabled");

        rerender(
            <BusyButton busy busyLabel="Deleting..." onClick={onClick}>
                Delete
            </BusyButton>
        );
        expect(button).toHaveAccessibleName("Deleting...");
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).not.toBeDisabled();
        expect(button).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard(" ");
        await user.click(button);
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(button).toHaveFocus();
    });

    it("looks disabled while busy and keeps its label without a busy label", () => {
        render(
            <BusyButton busy className="px-4">
                Cancel
            </BusyButton>
        );
        const button = screen.getByRole("button", { name: "Cancel" });
        expect(button).toHaveClass("px-4", ...BUSY_LOOK.split(" "));
        expect(button).toHaveAttribute("type", "button");
    });

    it("stays disabled for another reason, apart from being busy", () => {
        render(
            <BusyButton busy={false} disabled>
                Save
            </BusyButton>
        );
        const button = screen.getByRole("button", { name: "Save" });
        expect(button).toBeDisabled();
        expect(button).not.toHaveAttribute("aria-disabled");
    });

    it("submits its form when idle and nothing while busy, clicked or through Enter in a field", async () => {
        const user = userEvent.setup({ delay: null });
        const onSubmit = jest.fn((e: React.FormEvent) => e.preventDefault());
        const Form: React.FC<{ busy: boolean }> = ({ busy }) => (
            <form onSubmit={onSubmit}>
                <input aria-label="Hours" />
                <BusyButton type="submit" busy={busy} busyLabel="Saving...">
                    Save
                </BusyButton>
            </form>
        );
        const { rerender } = render(<Form busy={false} />);
        await user.click(screen.getByRole("button", { name: "Save" }));
        expect(onSubmit).toHaveBeenCalledTimes(1);

        rerender(<Form busy />);
        await user.click(screen.getByRole("button", { name: "Saving..." }));
        await user.type(screen.getByLabelText("Hours"), "8{Enter}");
        expect(onSubmit).toHaveBeenCalledTimes(1);
    });
});

describe("BusyInput", () => {
    const Fields: React.FC<{ busy: boolean; disabled?: boolean }> = ({ busy, disabled }) => {
        const [hours, setHours] = useState("8");
        const [on, setOn] = useState(false);
        return (
            <>
                <BusyInput
                    aria-label="Hours"
                    value={hours}
                    onChange={(e) => setHours(e.target.value)}
                    busy={busy}
                    disabled={disabled}
                />
                <BusyInput
                    type="checkbox"
                    aria-label="Forced"
                    checked={on}
                    onChange={(e) => setOn(e.target.checked)}
                    busy={busy}
                    disabled={disabled}
                />
            </>
        );
    };

    it("keeps focus and its value while busy, a checkbox its state", async () => {
        const user = userEvent.setup({ delay: null });
        const { rerender } = render(<Fields busy={false} />);
        const hours = screen.getByLabelText("Hours");
        const forced = screen.getByLabelText("Forced");
        hours.focus();
        rerender(<Fields busy />);

        for (const field of [hours, forced]) {
            expect(field).toHaveAttribute("aria-disabled", "true");
            expect(field).not.toBeDisabled();
            expect(field).toHaveClass(...BUSY_LOOK.split(" "));
        }
        expect(hours).toHaveFocus();
        expect(hours).toHaveAttribute("readonly");
        await user.type(hours, "0");
        expect(hours).toHaveValue("8");
        await user.click(forced);
        expect(forced).not.toBeChecked();
        // A change that reaches the field anyway is dropped.
        fireEvent.change(hours, { target: { value: "9" } });
        expect(hours).toHaveValue("8");

        rerender(<Fields busy={false} />);
        await user.click(forced);
        expect(forced).toBeChecked();
        expect(hours).not.toHaveAttribute("readonly");
        expect(hours).not.toHaveAttribute("aria-disabled");
    });

    it("stays disabled for another reason, apart from being busy", () => {
        render(<Fields busy={false} disabled />);
        expect(screen.getByLabelText("Hours")).toBeDisabled();
        expect(screen.getByLabelText("Forced")).toBeDisabled();
        expect(screen.getByLabelText("Hours")).not.toHaveAttribute("aria-disabled");
    });
});

describe("openWhileBusy", () => {
    it("drops a close while busy and passes every change otherwise", () => {
        const onOpenChange = jest.fn();
        openWhileBusy(true, onOpenChange)(false);
        expect(onOpenChange).not.toHaveBeenCalled();
        openWhileBusy(true, onOpenChange)(true);
        expect(onOpenChange).toHaveBeenLastCalledWith(true);
        openWhileBusy(false, onOpenChange)(false);
        expect(onOpenChange).toHaveBeenLastCalledWith(false);
    });
});
