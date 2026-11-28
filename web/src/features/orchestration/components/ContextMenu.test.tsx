/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ContextMenu, { type ContextMenuItem } from "./ContextMenu";
import RowActionsButton from "./RowActionsButton";

// The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
const openMenu = async () => {
    screen.getByRole("button", { name: "Actions" }).focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("menu");
};

const renderAndOpen = async (
    user: ReturnType<typeof userEvent.setup>,
    items: ContextMenuItem[]
) => {
    render(<ContextMenu items={items} trigger={<button aria-label="Actions">⋮</button>} />);
    screen.getByLabelText("Actions").focus();
    await user.keyboard("{Enter}");
    return screen.findAllByRole("menuitem");
};

const REASON = "Disabled workflows cannot be executed";

describe("ContextMenu", () => {
    it("renders the visible items by label, with no description when none carries a hint", async () => {
        const user = userEvent.setup();
        const onSelect = jest.fn();
        const items = await renderAndOpen(user, [
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
        await renderAndOpen(user, [
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
        await renderAndOpen(user, [
            { label: "Delete", onSelect: jest.fn(), disabled: true, hint: "Not yet." },
            { label: "Stop", onSelect: jest.fn(), hint: "Stops it." },
        ]);
        expect(screen.getByText("Not yet.")).toHaveClass("text-text-disabled");
        expect(screen.getByText("Not yet.")).not.toHaveClass("text-text-secondary");
        expect(screen.getByText("Stops it.")).toHaveClass("text-text-secondary");
    });
});

describe("ContextMenu disabledReason", () => {
    it("explains a disabled item through its aria-description", async () => {
        render(
            <ContextMenu
                trigger={<RowActionsButton aria-label="Actions" />}
                items={[
                    {
                        label: "Execute",
                        onSelect: jest.fn(),
                        disabled: true,
                        disabledReason: REASON,
                    },
                    { label: "Edit", onSelect: jest.fn(), disabledReason: "never shown" },
                ]}
            />
        );
        await openMenu();
        const execute = screen.getByRole("menuitem", { name: "Execute" });
        expect(execute).toHaveAttribute("aria-disabled", "true");
        expect(execute).toHaveAttribute("aria-description", REASON);
        // A reason only qualifies a disabled item; an enabled item carries none.
        const edit = screen.getByRole("menuitem", { name: "Edit" });
        expect(edit).not.toHaveAttribute("aria-disabled");
        expect(edit).not.toHaveAttribute("aria-description");
    });

    it("keeps a disabled item with a reason in the keyboard order and shows the reason on focus", async () => {
        const onSelect = jest.fn();
        render(
            <ContextMenu
                trigger={<RowActionsButton aria-label="Actions" />}
                items={[
                    { label: "View", onSelect: jest.fn() },
                    { label: "Archive", onSelect, disabled: true, disabledReason: REASON },
                ]}
            />
        );
        await openMenu();
        await userEvent.keyboard("{ArrowDown}");
        const archive = screen.getByRole("menuitem", { name: "Archive" });
        expect(archive).toHaveFocus();
        expect(await screen.findByRole("tooltip")).toHaveTextContent(REASON);

        // Activating it runs nothing and leaves the menu open on the item.
        await userEvent.keyboard("{Enter}");
        expect(onSelect).not.toHaveBeenCalled();
        expect(screen.getByRole("menu")).toBeInTheDocument();
    });

    it("shows the reason on hover and runs nothing on click", async () => {
        const onSelect = jest.fn();
        render(
            <ContextMenu
                trigger={<RowActionsButton aria-label="Actions" />}
                items={[{ label: "Archive", onSelect, disabled: true, disabledReason: REASON }]}
            />
        );
        await openMenu();
        const archive = screen.getByRole("menuitem", { name: "Archive" });
        await userEvent.hover(archive);
        expect(await screen.findByRole("tooltip")).toHaveTextContent(REASON);
        await userEvent.click(archive);
        expect(onSelect).not.toHaveBeenCalled();
    });

    it("skips a disabled item without a reason, as before", async () => {
        render(
            <ContextMenu
                trigger={<RowActionsButton aria-label="Actions" />}
                items={[
                    { label: "View", onSelect: jest.fn() },
                    { label: "Abort", onSelect: jest.fn(), disabled: true },
                    { label: "Rerun", onSelect: jest.fn() },
                ]}
            />
        );
        await openMenu();
        await userEvent.keyboard("{ArrowDown}");
        expect(screen.getByRole("menuitem", { name: "Rerun" })).toHaveFocus();
        expect(screen.getByRole("menuitem", { name: "Abort" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    });
});
