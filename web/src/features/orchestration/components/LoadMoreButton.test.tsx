/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoadMoreButton from "./LoadMoreButton";

/** A paged list: `rows` loaded, Load more while `more` remain. */
const List: React.FC<{
    rows: number;
    more: boolean;
    busy?: boolean;
    focusableRows?: boolean;
    onLoad?: () => void;
}> = ({ rows, more, busy = false, focusableRows = true, onLoad = jest.fn() }) => (
    <>
        <button>Refresh</button>
        <ul>
            {Array.from({ length: rows }, (_, i) => (
                <li key={i}>
                    {focusableRows ? <button>{`Row ${i + 1}`}</button> : `Row ${i + 1}`}
                </li>
            ))}
        </ul>
        {more && <LoadMoreButton onClick={onLoad} busy={busy} />}
        <button>Back to top</button>
    </>
);

// The hand-off waits for the commit that removed the button.
const settle = () => act(() => Promise.resolve());

describe("LoadMoreButton", () => {
    it("keeps focus while the page is read, says so and ignores a second press", async () => {
        const user = userEvent.setup({ delay: null });
        const onLoad = jest.fn();
        const { rerender } = render(<List rows={2} more onLoad={onLoad} />);
        const button = screen.getByRole("button", { name: "Load more" });
        button.focus();
        await user.keyboard("{Enter}");
        expect(onLoad).toHaveBeenCalledTimes(1);

        rerender(<List rows={2} more busy onLoad={onLoad} />);
        expect(button).toHaveAccessibleName("Loading more...");
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).not.toBeDisabled();
        expect(button).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.click(button);
        expect(onLoad).toHaveBeenCalledTimes(1);

        // A page answered with more to come: still focused, live again.
        rerender(<List rows={4} more onLoad={onLoad} />);
        expect(button).toHaveFocus();
        expect(button).toHaveAccessibleName("Load more");
        expect(button).not.toHaveAttribute("aria-disabled");
    });

    it("hands focus to the first row the last page loaded as it leaves", async () => {
        const user = userEvent.setup({ delay: null });
        const { rerender } = render(<List rows={2} more />);
        screen.getByRole("button", { name: "Load more" }).focus();
        await user.keyboard("{Enter}");
        rerender(<List rows={2} more busy />);
        rerender(<List rows={4} more={false} />);
        await settle();
        expect(screen.getByRole("button", { name: "Row 3" })).toHaveFocus();
    });

    it("hands focus to the control after the list when the rows take no focus", async () => {
        const user = userEvent.setup({ delay: null });
        const { rerender } = render(<List rows={2} more focusableRows={false} />);
        screen.getByRole("button", { name: "Load more" }).focus();
        await user.keyboard("{Enter}");
        rerender(<List rows={4} more={false} focusableRows={false} />);
        await settle();
        expect(screen.getByRole("button", { name: "Back to top" })).toHaveFocus();
    });

    it("leaves focus alone when it leaves without focus", async () => {
        const { rerender } = render(<List rows={2} more />);
        screen.getByRole("button", { name: "Row 1" }).focus();
        rerender(<List rows={4} more={false} />);
        await settle();
        expect(screen.getByRole("button", { name: "Row 1" })).toHaveFocus();
    });

    it("stays disabled for another reason, apart from being busy", () => {
        render(<LoadMoreButton onClick={jest.fn()} busy={false} disabled />);
        const button = screen.getByRole("button", { name: "Load more" });
        expect(button).toBeDisabled();
        expect(button).not.toHaveAttribute("aria-disabled");
    });
});
