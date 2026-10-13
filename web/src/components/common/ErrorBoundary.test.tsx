/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

jest.mock("../../utils/chunkLoadRecovery", () => ({
    ...jest.requireActual("../../utils/chunkLoadRecovery"),
    reloadPage: jest.fn(),
}));

let failure: Error | null = null;

const Thrower = () => {
    if (failure) {
        throw failure;
    }
    return <div>content rendered</div>;
};

describe("ErrorBoundary", () => {
    let consoleError: jest.SpyInstance;
    beforeEach(() => {
        consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
        jest.clearAllMocks();
    });
    afterEach(() => {
        consoleError.mockRestore();
        failure = null;
    });

    it("offers Retry for an ordinary error and re-renders the children on it", () => {
        failure = new Error("render failed");
        render(
            <ErrorBoundary componentName="Widget">
                <Thrower />
            </ErrorBoundary>
        );
        expect(screen.getByText("Widget failed to load")).toBeInTheDocument();

        failure = null;
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(screen.getByText("content rendered")).toBeInTheDocument();
    });

    it("offers Reload, not Retry, when a chunk failed to download", () => {
        const { reloadPage } = jest.requireMock("../../utils/chunkLoadRecovery");
        failure = new TypeError(
            "Failed to fetch dynamically imported module: https://host/assets/App-abc.js"
        );
        render(
            <ErrorBoundary componentName="The application">
                <Thrower />
            </ErrorBoundary>
        );
        expect(screen.getByText("The application could not be downloaded")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Reload" }));
        expect(reloadPage).toHaveBeenCalledTimes(1);
    });
});
