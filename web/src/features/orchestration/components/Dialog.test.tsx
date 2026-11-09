/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dialog, { DialogFooter } from "./Dialog";
import { BUSY_LOOK } from "./BusyControls";

describe("Dialog layout", () => {
    it("defaults to the medium width and offers a large one", () => {
        const { unmount } = render(
            <Dialog open onOpenChange={jest.fn()} title="Medium">
                <p>body</p>
            </Dialog>
        );
        expect(screen.getByRole("dialog").className).toContain("max-w-2xl");
        unmount();

        render(
            <Dialog open onOpenChange={jest.fn()} title="Large" size="lg">
                <p>body</p>
            </Dialog>
        );
        expect(screen.getByRole("dialog").className).toContain("max-w-5xl");
    });

    it("fixes the title and footer and scrolls only the body", () => {
        render(
            <Dialog open onOpenChange={jest.fn()} title="T" footer={<button>Ok</button>}>
                <p>body</p>
            </Dialog>
        );
        const content = screen.getByRole("dialog");
        // The card is a column whose middle region takes the remaining height and scrolls.
        expect(content.className).toContain("flex");
        expect(content.className).toContain("flex-col");
        expect(content.className).toContain("max-h-[85vh]");
        expect(content.className).not.toContain("overflow-auto");
        const body = screen.getByText("body").parentElement as HTMLElement;
        expect(body.className).toContain("overflow-y-auto");
        expect(body.className).toContain("min-h-0");
        expect(screen.getByTestId("dialog-footer")).toContainElement(
            screen.getByRole("button", { name: "Ok" })
        );
    });

    it("renders DialogFooter children in the footer row before the first assertion runs", () => {
        // The slot node is only known after the first commit; holding it in state re-renders the
        // portal in that same commit, so by the time render() returns the child's footer is already
        // in the row. (A ref would leave it one commit late — observable here as a missing button.)
        render(
            <Dialog open onOpenChange={jest.fn()} title="T">
                <DialogFooter>
                    <button>Go</button>
                </DialogFooter>
                <p>body</p>
            </Dialog>
        );
        const go = screen.getByRole("button", { name: "Go" });
        expect(screen.getByTestId("dialog-footer")).toContainElement(go);
        // Not inside the scrolling body.
        expect(screen.getByText("body").parentElement).not.toContainElement(go);
    });

    it("renders DialogFooter as nothing outside a Dialog", () => {
        const { container } = render(
            <DialogFooter>
                <button>Stray</button>
            </DialogFooter>
        );
        expect(container).toBeEmptyDOMElement();
    });
});

describe("Dialog close button", () => {
    it("takes its colour from the theme text tokens", () => {
        render(
            <Dialog open onOpenChange={jest.fn()} title="T">
                <p>body</p>
            </Dialog>
        );
        const close = screen.getByRole("button", { name: "Close dialog" });
        expect(close.className).toContain("text-text-secondary");
        expect(close.className).toContain("hover:text-text-primary");
        expect(close.className).not.toMatch(/text-gray-/);
    });
});

// Radix starts listening for a press outside the dialog one task after it opens.
const outsidePressArmed = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));

describe("Dialog backdrop", () => {
    it("cancels a mouse press on the backdrop, so focus stays in the dialog", () => {
        render(
            <Dialog open onOpenChange={jest.fn()} title="T" footer={<button>Ok</button>}>
                <p>body</p>
            </Dialog>
        );
        // jsdom does not move focus on a mouse press, so the cancelled default is what is asserted.
        expect(fireEvent.mouseDown(screen.getByTestId("dialog-overlay"))).toBe(false);
    });

    it("still closes a dialog that is not held on a click outside", async () => {
        const user = userEvent.setup({ delay: null });
        const onOpenChange = jest.fn();
        render(
            <Dialog open onOpenChange={onOpenChange} title="T">
                <p>body</p>
            </Dialog>
        );
        await outsidePressArmed();
        await user.click(screen.getByTestId("dialog-overlay"));
        expect(onOpenChange).toHaveBeenCalledWith(false);
    });
});

describe("Dialog held while busy", () => {
    const Held: React.FC<{ busy: boolean; onOpenChange: (open: boolean) => void }> = ({
        busy,
        onOpenChange,
    }) => (
        <Dialog
            open
            onOpenChange={onOpenChange}
            title="T"
            busy={busy}
            footer={<button>Deleting...</button>}
        >
            <p>body</p>
        </Dialog>
    );

    it("stays open on Escape, a click outside and its close button, which looks and reads unavailable", async () => {
        const user = userEvent.setup({ delay: null });
        const onOpenChange = jest.fn();
        render(<Held busy onOpenChange={onOpenChange} />);
        await outsidePressArmed();
        const close = screen.getByRole("button", { name: "Close dialog" });
        expect(close).toHaveAttribute("aria-disabled", "true");
        expect(close).not.toBeDisabled();
        expect(close).toHaveClass(...BUSY_LOOK.split(" "));

        screen.getByRole("button", { name: "Deleting..." }).focus();
        await user.keyboard("{Escape}");
        await user.click(screen.getByTestId("dialog-overlay"));
        await user.click(close);
        expect(onOpenChange).not.toHaveBeenCalledWith(false);
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("closes as before once the request is answered", async () => {
        const user = userEvent.setup({ delay: null });
        const onOpenChange = jest.fn();
        const { rerender } = render(<Held busy onOpenChange={onOpenChange} />);
        rerender(<Held busy={false} onOpenChange={onOpenChange} />);
        await outsidePressArmed();
        const close = screen.getByRole("button", { name: "Close dialog" });
        expect(close).not.toHaveAttribute("aria-disabled");
        await user.click(close);
        expect(onOpenChange).toHaveBeenLastCalledWith(false);
        onOpenChange.mockClear();
        await user.keyboard("{Escape}");
        expect(onOpenChange).toHaveBeenLastCalledWith(false);
        onOpenChange.mockClear();
        await user.click(screen.getByTestId("dialog-overlay"));
        expect(onOpenChange).toHaveBeenLastCalledWith(false);
    });
});
