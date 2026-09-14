/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
