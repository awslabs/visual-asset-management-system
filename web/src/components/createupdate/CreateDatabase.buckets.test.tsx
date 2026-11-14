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
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import CreateDatabase from "./CreateDatabase";

jest.mock("../../services/APIService", () => ({
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    fetchBuckets: jest.fn(),
}));

let mockAllowed = { loading: false, buckets: true, failed: false };
const mockRetry = jest.fn();
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: mockAllowed.loading,
        failed: mockAllowed.failed,
        retry: mockRetry,
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
    mockAllowed = { loading: false, buckets: true, failed: false };
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
        mockAllowed = { loading: false, buckets: false, failed: false };
        render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("waits for the role's permissions before deciding", async () => {
        mockAllowed = { loading: true, buckets: true, failed: false };
        const { rerender } = render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
        mockAllowed = { loading: false, buckets: true, failed: false };
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        expect(screen.getByText("Default Bucket and Prefix")).toBeInTheDocument();
    });

    it("retries a failed permissions check once per opening of the form", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const { rerender } = render(form(false));
        await new Promise((r) => setTimeout(r, 0));
        expect(mockRetry).not.toHaveBeenCalled();

        rerender(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
        rerender(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(mockRetry).toHaveBeenCalledTimes(1);

        rerender(form(false));
        rerender(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(2));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("reads the bucket list once a retried permissions check allows it", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const { rerender } = render(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
        mockAllowed = { loading: false, buckets: true, failed: false };
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
    });
});

describe("CreateDatabase bucket list empty message", () => {
    const openList = async () => {
        const { rerender } = render(form(true));
        if (mockAllowed.failed) {
            // The form retries the check on open; play the retry out to the same answer.
            await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
            mockAllowed = { ...mockAllowed, loading: true };
            rerender(form(true));
            mockAllowed = { ...mockAllowed, loading: false };
            rerender(form(true));
        }
        const wrapper = createWrapper(document.body);
        const select = wrapper.findSelect('[data-testid="database-bucket"]');
        select!.openDropdown();
        return select!;
    };

    it("says the permissions check failed when it did", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const select = await openList();
        expect(select.findDropdown().getElement().textContent).toContain(
            "The permissions check failed"
        );
    });

    it("says the role may not list buckets when the check succeeded without the route", async () => {
        mockAllowed = { loading: false, buckets: false, failed: false };
        const select = await openList();
        expect(select.findDropdown().getElement().textContent).toContain(
            "Your role does not allow listing buckets."
        );
    });
});
