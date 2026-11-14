/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RefreshButton from "./RefreshButton";

describe("RefreshButton", () => {
    it("keeps focus and its name while the list is read, says it is busy and ignores a press", async () => {
        const user = userEvent.setup({ delay: null });
        const onClick = jest.fn();
        const { rerender } = render(
            <RefreshButton onClick={onClick} ariaLabel="Refresh sessions" />
        );
        const button = screen.getByRole("button", { name: "Refresh sessions" });
        button.focus();
        await user.keyboard("{Enter}");
        expect(onClick).toHaveBeenCalledTimes(1);

        rerender(<RefreshButton onClick={onClick} busy ariaLabel="Refresh sessions" />);
        expect(button).not.toBeDisabled();
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).toHaveAttribute("aria-busy", "true");
        expect(button).toHaveAccessibleName("Refresh sessions");
        expect(button).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard(" ");
        await user.click(button);
        expect(onClick).toHaveBeenCalledTimes(1);

        // Answered: still focused, live again.
        rerender(<RefreshButton onClick={onClick} ariaLabel="Refresh sessions" />);
        expect(button).toHaveFocus();
        expect(button).not.toHaveAttribute("aria-disabled");
        expect(button).not.toHaveAttribute("aria-busy");
        await user.keyboard("{Enter}");
        expect(onClick).toHaveBeenCalledTimes(2);
    });
});
