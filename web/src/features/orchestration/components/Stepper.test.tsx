/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Stepper from "./Stepper";

/**
 * The text `element` shows a sighted user, as a speech-input user would say it: every text node,
 * aria-hidden or not, a space between elements (the badge and the label are separate flex items), and
 * symbols such as the check mark left out, since they are not words (axe's
 * label-content-name-mismatch drops them too).
 */
const shownText = (element: Element) => {
    const text = (node: Node): string =>
        node.nodeType === Node.TEXT_NODE
            ? node.textContent || ""
            : ` ${Array.from(node.childNodes).map(text).join("")} `;
    return text(element)
        .replace(/[\u2190-\u2BFF]/g, "")
        .replace(/\s+/g, " ")
        .trim();
};

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

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

    it("turns only the completed steps into step buttons and jumps on click", async () => {
        const onJumpTo = jest.fn();
        render(<Stepper steps={STEPS} current="tags" onJumpTo={onJumpTo} />);
        const back = screen.getByRole("button", { name: "Basic: go to step 1" });
        expect(screen.queryByRole("button", { name: /Review: go to step 3$/ })).toBeNull();
        expect(
            screen.queryByRole("button", { name: /Tags and Config Body: go to step 2$/ })
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
        const basic = screen.getByRole("button", { name: "Basic: go to step 1" });
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
        expect(screen.getByRole("button", { name: "3 Review: go to step 3" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Basic: go to step 1$/ })).toBeNull();
        expect(
            screen.queryByRole("button", { name: /Tags and Config Body: go to step 2$/ })
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

    it("names each step button starting with the text it shows, then the step it opens (#1592)", () => {
        // Basic is completed (its badge is a check mark) and Review is ahead (its badge is "3").
        render(
            <Stepper steps={STEPS} current="tags" onJumpTo={jest.fn()} canJumpTo={() => true} />
        );
        const buttons = screen.getAllByRole("button");
        expect(buttons.map(shownText)).toEqual(["Basic", "3 Review"]);
        for (const button of buttons) {
            expect(button).toHaveAccessibleName(
                expect.stringMatching(new RegExp(`^${escape(shownText(button))}: go to step \\d$`))
            );
        }
        expect(buttons[0]).toHaveAccessibleName("Basic: go to step 1");
        expect(buttons[1]).toHaveAccessibleName("3 Review: go to step 3");
    });

    it("names the steps ahead of the first one by their numbers and labels (#1592)", () => {
        // The pipeline template wizard: from its first step every later step can be opened, and
        // each shows its number badge beside its label.
        render(
            <Stepper steps={STEPS} current="basic" onJumpTo={jest.fn()} canJumpTo={() => true} />
        );
        const buttons = screen.getAllByRole("button");
        expect(buttons.map(shownText)).toEqual(["2 Tags and Config Body", "3 Review"]);
        expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
            "2 Tags and Config Body: go to step 2",
            "3 Review: go to step 3",
        ]);
        // The number is visible text, so it stays in the accessibility tree; only a check mark is
        // hidden.
        for (const button of buttons) {
            expect(button.querySelector("[aria-hidden='true']")).toBeNull();
        }
    });

    it("keeps a compact jumpable step named by its label", () => {
        render(<Stepper steps={STEPS} current="review" onJumpTo={jest.fn()} />);
        const back = screen.getByRole("button", { name: "Basic: go to step 1" });
        expect(screen.getByText("Basic")).toHaveClass("sr-only");
        expect(back).toContainElement(screen.getByText("Basic"));
    });
});
