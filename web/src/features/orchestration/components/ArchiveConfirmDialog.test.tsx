/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArchiveConfirmDialog from "./ArchiveConfirmDialog";

describe("ArchiveConfirmDialog", () => {
    it("holds while the archive runs: Archive keeps focus and says so, nothing closes it or sends it again", async () => {
        const user = userEvent.setup({ delay: null });
        const onConfirm = jest.fn();
        const onCancel = jest.fn();
        const { rerender } = render(
            <ArchiveConfirmDialog
                entityName="Pipeline One"
                open
                onConfirm={onConfirm}
                onCancel={onCancel}
            />
        );
        const archive = screen.getByRole("button", { name: "Archive" });
        archive.focus();
        await user.keyboard("{Enter}");
        expect(onConfirm).toHaveBeenCalledTimes(1);

        rerender(
            <ArchiveConfirmDialog
                entityName="Pipeline One"
                open
                busy
                onConfirm={onConfirm}
                onCancel={onCancel}
            />
        );
        expect(archive).toHaveAccessibleName("Archiving...");
        expect(archive).toHaveAttribute("aria-disabled", "true");
        expect(archive).not.toBeDisabled();
        expect(archive).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        const cancel = screen.getByRole("button", { name: "Cancel" });
        expect(cancel).toHaveAttribute("aria-disabled", "true");
        await user.click(cancel);
        await user.click(screen.getByRole("button", { name: "Close dialog" }));
        expect(onConfirm).toHaveBeenCalledTimes(1);
        expect(onCancel).not.toHaveBeenCalled();
        expect(screen.getByRole("dialog")).toBeInTheDocument();

        // Answered (here with a failure the page reports): Escape and Cancel close it as before.
        rerender(
            <ArchiveConfirmDialog
                entityName="Pipeline One"
                open
                onConfirm={onConfirm}
                onCancel={onCancel}
            />
        );
        expect(archive).toHaveAccessibleName("Archive");
        await user.keyboard("{Escape}");
        expect(onCancel).toHaveBeenCalledTimes(1);
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onCancel).toHaveBeenCalledTimes(2);
    });
});
