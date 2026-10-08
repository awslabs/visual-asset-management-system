/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceAuditLog, { eventTypeOptions } from "./ComplianceAuditLog";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../services/ComplianceService";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchAuditLog: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const entry = (entryId: string, actor: string) => ({
    entryId,
    eventType: "compliance_check",
    databaseId: "db1",
    assetId: `asset-${entryId}`,
    actor,
    timestamp: "2026-01-01T00:00:00Z",
});

describe("ComplianceAuditLog pagination", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("renders the first page, marks that more exist, and fetches page two with its token", async () => {
        service().fetchAuditLog.mockImplementation(async (params: any) => {
            if (params.startingToken === "tok-page-2") {
                return [
                    true,
                    { entries: [entry("e2", "second-page-actor")], nextToken: undefined },
                ];
            }
            return [true, { entries: [entry("e1", "first-page-actor")], nextToken: "tok-page-2" }];
        });

        render(<ComplianceAuditLog />);

        await waitFor(() => {
            expect(screen.getByText("first-page-actor")).toBeInTheDocument();
        });
        // The counter marks an open end rather than presenting the page as the whole log.
        expect(screen.getByText("(1+)")).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Next page of audit entries" })
        ).not.toHaveAttribute("aria-disabled", "true");

        const firstCall = service().fetchAuditLog.mock.calls[0][0];
        expect(firstCall.maxItems).toBe(COMPLIANCE_LISTING_PAGE_SIZE);
        expect(firstCall.startingToken).toBeUndefined();

        await userEvent.click(screen.getByRole("button", { name: "Next page of audit entries" }));

        await waitFor(() => {
            expect(screen.getByText("second-page-actor")).toBeInTheDocument();
        });
        expect(screen.queryByText("first-page-actor")).not.toBeInTheDocument();
        const secondCall = service().fetchAuditLog.mock.calls[1][0];
        expect(secondCall.startingToken).toBe("tok-page-2");
        expect(secondCall.maxItems).toBe(COMPLIANCE_LISTING_PAGE_SIZE);
        // The last page closes the counter.
        expect(screen.getByText("(1)")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Next page of audit entries" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );

        // Going back re-reads page one from its (absent) starting token.
        await userEvent.click(
            screen.getByRole("button", { name: "Previous page of audit entries" })
        );
        await waitFor(() => {
            expect(screen.getByText("first-page-actor")).toBeInTheDocument();
        });
        const thirdCall = service().fetchAuditLog.mock.calls[2][0];
        expect(thirdCall.startingToken).toBeUndefined();
    });

    it("restarts from the first page when the event type filter changes", async () => {
        service().fetchAuditLog.mockResolvedValue([
            true,
            { entries: [entry("e1", "someone")], nextToken: undefined },
        ]);

        render(<ComplianceAuditLog />);
        await waitFor(() => {
            expect(screen.getByText("someone")).toBeInTheDocument();
        });
        expect(service().fetchAuditLog.mock.calls[0][0].eventType).toBeUndefined();

        await userEvent.click(
            screen.getByRole("button", { name: /Filter audit entries by event type/ })
        );
        await userEvent.click(await screen.findByText("Exception Granted"));

        await waitFor(() => {
            expect(service().fetchAuditLog.mock.calls.length).toBeGreaterThanOrEqual(2);
        });
        const lastCall = service().fetchAuditLog.mock.calls.at(-1)[0];
        expect(lastCall.eventType).toBe("exception_granted");
        expect(lastCall.startingToken).toBeUndefined();
    });

    it("labels the filter and the refresh control", async () => {
        service().fetchAuditLog.mockResolvedValue([true, { entries: [], nextToken: undefined }]);
        render(<ComplianceAuditLog />);
        await waitFor(() => {
            expect(screen.getByText("No audit entries")).toBeInTheDocument();
        });
        expect(screen.getByText("Event type")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Refresh audit log" })).toBeInTheDocument();
    });
});

describe("ComplianceAuditLog event type filter", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("offers every event type the audit trail records", async () => {
        const recordedEventTypes = [
            "compliance_check",
            "schema_bound_to_database",
            "schema_bound_to_asset",
            "schema_unbound_from_database",
            "schema_unbound_from_asset",
            "quarantine_released",
            "exception_granted",
            "exception_revoked",
            "cascade_triggered",
            "cascade_auto_triggered",
            "cascade_approved",
            "cascade_rejected",
            "cascade_completed",
        ];
        const offered = eventTypeOptions.map((option) => option.value);
        expect(offered[0]).toBe("");
        expect(offered.slice(1).sort()).toEqual([...recordedEventTypes].sort());
        // Each option carries a label distinct from its raw value.
        eventTypeOptions.forEach((option) => {
            expect(option.label).toBeTruthy();
            expect(option.label).not.toBe(option.value);
        });
    });

    it("filters on the exception revoked and cascade completion events", async () => {
        service().fetchAuditLog.mockResolvedValue([
            true,
            { entries: [entry("e1", "someone")], nextToken: undefined },
        ]);

        render(<ComplianceAuditLog />);
        await waitFor(() => {
            expect(screen.getByText("someone")).toBeInTheDocument();
        });

        const openFilter = () =>
            userEvent.click(
                screen.getByRole("button", { name: /Filter audit entries by event type/ })
            );

        await openFilter();
        await userEvent.click(await screen.findByText("Exception Revoked"));
        await waitFor(() => {
            expect(service().fetchAuditLog.mock.calls.at(-1)[0].eventType).toBe(
                "exception_revoked"
            );
        });

        await openFilter();
        await userEvent.click(await screen.findByText("Cascade Completed"));
        await waitFor(() => {
            expect(service().fetchAuditLog.mock.calls.at(-1)[0].eventType).toBe(
                "cascade_completed"
            );
        });
    });
});
