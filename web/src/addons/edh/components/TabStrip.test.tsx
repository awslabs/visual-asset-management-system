/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TabStrip from "./TabStrip";

type Key = "a" | "b" | "c";
const TABS: Array<{ key: Key; label: string }> = [
    { key: "a", label: "Alpha" },
    { key: "b", label: "Beta" },
    { key: "c", label: "Gamma" },
];

const strip = (active: Key, onChange: (key: Key) => void) => (
    <TabStrip
        tabs={TABS}
        active={active}
        onChange={onChange}
        ariaLabel="Letters"
        tabId={(key) => `letter-tab-${key}`}
        panelId="letter-panel"
    />
);

describe("TabStrip", () => {
    it("renders a tablist whose selected tab is the only tab stop", () => {
        render(strip("b", jest.fn()));
        expect(screen.getByRole("tablist", { name: "Letters" })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("tabindex", "0");
        expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("aria-selected", "true");
        expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("tabindex", "-1");
        expect(screen.getByRole("tab", { name: "Gamma" })).toHaveAttribute(
            "aria-controls",
            "letter-panel"
        );
        expect(screen.getByRole("tab", { name: "Gamma" })).toHaveAttribute("id", "letter-tab-c");
    });

    it("selects on click", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        render(strip("a", onChange));
        await user.click(screen.getByRole("tab", { name: "Gamma" }));
        expect(onChange).toHaveBeenCalledWith("c");
    });

    it("wraps the arrow keys around the ends and moves focus with the selection", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        const { rerender } = render(strip("c", onChange));

        screen.getByRole("tab", { name: "Gamma" }).focus();
        await user.keyboard("{ArrowRight}");
        expect(onChange).toHaveBeenLastCalledWith("a");
        expect(screen.getByRole("tab", { name: "Alpha" })).toHaveFocus();

        rerender(strip("a", onChange));
        await user.keyboard("{ArrowLeft}");
        expect(onChange).toHaveBeenLastCalledWith("c");
        expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
    });

    it("jumps to the first and last tab on Home and End", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        const { rerender } = render(strip("b", onChange));

        screen.getByRole("tab", { name: "Beta" }).focus();
        await user.keyboard("{End}");
        expect(onChange).toHaveBeenLastCalledWith("c");
        expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();

        rerender(strip("c", onChange));
        await user.keyboard("{Home}");
        expect(onChange).toHaveBeenLastCalledWith("a");
        expect(screen.getByRole("tab", { name: "Alpha" })).toHaveFocus();
    });

    it("ignores keys that are not tab navigation", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        render(strip("a", onChange));
        screen.getByRole("tab", { name: "Alpha" }).focus();
        await user.keyboard("{ArrowDown}");
        expect(onChange).not.toHaveBeenCalled();
    });
});
