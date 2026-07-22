/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExecutionsPage from "./ExecutionsPage";

// Mock ExecutionsBoard
const mockExecutionsBoard = jest.fn();
jest.mock("../features/orchestration/executions/ExecutionsBoard", () => ({
    __esModule: true,
    default: (props: any) => {
        mockExecutionsBoard(props);
        return null;
    },
}));

describe("ExecutionsPage", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: { queries: { retry: false } },
        });
        jest.clearAllMocks();
    });

    it("passes global scope when no query params", () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter initialEntries={["/executions"]}>
                    <Routes>
                        <Route path="/executions" element={<ExecutionsPage />} />
                    </Routes>
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(mockExecutionsBoard).toHaveBeenCalledWith(
            expect.objectContaining({
                scope: { kind: "global" },
            })
        );
    });

    it("passes workflow scope when workflowId and workflowDatabaseId params are present", () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter
                    initialEntries={["/executions?workflowId=wf-1&workflowDatabaseId=db1"]}
                >
                    <Routes>
                        <Route path="/executions" element={<ExecutionsPage />} />
                    </Routes>
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(mockExecutionsBoard).toHaveBeenCalledWith(
            expect.objectContaining({
                scope: {
                    kind: "workflow",
                    workflowId: "wf-1",
                    databaseId: "db1",
                },
            })
        );
    });

    it("passes global scope when only one param is present", () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter initialEntries={["/executions?workflowId=wf-1"]}>
                    <Routes>
                        <Route path="/executions" element={<ExecutionsPage />} />
                    </Routes>
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(mockExecutionsBoard).toHaveBeenCalledWith(
            expect.objectContaining({
                scope: { kind: "global" },
            })
        );
    });
});
