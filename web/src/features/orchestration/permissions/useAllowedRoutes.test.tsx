/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { renderHook, waitFor } from "@testing-library/react";
import * as APIService from "../../../services/APIService";
import { useAllowedRoutes, __resetCache } from "./useAllowedRoutes";

jest.mock("../../../services/APIService");

describe("useAllowedRoutes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // Reset the module-level cache between tests
        __resetCache();
    });

    it("can() is true for an allowed method+path template and false otherwise", async () => {
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue([
            true,
            {
                routes: [
                    {
                        path: "/workflows/executions/{executionId}/logs",
                        methods: ["GET"],
                        category: "workflow",
                    },
                ],
                userId: "u",
            },
        ]);
        const { result } = renderHook(() => useAllowedRoutes());
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.can("GET", "/workflows/executions/{executionId}/logs")).toBe(true);
        expect(result.current.can("DELETE", "/workflows/executions/{executionId}/permanent")).toBe(
            false
        );
    });

    it("can() matches a concrete path against a template with {param} placeholders", async () => {
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue([
            true,
            {
                routes: [
                    {
                        path: "/workflows/executions/{executionId}/logs",
                        methods: ["GET"],
                        category: "workflow",
                    },
                ],
                userId: "u",
            },
        ]);
        const { result } = renderHook(() => useAllowedRoutes());
        await waitFor(() => expect(result.current.loading).toBe(false));
        // Concrete path with actual executionId should match the template
        expect(result.current.can("GET", "/workflows/executions/abc123/logs")).toBe(true);
        // Different concrete path should not match
        expect(result.current.can("GET", "/workflows/executions/abc123/different")).toBe(false);
    });

    it("can() returns false while loading (before promise resolves)", () => {
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockImplementation(
            () => new Promise(() => {}) // Never resolves
        );
        const { result } = renderHook(() => useAllowedRoutes());
        expect(result.current.loading).toBe(true);
        expect(result.current.can("GET", "/workflows/executions/{executionId}/logs")).toBe(false);
    });

    it("can() returns false on fetch error and loading becomes false", async () => {
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue([false, "Network error"]);
        const { result } = renderHook(() => useAllowedRoutes());
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.can("GET", "/workflows/executions/{executionId}/logs")).toBe(false);
    });

    it("can() is case-insensitive for methods", async () => {
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue([
            true,
            {
                routes: [
                    {
                        path: "/workflows",
                        methods: ["get", "post"],
                        category: "workflow",
                    },
                ],
                userId: "u",
            },
        ]);
        const { result } = renderHook(() => useAllowedRoutes());
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.can("GET", "/workflows")).toBe(true);
        expect(result.current.can("get", "/workflows")).toBe(true);
        expect(result.current.can("POST", "/workflows")).toBe(true);
        expect(result.current.can("post", "/workflows")).toBe(true);
        expect(result.current.can("DELETE", "/workflows")).toBe(false);
    });
});
