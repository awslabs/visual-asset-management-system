/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance listings drop a response that arrives after a later request began: the shared
 * guard itself, and the audit log moving from one database filter to another while the first
 * answer is still on its way.
 */

import React from "react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { useLatestRequest } from "../components/compliance/listingFilters";
import ComplianceAuditLog from "./ComplianceAuditLog";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchAuditLog: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

describe("useLatestRequest", () => {
    it("keeps only the latest load current", () => {
        const { result } = renderHook(() => useLatestRequest());
        const first = result.current();
        expect(first()).toBe(true);
        const second = result.current();
        expect(first()).toBe(false);
        expect(second()).toBe(true);
    });

    it("keeps the same begin function across renders", () => {
        const { result, rerender } = renderHook(() => useLatestRequest());
        const begin = result.current;
        rerender();
        expect(result.current).toBe(begin);
    });

    it("drops every load once the component unmounts", () => {
        const { result, unmount } = renderHook(() => useLatestRequest());
        const load = result.current();
        unmount();
        expect(load()).toBe(false);
    });
});

describe("ComplianceAuditLog stale responses", () => {
    const pending: { databaseId: string; resolve: (value: any) => void }[] = [];

    const entry = (databaseId: string, actor: string) => ({
        entryId: `${databaseId}-entry`,
        eventType: "compliance_check",
        databaseId,
        assetId: `${databaseId}-asset`,
        actor,
        timestamp: "2026-01-01T00:00:00Z",
    });

    const answer = async (databaseId: string, actor: string) => {
        const index = pending.findIndex((request) => request.databaseId === databaseId);
        expect(index).toBeGreaterThanOrEqual(0);
        const [request] = pending.splice(index, 1);
        await act(async () => {
            request.resolve([true, { entries: [entry(databaseId, actor)] }]);
        });
    };

    beforeEach(() => {
        jest.clearAllMocks();
        pending.length = 0;
        service().fetchAuditLog.mockImplementation(
            (params: any) =>
                new Promise((resolve) => {
                    pending.push({ databaseId: params.databaseId, resolve });
                })
        );
    });

    it("shows only the current filter's entries when the earlier answer arrives last", async () => {
        window.history.replaceState(null, "", "/#/compliance/audit?databaseId=db-a");
        render(<ComplianceAuditLog />);
        await waitFor(() => expect(pending.map((request) => request.databaseId)).toEqual(["db-a"]));

        act(() => {
            window.history.pushState(null, "", "/#/compliance/audit?databaseId=db-b");
            window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        await waitFor(() =>
            expect(pending.map((request) => request.databaseId)).toEqual(["db-a", "db-b"])
        );

        await answer("db-b", "bravo-actor");
        await answer("db-a", "alpha-actor");

        expect(await screen.findByText("bravo-actor")).toBeInTheDocument();
        expect(screen.queryByText("alpha-actor")).not.toBeInTheDocument();
    });
});
