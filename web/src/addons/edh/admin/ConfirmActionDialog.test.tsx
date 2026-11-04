/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where focus goes when an EDH confirmation closes: back to the element that opened it, after
 * Escape, Cancel, the close button or the caller closing it once the action is answered; to the
 * caller's choice when the opener has left the page; and never to the page body while either is on
 * the page.
 */

import React, { useRef, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfirmActionDialog, {
    controlNamed,
    rowActionsFor,
    rowActionsTrigger,
} from "./ConfirmActionDialog";

interface HarnessProps {
    /** Whether the opener leaves the page as the dialog opens, as a row menu's item does. */
    openerLeaves?: boolean;
    returnFocusTo?: (opener: HTMLElement | null) => HTMLElement | null | undefined;
}

/** An opener, a row trigger to fall back to, and the confirmation the opener opens. */
const Harness: React.FC<HarnessProps> = ({ openerLeaves = false, returnFocusTo }) => {
    const [open, setOpen] = useState(false);
    const listRef = useRef<HTMLDivElement>(null);
    return (
        <div ref={listRef}>
            <button type="button" {...rowActionsFor("row-1")} aria-label="Actions for row 1">
                ⋮
            </button>
            {!(openerLeaves && open) && (
                <button type="button" onClick={() => setOpen(true)}>
                    Open
                </button>
            )}
            {open && (
                <ConfirmActionDialog
                    open
                    title="Stop row 1"
                    message="Stop it?"
                    confirmLabel="Stop"
                    onCancel={() => setOpen(false)}
                    onConfirm={() => setOpen(false)}
                    returnFocusTo={
                        returnFocusTo ??
                        ((opener) => opener ?? rowActionsTrigger(listRef.current, "row-1"))
                    }
                />
            )}
        </div>
    );
};

const openDialog = async (user: ReturnType<typeof userEvent.setup>) => {
    screen.getByRole("button", { name: "Open" }).focus();
    await user.keyboard("{Enter}");
    return screen.findByRole("dialog", { name: "Stop row 1" });
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("ConfirmActionDialog returns focus", () => {
    it("to the opener after Escape", async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
    });

    it("to the opener after Cancel and after the close button", async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await user.click(within(await openDialog(user)).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());

        await user.click(
            within(await openDialog(user)).getByRole("button", { name: "Close dialog" })
        );
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
    });

    it("to the opener when the caller closes it after the action", async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await user.click(within(await openDialog(user)).getByRole("button", { name: "Stop" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
    });

    it("to the caller's element when the opener has left the page", async () => {
        const user = userEvent.setup();
        const returnFocusTo = jest.fn(() =>
            screen.getByRole("button", { name: "Actions for row 1" })
        );
        render(<Harness openerLeaves returnFocusTo={returnFocusTo} />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Actions for row 1" })).toHaveFocus()
        );
        // The opener is gone, so the caller is told so.
        expect(returnFocusTo).toHaveBeenCalledWith(null);
    });

    it("to the caller's element over the opener when the caller names one", async () => {
        const user = userEvent.setup();
        render(
            <Harness
                returnFocusTo={() => screen.getByRole("button", { name: "Actions for row 1" })}
            />
        );
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Actions for row 1" })).toHaveFocus()
        );
    });

    it("to the opener when the caller's element is not on the page", async () => {
        const user = userEvent.setup();
        const detached = document.createElement("button");
        render(<Harness returnFocusTo={() => detached} />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
    });
});

describe("the focus targets", () => {
    it("finds a row's actions trigger by the row id, and a control by its accessible name", () => {
        render(
            <div data-testid="list">
                <button type="button" {...rowActionsFor('a "quoted" id')} aria-label="Actions">
                    ⋮
                </button>
                <input aria-label="Search sessions" />
            </div>
        );
        const list = screen.getByTestId("list");
        expect(rowActionsTrigger(list, 'a "quoted" id')).toBe(
            screen.getByRole("button", { name: "Actions" })
        );
        expect(rowActionsTrigger(list, "another")).toBeNull();
        expect(rowActionsTrigger(null, 'a "quoted" id')).toBeNull();
        expect(controlNamed(list, "Search sessions")).toBe(
            screen.getByRole("textbox", { name: "Search sessions" })
        );
        expect(controlNamed(list, "Search images")).toBeNull();
    });
});
