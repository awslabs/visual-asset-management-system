/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import LoadingScreen from "./LoadingScreen";

jest.mock("../../utils/chunkLoadRecovery", () => ({
    reloadPage: jest.fn(),
}));

describe("LoadingScreen", () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it("shows only the spinner text when no slow notice is requested", () => {
        render(<LoadingScreen />);
        act(() => {
            jest.advanceTimersByTime(60_000);
        });
        expect(screen.getByText("One moment please ...")).toBeInTheDocument();
        expect(screen.queryByTestId("loading-slow-notice")).not.toBeInTheDocument();
    });

    it("offers a reload once loading passes the threshold", () => {
        const { reloadPage } = jest.requireMock("../../utils/chunkLoadRecovery");
        render(<LoadingScreen slowNoticeAfterMs={10_000} />);

        act(() => {
            jest.advanceTimersByTime(9_999);
        });
        expect(screen.queryByTestId("loading-slow-notice")).not.toBeInTheDocument();

        act(() => {
            jest.advanceTimersByTime(1);
        });
        expect(screen.getByText("This is taking longer than usual")).toBeInTheDocument();

        act(() => {
            screen.getByRole("button", { name: "Reload" }).click();
        });
        expect(reloadPage).toHaveBeenCalledTimes(1);
    });
});
