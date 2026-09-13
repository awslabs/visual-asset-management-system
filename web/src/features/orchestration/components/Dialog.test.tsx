/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import Dialog, { DialogFooter } from "./Dialog";

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
