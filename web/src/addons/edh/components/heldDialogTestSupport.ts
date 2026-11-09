/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Test support: the assertions every EDH dialog held open by the shared Dialog's `busy` state
 * shares. Imported by the module's tests only.
 */

import { fireEvent, screen, within } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";
import { BUSY_LOOK } from "../../../features/orchestration/components/BusyControls";

/**
 * Asserts that `dialog` is held open while its request is in flight: its close button carries
 * `aria-disabled="true"` and the busy look, and a click on it leaves the dialog open; a mouse
 * press on the backdrop is cancelled, so it never moves focus to the page body. With a dialog
 * opened from another one, the backdrop checked is the topmost, the held dialog's own.
 */
export async function expectHeldOpen(
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement
): Promise<void> {
    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    expect(close).toHaveAttribute("aria-disabled", "true");
    expect(close).not.toBeDisabled();
    expect(close.className).toContain(BUSY_LOOK);
    await user.click(close);
    expect(dialog).toBeInTheDocument();
    const overlays = screen.getAllByTestId("dialog-overlay");
    expect(fireEvent.mouseDown(overlays[overlays.length - 1])).toBe(false);
    expect(dialog).toBeInTheDocument();
}
