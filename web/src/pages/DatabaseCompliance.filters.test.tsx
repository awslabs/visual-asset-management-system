/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database compliance overview filters and sorts on the server: the filters ride on every page
 * request, live in the URL, restart the token walk when they change, and the summary tiles act as
 * state filters. The summary itself always covers the whole database.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { TEXT_FILTER_DEBOUNCE_MS } from "../components/compliance/listingFilters";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const overview = (nextToken?: string) => ({
    databaseId: "db1",
    totalAssets: 120,
    summary: {
        compliant: 100,
        non_compliant: 11,
        pending_evaluation: 3,
        quarantined: 2,
        exception: 4,
        unknown: 0,
        error: 5,
    },
    assets: [
        {
            databaseId: "db1",
            assetId: "a1",
            assetName: "Turbine Blade",
            complianceState: "quarantined",
            schemaName: "std",
        },
    ],
    nextToken,
});

const filtersOf = (call: number) => service().fetchDatabaseComplianceOverview.mock.calls[call][2];

beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState(null, "", "/#/databases/db1/compliance");
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview("tok-2")]);
});

afterEach(() => {
    jest.useRealTimers();
});

describe("DatabaseCompliance overview filters", () => {
    it("sends the URL's filters and sort with the first page", async () => {
        window.history.replaceState(
            null,
            "",
            "/#/databases/db1/compliance?state=quarantined&schemaSource=asset&evaluationError=true&text=blade&sort=name&schemaName=std"
        );
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalled());
        expect(filtersOf(0)).toEqual({
            state: "quarantined",
            schemaName: "std",
            schemaSource: "asset",
            evaluationError: "true",
            text: "blade",
            sort: "name",
        });
        // A filtered table counts its page, not the database; the summary still covers it all.
        expect(await screen.findByText("(1+)")).toBeInTheDocument();
        expect(screen.getByText(/the summary above covers all 120 tracked/)).toBeInTheDocument();
    });

    it("a state tile filters the table to that state and restarts the walk", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        await userEvent.click(screen.getByRole("button", { name: /Next page of/ }));
        await waitFor(() =>
            expect(
                service().fetchDatabaseComplianceOverview.mock.calls.some(
                    (call: any[]) => call[1].startingToken === "tok-2"
                )
            ).toBe(true)
        );
        const before = service().fetchDatabaseComplianceOverview.mock.calls.length;
        await userEvent.click(screen.getByRole("button", { name: /Show only quarantined/ }));
        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview.mock.calls.length).toBe(before + 1)
        );
        const last = service().fetchDatabaseComplianceOverview.mock.calls[before];
        expect(last[1].startingToken).toBeUndefined();
        expect(last[2].state).toBe("quarantined");
        expect(window.location.hash).toContain("state=quarantined");
        // Clicking the active tile again clears the filter.
        await userEvent.click(screen.getByRole("button", { name: /Show only quarantined/ }));
        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview.mock.calls.length).toBe(before + 2)
        );
        expect(filtersOf(before + 1).state).toBe("");
    });

    it("the errors tile filters to the errored evaluations and Tracked clears the filters", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        await userEvent.click(screen.getByRole("button", { name: /last evaluation errored/ }));
        await waitFor(() =>
            expect(
                filtersOf(service().fetchDatabaseComplianceOverview.mock.calls.length - 1)
            ).toEqual(expect.objectContaining({ evaluationError: "true" }))
        );
        await userEvent.click(screen.getByRole("button", { name: /Show every tracked/ }));
        await waitFor(() =>
            expect(
                filtersOf(service().fetchDatabaseComplianceOverview.mock.calls.length - 1)
            ).toEqual(expect.objectContaining({ evaluationError: "", state: "" }))
        );
    });

    it("debounces the search into one request", async () => {
        jest.useFakeTimers();
        const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
        render(<DatabaseCompliancePage />);
        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(1)
        );
        await user.type(screen.getByLabelText(/^Search/), "blade");
        expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(1);
        await act(async () => {
            jest.advanceTimersByTime(TEXT_FILTER_DEBOUNCE_MS + 10);
        });
        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(2)
        );
        expect(filtersOf(1).text).toBe("blade");
    });

    it("the state, source, evaluation and sort selects each reach the request", async () => {
        const { container } = render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        // The filter bar's selects, in order: state, schema, binding source, last evaluation, sort.
        const pick = async (index: number, value: string) => {
            const select = createWrapper(container).findAllSelects()[index];
            const count = service().fetchDatabaseComplianceOverview.mock.calls.length;
            act(() => {
                select.openDropdown();
            });
            act(() => {
                select.selectOptionByValue(value);
            });
            await waitFor(() =>
                expect(service().fetchDatabaseComplianceOverview.mock.calls.length).toBe(count + 1)
            );
            return filtersOf(count);
        };
        expect((await pick(0, "non_compliant")).state).toBe("non_compliant");
        expect((await pick(2, "asset")).schemaSource).toBe("asset");
        expect((await pick(3, "false")).evaluationError).toBe("false");
        expect((await pick(4, "name")).sort).toBe("name");
        expect((await pick(1, "std")).schemaName).toBe("std");
    });

    it("says no asset matches when a filtered page is empty", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance?state=unknown");
        service().fetchDatabaseComplianceOverview.mockResolvedValue([
            true,
            { ...overview(), assets: [] },
        ]);
        render(<DatabaseCompliancePage />);
        expect(await screen.findByText(/match the filters/)).toBeInTheDocument();
    });
});
