/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import ExecutionDetail from "./ExecutionDetail";

// A stand-in for the detail page that keeps local state, as the real page keeps its tab and the
// per-step editor state.
jest.mock("../features/orchestration/executions/ExecutionDetailPage", () => ({
    __esModule: true,
    default: ({ executionId }: { executionId: string }) => {
        const [tab, setTab] = jest.requireActual("react").useState("inputs");
        return (
            <div>
                <p>{`${executionId} on ${tab}`}</p>
                <button type="button" onClick={() => setTab("settings")}>
                    Settings
                </button>
            </div>
        );
    },
}));
jest.mock("../hooks/usePageTitle", () => ({ usePageTitle: jest.fn() }));

describe("ExecutionDetail", () => {
    it("opens another execution on the same route with fresh page state", async () => {
        render(
            <MemoryRouter initialEntries={["/executions/e-1"]}>
                <Routes>
                    <Route
                        path="/executions/:executionId"
                        element={
                            <>
                                <ExecutionDetail />
                                <Link to="/executions/e-2">Blocked by e-2</Link>
                            </>
                        }
                    />
                </Routes>
            </MemoryRouter>
        );
        await userEvent.click(screen.getByRole("button", { name: "Settings" }));
        expect(screen.getByText("e-1 on settings")).toBeInTheDocument();

        await userEvent.click(screen.getByRole("link", { name: "Blocked by e-2" }));
        expect(screen.getByText("e-2 on inputs")).toBeInTheDocument();
    });
});
