/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import fs from "fs";
import path from "path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceAuditLog, { auditDetailLines, eventTypeOptions } from "./ComplianceAuditLog";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../services/ComplianceService";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchAuditLog: jest.fn(),
}));

// The routes the caller may call, as "METHOD path-template" strings.
let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
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

    it("explains an empty page that still has a continuation and keeps paging", async () => {
        // A reader of few databases can get a page with no entry it may read and a token.
        service().fetchAuditLog.mockImplementation(async (params: any) =>
            params.startingToken === "tok-2"
                ? [true, { entries: [entry("e9", "later-actor")], nextToken: undefined }]
                : [true, { entries: [], nextToken: "tok-2" }]
        );
        render(<ComplianceAuditLog />);

        expect(await screen.findByText(/Open the next page to continue/)).toBeInTheDocument();
        expect(screen.queryByText(/have been recorded/)).not.toBeInTheDocument();
        expect(screen.getByText("(0+)")).toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Next page of audit entries" }));
        expect(await screen.findByText("later-actor")).toBeInTheDocument();
        expect(service().fetchAuditLog.mock.calls.at(-1)[0].startingToken).toBe("tok-2");
    });

    it("says no events were recorded only on an empty last page", async () => {
        service().fetchAuditLog.mockResolvedValue([true, { entries: [], nextToken: undefined }]);
        render(<ComplianceAuditLog />);
        expect(
            await screen.findByText("No compliance audit events have been recorded.")
        ).toBeInTheDocument();
    });
});

describe("ComplianceAuditLog event type filter", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("offers every event type the audit trail records", async () => {
        // The user guide's event-type table, which the backend test holds to the types the code
        // writes: one list, so a type cannot be recorded and then be impossible to filter on.
        const guide = fs.readFileSync(
            path.join(
                __dirname,
                "..",
                "..",
                "..",
                "documentation",
                "docusaurus-site",
                "docs",
                "user-guide",
                "compliance.md"
            ),
            "utf-8"
        );
        const section = guide.slice(guide.indexOf("### Event types"));
        const table = section.slice(0, section.indexOf("\n## "));
        const recordedEventTypes = Array.from(
            table.matchAll(/^\|\s*`([a-z]+(?:_[a-z]+)+)`\s*\|/gm),
            (match) => match[1]
        );
        // The control: the table was found and holds the types once missing from the filter.
        expect(recordedEventTypes).toEqual(
            expect.arrayContaining(["evaluation_error", "exception_superseded", "schema_deleted"])
        );
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

describe("ComplianceAuditLog asset links", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().fetchAuditLog.mockResolvedValue([
            true,
            { entries: [entry("e1", "someone")], nextToken: undefined },
        ]);
    });

    it("shows the asset as text to a caller who may not open the asset page", async () => {
        mockAllowed = [];
        render(<ComplianceAuditLog />);
        const asset = await screen.findByText("asset-e1");
        expect(asset.closest("a")).toBeNull();
    });

    it("links the asset for a caller who may open the asset page", async () => {
        mockAllowed = ["GET /database/{databaseId}/assets/{assetId}"];
        render(<ComplianceAuditLog />);
        expect(await screen.findByRole("link", { name: "asset-e1" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/asset-e1"
        );
    });
});

describe("ComplianceAuditLog details", () => {
    it("lists each detail field instead of raw JSON", () => {
        expect(
            auditDetailLines(
                JSON.stringify({
                    reason: "Legacy part",
                    childCount: 2,
                    ruleNames: ["a", "b"],
                    summary: { compliant: 2, skipped: 1 },
                })
            )
        ).toEqual([
            { key: "reason", value: "Legacy part" },
            { key: "childCount", value: "2" },
            { key: "ruleNames", value: "a, b" },
            { key: "summary", value: "compliant 2, skipped 1" },
        ]);
        expect(auditDetailLines("not json")).toEqual([{ key: "details", value: "not json" }]);
        expect(auditDetailLines(undefined)).toEqual([]);
    });

    it("renders the fields in the Details column", async () => {
        mockAllowed = [];
        service().fetchAuditLog.mockResolvedValue([
            true,
            {
                entries: [
                    {
                        ...entry("1", "reviewer"),
                        details: JSON.stringify({ reason: "Legacy part" }),
                    },
                ],
                nextToken: undefined,
            },
        ]);
        render(<ComplianceAuditLog />);
        expect(await screen.findByText("Legacy part", { exact: false })).toBeInTheDocument();
        expect(screen.queryByText('{"reason":"Legacy part"}')).not.toBeInTheDocument();
    });
});
