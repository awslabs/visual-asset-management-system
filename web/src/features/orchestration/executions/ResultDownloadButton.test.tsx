/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResultDownloadButton } from "./ResultDownloadButton";

describe("ResultDownloadButton", () => {
    it("keeps focus while the result is read, says so and ignores a second press", async () => {
        const user = userEvent.setup({ delay: null });
        let fail: (reason: Error) => void = jest.fn();
        const readFullText = jest.fn(
            () =>
                new Promise<string>((_resolve, reject) => {
                    fail = reject;
                })
        );
        render(
            <ResultDownloadButton
                executionId="exec-1"
                readFullText={readFullText}
                className="px-3"
                testId="download"
            />
        );
        const button = screen.getByTestId("download");
        button.focus();
        await user.keyboard("{Enter}");
        expect(button).toHaveAccessibleName("Downloading…");
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).not.toBeDisabled();
        expect(button).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.click(button);
        expect(readFullText).toHaveBeenCalledTimes(1);

        // Answered (here with a failure): focus stays on the button, which reads Download again.
        await act(async () => fail(new Error("read failed")));
        expect(button).toHaveFocus();
        expect(button).toHaveAccessibleName("Download");
        expect(button).not.toHaveAttribute("aria-disabled");
        expect(screen.getByRole("alert")).toHaveTextContent("The download failed.");
    });
});
