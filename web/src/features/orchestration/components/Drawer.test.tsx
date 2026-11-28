/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Drawer from "./Drawer";
import InfoTooltip from "./InfoTooltip";

describe("Drawer", () => {
    it("closes on Escape when its first tabbable element is a tooltip trigger", async () => {
        // Radix focuses the first tabbable element of a dialog on open. In the quick view that is an
        // info-tooltip trigger, and a Radix tooltip opens on focus — so the tooltip became the
        // highest dismissable layer and took the first Escape, leaving the panel open until a second
        // press. The panel itself receives focus instead, so one Escape closes it.
        const onOpenChange = jest.fn();
        render(
            <Drawer open onOpenChange={onOpenChange} title="Probe">
                <InfoTooltip text="What this list includes" label="More information" />
            </Drawer>
        );
        const dialog = await screen.findByRole("dialog");
        await waitFor(() => expect(document.activeElement).toBe(dialog));
        expect(screen.queryByText("What this list includes")).not.toBeInTheDocument();

        fireEvent.keyDown(dialog, { key: "Escape" });
        expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("keeps focus inside the panel on open", async () => {
        render(
            <Drawer open onOpenChange={jest.fn()} title="Probe">
                <button type="button">first</button>
            </Drawer>
        );
        const dialog = await screen.findByRole("dialog");
        await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    });
});

describe("Drawer focus return", () => {
    /** A page with a row that opens the panel and a control the caller may name instead. */
    const Page: React.FC<{ returnFocusTo?: () => HTMLElement | null }> = ({ returnFocusTo }) => {
        const [open, setOpen] = React.useState(false);
        return (
            <div>
                <button type="button">Row actions</button>
                <button type="button" onClick={() => setOpen(true)}>
                    Row
                </button>
                <Drawer
                    open={open}
                    onOpenChange={setOpen}
                    title="Probe"
                    returnFocusTo={returnFocusTo}
                >
                    <p>details</p>
                </Drawer>
            </div>
        );
    };
    const row = () => screen.getByRole("button", { name: "Row", hidden: true });
    const openPanel = async (user: ReturnType<typeof userEvent.setup>) => {
        row().focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog", { name: "Probe" });
    };
    const closed = () =>
        waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    it("returns focus to the control that opened it after Escape and the close button", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Page />);
        await openPanel(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(row()).toHaveFocus());

        await user.click(
            within(await openPanel(user)).getByRole("button", { name: "Close panel" })
        );
        await closed();
        await waitFor(() => expect(row()).toHaveFocus());
    });

    it("returns focus to returnFocusTo's choice", async () => {
        const user = userEvent.setup({ delay: null });
        render(
            <Page
                returnFocusTo={() =>
                    screen.getByRole("button", { name: "Row actions", hidden: true })
                }
            />
        );
        await openPanel(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Row actions" })).toHaveFocus()
        );
    });
});

describe("Drawer close button", () => {
    it("takes its colour from the theme text tokens", () => {
        render(
            <Drawer open onOpenChange={jest.fn()} title="T">
                <p>body</p>
            </Drawer>
        );
        const close = screen.getByRole("button", { name: "Close panel" });
        expect(close.className).toContain("text-text-secondary");
        expect(close.className).toContain("hover:text-text-primary");
        expect(close.className).not.toMatch(/text-gray-/);
    });
});
