/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ContextMenu, { type ContextMenuItem } from "./ContextMenu";

const openMenu = async (user: ReturnType<typeof userEvent.setup>, items: ContextMenuItem[]) => {
    render(<ContextMenu items={items} trigger={<button aria-label="Actions">⋮</button>} />);
    // A Radix dropdown opens on keyboard activation in jsdom.
    screen.getByLabelText("Actions").focus();
    await user.keyboard("{Enter}");
    return screen.findAllByRole("menuitem");
};

describe("ContextMenu", () => {
    it("renders the visible items by label, with no description when none carries a hint", async () => {
        const user = userEvent.setup();
        const onSelect = jest.fn();
        const items = await openMenu(user, [
            { label: "Edit", onSelect },
            { label: "Archive", onSelect: jest.fn(), danger: true },
            { label: "Hidden", onSelect: jest.fn(), hidden: true },
        ]);
        expect(items.map((item) => item.textContent)).toEqual(["Edit", "Archive"]);
        const edit = screen.getByRole("menuitem", { name: "Edit" });
        expect(edit).not.toHaveAttribute("aria-describedby");
        expect(edit).not.toHaveAttribute("aria-label");
        await user.click(edit);
        expect(onSelect).toHaveBeenCalledTimes(1);
    });

    it("shows a hint as secondary text and as the item's description, keeping the label as its name", async () => {
        const user = userEvent.setup();
        await openMenu(user, [
            {
                label: "Delete",
                onSelect: jest.fn(),
                danger: true,
                disabled: true,
                hint: "Available once the launch has finished.",
            },
            { label: "Stop", onSelect: jest.fn() },
        ]);
        const remove = screen.getByRole("menuitem", { name: "Delete" });
        expect(remove).toHaveAttribute("aria-disabled", "true");
        expect(remove).toHaveAccessibleDescription("Available once the launch has finished.");
        expect(screen.getByText("Available once the launch has finished.")).toBeVisible();
        expect(screen.getByRole("menuitem", { name: "Stop" })).not.toHaveAttribute(
            "aria-describedby"
        );
    });

    it("colours a hint like the item it explains: muted on a disabled item, secondary on an enabled one", async () => {
        const user = userEvent.setup();
        await openMenu(user, [
            { label: "Delete", onSelect: jest.fn(), disabled: true, hint: "Not yet." },
            { label: "Stop", onSelect: jest.fn(), hint: "Stops it." },
        ]);
        expect(screen.getByText("Not yet.")).toHaveClass("text-text-disabled");
        expect(screen.getByText("Not yet.")).not.toHaveClass("text-text-secondary");
        expect(screen.getByText("Stops it.")).toHaveClass("text-text-secondary");
    });
});
