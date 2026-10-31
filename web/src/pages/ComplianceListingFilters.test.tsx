/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance listings filter on the server: the quarantine, cascade and audit pages send their
 * filters with the page request, keep them in the URL (so a filtered view reloads as itself), restart
 * the token walk when a filter changes, and debounce free text so typing sends one request.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import ComplianceAuditLog, { dayBound } from "./ComplianceAuditLog";
import {
    readUrlFilters,
    writeUrlFilters,
    TEXT_FILTER_DEBOUNCE_MS,
} from "../components/compliance/listingFilters";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchCascade: jest.fn(),
    fetchAuditLog: jest.fn(),
}));

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(() =>
        Promise.resolve([{ databaseId: "db1" }, { databaseId: "db2" }])
    ),
}));

let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
}));

jest.mock("../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const setHash = (hash: string) => window.history.replaceState(null, "", `/${hash}`);

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowed = ["GET /database"];
    setHash("#/compliance/quarantine");
    service().fetchQuarantinedAssets.mockResolvedValue([true, { quarantinedAssets: [] }]);
    service().fetchCascades.mockResolvedValue([true, { cascades: [] }]);
    service().fetchAuditLog.mockResolvedValue([true, { entries: [] }]);
});

afterEach(() => {
    jest.useRealTimers();
});

describe("filters kept in the URL", () => {
    it("reads and writes the hash route's query, keeping other parameters", () => {
        setHash("#/compliance/audit?eventType=compliance_check&other=1");
        expect(readUrlFilters(["eventType", "text"] as const)).toEqual({
            eventType: "compliance_check",
            text: "",
        });
        writeUrlFilters({ eventType: "", text: "turbine blade" });
        expect(window.location.hash).toBe("#/compliance/audit?other=1&text=turbine+blade");
    });
});

describe("ComplianceQuarantine filters", () => {
    it("sends the URL's filters with the first page request", async () => {
        setHash("#/compliance/quarantine?databaseId=db2&text=blade");
        render(<ComplianceQuarantine />);
        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalled());
        expect(service().fetchQuarantinedAssets).toHaveBeenLastCalledWith(
            expect.objectContaining({ startingToken: undefined }),
            { databaseId: "db2", text: "blade" }
        );
    });

    it("debounces the text filter into one request and writes it to the URL", async () => {
        jest.useFakeTimers();
        const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
        render(<ComplianceQuarantine />);
        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(1));
        const input = screen.getByLabelText(/Search quarantined/);
        await user.type(input, "blade");
        expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(1);
        await act(async () => {
            jest.advanceTimersByTime(TEXT_FILTER_DEBOUNCE_MS + 10);
        });
        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2));
        expect(service().fetchQuarantinedAssets.mock.calls[1][1]).toEqual({
            databaseId: "",
            text: "blade",
        });
        expect(window.location.hash).toContain("text=blade");
    });

    it("restarts the token walk when the database filter changes", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: "page-2" },
        ]);
        const { container } = render(<ComplianceQuarantine />);
        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(1));
        const select = createWrapper(container).findSelect()!;
        select.openDropdown();
        await waitFor(() => expect(select.findDropdown().findOptions().length).toBe(3));
        select.selectOptionByValue("db1");
        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2));
        expect(service().fetchQuarantinedAssets.mock.calls[1]).toEqual([
            expect.objectContaining({ startingToken: undefined }),
            { databaseId: "db1", text: "" },
        ]);
        expect(window.location.hash).toContain("databaseId=db1");
    });

    it("offers a database id input to a caller who may not list databases", async () => {
        mockAllowed = [];
        render(<ComplianceQuarantine />);
        expect(await screen.findByPlaceholderText("Database ID")).toBeTruthy();
    });
});

describe("ComplianceCascades filters and paging", () => {
    it("lists pending cascades by default and pages with the token", async () => {
        service().fetchCascades.mockResolvedValueOnce([
            true,
            {
                cascades: [
                    {
                        cascadeId: "c1",
                        state: "pending_approval",
                        triggeredByDatabaseId: "db1",
                        triggeredByAssetId: "a1",
                        assetName: "Turbine Blade",
                        requireApproval: true,
                        createdAt: "2026-10-01T00:00:00Z",
                    },
                ],
                nextToken: "page-2",
            },
        ]);
        const { container } = render(<ComplianceCascades />);
        expect(await screen.findByText("Turbine Blade")).toBeTruthy();
        // The trigger asset's database and ID beneath its name.
        expect(screen.getByText("db1 / a1")).toBeTruthy();
        expect(service().fetchCascades.mock.calls[0][0]).toEqual(
            expect.objectContaining({ state: "pending_approval", startingToken: undefined })
        );
        createWrapper(container).findPagination()!.findNextPageButton().click();
        await waitFor(() => expect(service().fetchCascades).toHaveBeenCalledTimes(2));
        expect(service().fetchCascades.mock.calls[1][0].startingToken).toBe("page-2");
    });

    it("sends the state, database and text filters from the URL", async () => {
        setHash("#/compliance/cascades?state=aborted&databaseId=db1&text=blade");
        render(<ComplianceCascades />);
        await waitFor(() => expect(service().fetchCascades).toHaveBeenCalled());
        expect(service().fetchCascades.mock.calls[0][0]).toEqual(
            expect.objectContaining({ state: "aborted", databaseId: "db1", text: "blade" })
        );
        expect(await screen.findByText("No cascades match the filters.")).toBeTruthy();
    });
});

describe("ComplianceAuditLog filters", () => {
    it("sends the database, asset, text and day range from the URL", async () => {
        setHash(
            "#/compliance/audit?databaseId=db1&assetId=a1&text=reviewer&from=2026-10-01&to=2026-10-02"
        );
        render(<ComplianceAuditLog />);
        await waitFor(() => expect(service().fetchAuditLog).toHaveBeenCalled());
        expect(service().fetchAuditLog.mock.calls[0][0]).toEqual(
            expect.objectContaining({
                databaseId: "db1",
                assetId: "a1",
                text: "reviewer",
                startDate: "2026-10-01T00:00:00Z",
                endDate: "2026-10-02T23:59:59Z",
            })
        );
    });

    it("sends no asset without a database", async () => {
        setHash("#/compliance/audit?assetId=a1");
        render(<ComplianceAuditLog />);
        await waitFor(() => expect(service().fetchAuditLog).toHaveBeenCalled());
        expect(service().fetchAuditLog.mock.calls[0][0].assetId).toBeUndefined();
    });

    it("shows the recorded asset name with the id beneath it", async () => {
        service().fetchAuditLog.mockResolvedValue([
            true,
            {
                entries: [
                    {
                        entryId: "e1",
                        eventType: "compliance_check",
                        databaseId: "db1",
                        assetId: "a1",
                        assetName: "Turbine Blade",
                        actor: "reviewer",
                        timestamp: "2026-10-01T00:00:00Z",
                    },
                ],
            },
        ]);
        render(<ComplianceAuditLog />);
        expect(await screen.findByText("Turbine Blade")).toBeTruthy();
        expect(screen.getByText("a1")).toBeTruthy();
    });

    it("bounds a day filter to the whole day", () => {
        expect(dayBound("2026-10-01", false)).toBe("2026-10-01T00:00:00Z");
        expect(dayBound("2026-10-01", true)).toBe("2026-10-01T23:59:59Z");
        expect(dayBound("", true)).toBeUndefined();
    });
});
