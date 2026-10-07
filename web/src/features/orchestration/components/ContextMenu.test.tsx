/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ContextMenu from "./ContextMenu";

// The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
const openMenu = async () => {
    screen.getByRole("button", { name: "Actions" }).focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("menu");
};

describe("ContextMenu disabledReason", () => {
    it("explains a disabled item through its title and aria-description", async () => {
        render(
            <ContextMenu
                trigger={<button aria-label="Actions">⋮</button>}
                items={[
                    {
                        label: "Execute",
                        onSelect: jest.fn(),
                        disabled: true,
                        disabledReason: "Disabled workflows cannot be executed",
                    },
                    { label: "Edit", onSelect: jest.fn(), disabledReason: "never shown" },
                ]}
            />
        );
        await openMenu();
        const execute = screen.getByRole("menuitem", { name: "Execute" });
        expect(execute).toHaveAttribute("aria-disabled", "true");
        expect(execute).toHaveAttribute("title", "Disabled workflows cannot be executed");
        expect(execute).toHaveAttribute(
            "aria-description",
            "Disabled workflows cannot be executed"
        );
        // A reason only qualifies a disabled item; an enabled item carries none.
        const edit = screen.getByRole("menuitem", { name: "Edit" });
        expect(edit).not.toHaveAttribute("title");
        expect(edit).not.toHaveAttribute("aria-description");
    });
});
