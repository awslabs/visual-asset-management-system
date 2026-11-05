/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Stepper from "./Stepper";

const STEPS = [
    { id: "basic", label: "Basic" },
    { id: "tags", label: "Tags and Config Body" },
    { id: "review", label: "Review" },
];

describe("Stepper", () => {
    it("renders every step inert and marks the current one when no jump handler is given", () => {
        render(<Stepper steps={STEPS} current="tags" />);
        expect(screen.queryAllByRole("button")).toHaveLength(0);
        const current = screen.getByText("Tags and Config Body").closest("[aria-current='step']");
        expect(current).not.toBeNull();
        expect(screen.getByText("✓")).toBeInTheDocument(); // the completed step's badge
        expect(screen.getByText("3")).toBeInTheDocument(); // the upcoming step's number
    });

    it("turns only the completed steps into 'Go to step' buttons and jumps on click", async () => {
        const onJumpTo = jest.fn();
        render(<Stepper steps={STEPS} current="tags" onJumpTo={onJumpTo} />);
        const back = screen.getByRole("button", { name: "Go to step Basic" });
        expect(screen.queryByRole("button", { name: "Go to step Review" })).toBeNull();
        expect(
            screen.queryByRole("button", { name: "Go to step Tags and Config Body" })
        ).toBeNull();
        await userEvent.click(back);
        expect(onJumpTo).toHaveBeenCalledWith("basic");
    });

    it("holds its step buttons while busy: focusable, aria-disabled, and a press does not jump", async () => {
        const user = userEvent.setup({ delay: null });
        const onJumpTo = jest.fn();
        const { rerender } = render(
            <Stepper steps={STEPS} current="review" onJumpTo={onJumpTo} busy />
        );
        const basic = screen.getByRole("button", { name: "Go to step Basic" });
        expect(basic).toHaveAttribute("aria-disabled", "true");
        expect(basic).not.toBeDisabled();
        await user.click(basic);
        basic.focus();
        await user.keyboard("{Enter}");
        expect(basic).toHaveFocus();
        expect(onJumpTo).not.toHaveBeenCalled();

        rerender(<Stepper steps={STEPS} current="review" onJumpTo={onJumpTo} />);
        expect(basic).not.toHaveAttribute("aria-disabled");
        await user.keyboard("{Enter}");
        expect(onJumpTo).toHaveBeenCalledWith("basic");
    });

    it("lets canJumpTo widen or narrow the reachable steps, never including the current one", () => {
        const onJumpTo = jest.fn();
        render(
            <Stepper
                steps={STEPS}
                current="basic"
                onJumpTo={onJumpTo}
                canJumpTo={(id) => id === "review"}
            />
        );
        expect(screen.getByRole("button", { name: "Go to step Review" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Go to step Basic" })).toBeNull();
        expect(
            screen.queryByRole("button", { name: "Go to step Tags and Config Body" })
        ).toBeNull();
    });

    it("shows only the current step's label below sm and wraps instead of widening the page", () => {
        // jsdom has no layout or media queries, so the responsive classes are the contract.
        const { container } = render(<Stepper steps={STEPS} current="tags" />);
        expect(container.firstElementChild).toHaveClass("flex-wrap", "min-w-0");

        const current = screen.getByText("Tags and Config Body");
        expect(current).not.toHaveClass("sr-only");
        expect(current.closest("[aria-current='step']")).not.toBeNull();

        // The other labels stay in the accessibility tree, visible again from sm up.
        for (const name of ["Basic", "Review"]) {
            expect(screen.getByText(name)).toHaveClass("sr-only", "sm:not-sr-only");
        }
    });

    it("keeps a compact jumpable step named by its label", () => {
        render(<Stepper steps={STEPS} current="review" onJumpTo={jest.fn()} />);
        const back = screen.getByRole("button", { name: "Go to step Basic" });
        expect(screen.getByText("Basic")).toHaveClass("sr-only");
        expect(back).toContainElement(screen.getByText("Basic"));
    });
});
