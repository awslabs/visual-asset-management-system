/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RowActionsButton from "./RowActionsButton";
import ContextMenu from "./ContextMenu";

// The vertical and horizontal ellipsis glyphs, which render blank without a font that carries them.
const DOT_GLYPHS = /[\u22ee\u22ef]/;

const circleCentres = (button: HTMLElement) =>
    Array.from(button.querySelectorAll("circle")).map((c) => [
        c.getAttribute("cx"),
        c.getAttribute("cy"),
    ]);

describe("RowActionsButton", () => {
    it("draws the dots as a hidden SVG in currentColor, with no text", () => {
        render(<RowActionsButton aria-label="Actions" />);
        const button = screen.getByRole("button", { name: "Actions" });
        const svg = button.querySelector("svg");
        expect(svg).not.toBeNull();
        expect(svg).toHaveAttribute("aria-hidden", "true");
        expect(svg).toHaveAttribute("fill", "currentColor");
        expect(svg).toHaveAttribute("width", "1em");
        expect(svg).toHaveAttribute("height", "1em");
        expect(button.textContent).toBe("");
        expect(button.textContent).not.toMatch(DOT_GLYPHS);
    });

    it("stacks the dots vertically by default and lays them out horizontally on request", () => {
        const { rerender } = render(<RowActionsButton aria-label="Actions" />);
        expect(circleCentres(screen.getByRole("button"))).toEqual([
            ["8", "3"],
            ["8", "8"],
            ["8", "13"],
        ]);
        rerender(<RowActionsButton aria-label="Actions" orientation="horizontal" />);
        expect(circleCentres(screen.getByRole("button"))).toEqual([
            ["3", "8"],
            ["8", "8"],
            ["13", "8"],
        ]);
    });

    it("forwards its ref and passes its props to the button", () => {
        const ref = React.createRef<HTMLButtonElement>();
        const onClick = jest.fn();
        render(
            <RowActionsButton
                ref={ref}
                aria-label="Actions"
                className="px-2 text-lg"
                onClick={onClick}
            />
        );
        const button = screen.getByRole("button", { name: "Actions" });
        expect(ref.current).toBe(button);
        expect(button).toHaveClass("px-2", "text-lg");
        // A button inside a form must not submit it.
        expect(button).toHaveAttribute("type", "button");
        button.click();
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    describe("as a ContextMenu trigger", () => {
        const renderMenu = () =>
            render(
                <ContextMenu
                    trigger={<RowActionsButton aria-label="Actions" />}
                    items={[
                        { label: "Edit", onSelect: jest.fn() },
                        { label: "Archive", onSelect: jest.fn() },
                    ]}
                />
            );

        it("opens the menu on click", async () => {
            renderMenu();
            const button = screen.getByRole("button", { name: "Actions" });
            expect(button).toHaveAttribute("aria-expanded", "false");
            await userEvent.click(button);
            expect(await screen.findByRole("menu")).toBeInTheDocument();
            expect(button).toHaveAttribute("aria-expanded", "true");
            expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
        });

        it("opens the menu from the keyboard and returns focus to the button on Escape", async () => {
            renderMenu();
            const button = screen.getByRole("button", { name: "Actions" });
            button.focus();
            await userEvent.keyboard("{Enter}");
            expect(await screen.findByRole("menu")).toBeInTheDocument();
            await userEvent.keyboard("{Escape}");
            expect(screen.queryByRole("menu")).not.toBeInTheDocument();
            // Radix restores focus through the trigger ref, so this holds only if the ref is forwarded.
            expect(button).toHaveFocus();
        });
    });
});
