/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The element a dialog opened from a controlled `open` returns focus to as it closes: its opener, or
 * the caller's choice, or the opener again when the control chosen cannot take focus. The shared
 * Dialog does this itself for a caller that passes no `returnFocusRef`, however it closes.
 */

import React, { useRef, useState } from "react";
import { render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dialog from "./Dialog";
import {
    controlNamed,
    rowActionsFor,
    rowActionsTrigger,
    useRemovedRowRef,
    useReturnFocus,
    type ReturnFocusTo,
} from "./useReturnFocus";

interface HarnessProps {
    returnFocusTo?: ReturnFocusTo;
    /** Whether the Refresh control is disabled. */
    refreshing?: boolean;
    /** How the dialog is given its return target: through the hook, or left to the Dialog. */
    via?: "hook" | "dialog";
}

/** A page with an opener and a Refresh control, and the dialog the opener opens. */
const Harness: React.FC<HarnessProps> = ({ returnFocusTo, refreshing = false, via = "hook" }) => {
    const [open, setOpen] = useState(false);
    const returnFocusRef = useReturnFocus(open, returnFocusTo);
    return (
        <div>
            <button type="button" disabled={refreshing}>
                Refresh
            </button>
            <button type="button" onClick={() => setOpen(true)}>
                Open
            </button>
            <Dialog
                open={open}
                onOpenChange={setOpen}
                title="Edit row 1"
                {...(via === "hook" ? { returnFocusRef } : { returnFocusTo })}
                footer={
                    <button type="button" onClick={() => setOpen(false)}>
                        Done
                    </button>
                }
            >
                <p>Edit it.</p>
            </Dialog>
        </div>
    );
};

const openDialog = async (user: ReturnType<typeof userEvent.setup>) => {
    screen.getByRole("button", { name: "Open" }).focus();
    await user.keyboard("{Enter}");
    return screen.findByRole("dialog", { name: "Edit row 1" });
};

// Hidden included: the page is hidden from the accessibility tree while the dialog is open.
const refresh = () => screen.getByRole("button", { name: "Refresh", hidden: true });
const opener = () => screen.getByRole("button", { name: "Open", hidden: true });

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("useReturnFocus", () => {
    it("returns focus to the opener after Escape and after the dialog's own button", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Harness />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());

        await user.click(within(await openDialog(user)).getByRole("button", { name: "Done" }));
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());
    });

    it("returns focus to the caller's choice, asking only as the dialog closes", async () => {
        const user = userEvent.setup({ delay: null });
        const returnFocusTo = jest.fn(() => refresh());
        render(<Harness returnFocusTo={returnFocusTo} />);
        await openDialog(user);
        expect(returnFocusTo).not.toHaveBeenCalled();
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(refresh()).toHaveFocus());
        expect(returnFocusTo).toHaveBeenCalledWith(opener());
    });

    it("returns focus to the opener when the caller's choice is disabled", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Harness refreshing returnFocusTo={() => refresh()} />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());
    });
});

describe("Dialog without a returnFocusRef", () => {
    it("returns focus to its opener after Escape, its own button and the close button", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Harness via="dialog" />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());

        await user.click(within(await openDialog(user)).getByRole("button", { name: "Done" }));
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());

        await user.click(
            within(await openDialog(user)).getByRole("button", { name: "Close dialog" })
        );
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());
    });

    it("returns focus to returnFocusTo's choice", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Harness via="dialog" returnFocusTo={() => refresh()} />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(refresh()).toHaveFocus());
    });

    it("returns focus to the opener when the caller closes it after unmounting it", async () => {
        // The caller answers the action and drops the dialog from the tree in the same step, as the
        // row-menu confirmations do.
        const Conditional: React.FC = () => {
            const [open, setOpen] = useState(false);
            return (
                <div>
                    <button type="button" onClick={() => setOpen(true)}>
                        Open
                    </button>
                    {open && (
                        <Dialog
                            open
                            onOpenChange={setOpen}
                            title="Edit row 1"
                            footer={
                                <button type="button" onClick={() => setOpen(false)}>
                                    Done
                                </button>
                            }
                        >
                            <p>Edit it.</p>
                        </Dialog>
                    )}
                </div>
            );
        };
        const user = userEvent.setup({ delay: null });
        render(<Conditional />);
        await user.click(within(await openDialog(user)).getByRole("button", { name: "Done" }));
        await closed();
        await waitFor(() => expect(opener()).toHaveFocus());
    });

    it("leaves a caller's returnFocusRef in charge", async () => {
        const WithRef: React.FC = () => {
            const [open, setOpen] = useState(false);
            const targetRef = useRef<HTMLButtonElement>(null);
            return (
                <div>
                    <button type="button" ref={targetRef}>
                        Target
                    </button>
                    <button type="button" onClick={() => setOpen(true)}>
                        Open
                    </button>
                    <Dialog
                        open={open}
                        onOpenChange={setOpen}
                        title="Edit row 1"
                        returnFocusRef={targetRef}
                        returnFocusTo={() => opener()}
                    >
                        <p>Edit it.</p>
                    </Dialog>
                </div>
            );
        };
        const user = userEvent.setup({ delay: null });
        render(<WithRef />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Target" })).toHaveFocus());
    });
});

describe("useRemovedRowRef", () => {
    it("keeps the note as the dialog closes and clears it when a dialog opens on a row", () => {
        const { result, rerender } = renderHook(
            ({ target }: { target: string | null }) => useRemovedRowRef<string>(target),
            { initialProps: { target: "row-1" as string | null } }
        );
        // The action is answered, the row may have left the list, and the dialog closes.
        result.current.current = "row-1";
        rerender({ target: null });
        expect(result.current.current).toBe("row-1");

        // The row is still listed and its dialog opens again.
        rerender({ target: "row-1" });
        expect(result.current.current).toBeNull();
    });
});

describe("row helpers", () => {
    it("find a row's actions trigger by its id and a control by its exact name", () => {
        render(
            <div data-testid="list">
                <button type="button" {...rowActionsFor('a "quoted" id')} aria-label="Actions">
                    ...
                </button>
                <input aria-label="Search" />
                <input aria-label="Search sessions" />
            </div>
        );
        const list = screen.getByTestId("list");
        expect(rowActionsTrigger(list, 'a "quoted" id')).toBe(
            screen.getByRole("button", { name: "Actions" })
        );
        expect(rowActionsTrigger(list, "another")).toBeNull();
        expect(rowActionsTrigger(null, 'a "quoted" id')).toBeNull();
        expect(controlNamed(list, "Search")).toBe(screen.getByRole("textbox", { name: "Search" }));
        expect(controlNamed(list, "Search images")).toBeNull();
    });
});
