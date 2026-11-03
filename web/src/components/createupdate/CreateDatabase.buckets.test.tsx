/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Databases page mounts the create form with the list, closed. Reading `GET /buckets` on mount
 * sent the request on every page load, and a role without that route got a 403 and a console error.
 * The form now reads the bucket list once it opens, and only for a role allowed the route.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import CreateDatabase from "./CreateDatabase";

jest.mock("../../services/APIService", () => ({
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    fetchBuckets: jest.fn(),
}));

let mockAllowed = { loading: false, buckets: true };
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: mockAllowed.loading,
        can: (method: string, path: string) =>
            !mockAllowed.loading && method === "GET" && path === "/buckets" && mockAllowed.buckets,
    }),
}));

const { fetchBuckets } = jest.requireMock("../../services/APIService");

const form = (open: boolean) => (
    <CreateDatabase open={open} setOpen={jest.fn()} setReload={jest.fn()} />
);

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowed = { loading: false, buckets: true };
    fetchBuckets.mockResolvedValue({ Items: [] });
});

describe("CreateDatabase bucket list", () => {
    it("does not read the bucket list while the form is closed", async () => {
        render(form(false));
        // Give any mount effect a chance to run before asserting it did not.
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("reads the bucket list once when the form opens, and not again on reopen", async () => {
        const { rerender } = render(form(false));
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        rerender(form(false));
        rerender(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).toHaveBeenCalledTimes(1);
    });

    it("never reads the bucket list for a role not allowed GET /buckets", async () => {
        mockAllowed = { loading: false, buckets: false };
        render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("waits for the role's permissions before deciding", async () => {
        mockAllowed = { loading: true, buckets: true };
        const { rerender } = render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
        mockAllowed = { loading: false, buckets: true };
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        expect(screen.getByText("Default Bucket and Prefix")).toBeInTheDocument();
    });
});
