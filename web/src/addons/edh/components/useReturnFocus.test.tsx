/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The element a dialog opened from a controlled `open` returns focus to as it closes: its opener, or
 * the caller's choice, or the opener again when the control chosen cannot take focus.
 */

import React, { useState } from "react";
import { render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dialog from "../../../features/orchestration/components/Dialog";
import { useRemovedRowRef, useReturnFocus, type ReturnFocusTo } from "./useReturnFocus";

interface HarnessProps {
    returnFocusTo?: ReturnFocusTo;
    /** Whether the Refresh control is disabled, as a refresh button is while its list is read. */
    refreshing?: boolean;
}

/** A page with an opener and a Refresh control, and the dialog the opener opens. */
const Harness: React.FC<HarnessProps> = ({ returnFocusTo, refreshing = false }) => {
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
                returnFocusRef={returnFocusRef}
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

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("useReturnFocus", () => {
    it("returns focus to the opener after Escape and after the dialog's own button", async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());

        await user.click(within(await openDialog(user)).getByRole("button", { name: "Done" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
    });

    it("returns focus to the caller's choice, asking only as the dialog closes", async () => {
        const user = userEvent.setup();
        const returnFocusTo = jest.fn(() => refresh());
        render(<Harness returnFocusTo={returnFocusTo} />);
        await openDialog(user);
        expect(returnFocusTo).not.toHaveBeenCalled();
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toHaveFocus());
        expect(returnFocusTo).toHaveBeenCalledWith(screen.getByRole("button", { name: "Open" }));
    });

    it("returns focus to the opener when the caller's choice is disabled", async () => {
        const user = userEvent.setup();
        render(<Harness refreshing returnFocusTo={() => refresh()} />);
        await openDialog(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(screen.getByRole("button", { name: "Open" })).toHaveFocus());
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
