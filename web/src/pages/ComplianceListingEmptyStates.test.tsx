/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An empty page of a compliance listing says what the walk found: nothing at all, nothing that
 * matches the filters, rows that do not match or are not the reader's on this page, or the end of
 * the rows earlier pages listed (components/compliance/listingStates.tsx). A filter that matches
 * nothing says so on every page, not that the earlier pages list everything or that every asset is
 * compliant.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../synonyms";
import ComplianceAuditLog from "./ComplianceAuditLog";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import DatabaseCompliancePage from "./DatabaseCompliance";
import ComplianceSchemas from "./ComplianceSchemas";
import { listedBefore, listingEmptyText } from "../components/compliance/listingStates";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchAuditLog: jest.fn(),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemaListing: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const auditEntry = (entryId: string) => ({
    entryId,
    eventType: "compliance_check",
    databaseId: "db1",
    assetId: `asset-${entryId}`,
    actor: `actor-${entryId}`,
    timestamp: "2026-01-01T00:00:00Z",
});

const cascade = (cascadeId: string) => ({
    cascadeId,
    state: "completed",
    databaseId: "db1",
    assetId: `trigger-${cascadeId}`,
    assetName: `Trigger ${cascadeId}`,
    createdAt: "2026-01-01T00:00:00Z",
});

/** A two-page walk: `first` on page 1 (with a token), `second` on page 2 (the end). */
const twoPages =
    <T,>(key: string, first: T[], second: T[]) =>
    async (params: any) =>
        params?.startingToken === "tok-2"
            ? [true, { [key]: second, nextToken: undefined }]
            : [true, { [key]: first, nextToken: "tok-2" }];

beforeEach(() => {
    jest.clearAllMocks();
    // The filters live in the URL.
    window.history.replaceState(null, "", "/");
});

describe("listingEmptyText", () => {
    const text = (walk: Partial<Parameters<typeof listingEmptyText>[0]>) =>
        listingEmptyText(
            { hasMore: false, pageIndex: 1, listedEarlier: false, filtered: false, ...walk },
            "rows",
            "Nothing recorded."
        );

    it("names the case of each empty page", () => {
        expect(text({})).toBe("Nothing recorded.");
        expect(text({ filtered: true })).toBe("No rows match the filters.");
        expect(text({ hasMore: true })).toBe(
            "None of the rows read for this page are visible to you. Open the next page to continue."
        );
        expect(text({ hasMore: true, filtered: true })).toBe(
            "The rows read for this page do not match the filters or are not visible to you. Open the next page to continue."
        );
        expect(text({ pageIndex: 2, listedEarlier: true })).toBe(
            "No more rows. The earlier pages list all of them."
        );
        expect(text({ pageIndex: 2, listedEarlier: true, filtered: true })).toBe(
            "No more rows match the filters. The earlier pages list all of them."
        );
        // A later page after pages that listed nothing: no "earlier pages list all of them".
        expect(text({ pageIndex: 3, filtered: true })).toBe("No rows match the filters.");
        expect(text({ pageIndex: 3 })).toBe("No rows are visible to you.");
    });

    it("counts only the pages before the one shown", () => {
        expect(listedBefore({ 0: false, 1: true }, 2)).toBe(false);
        expect(listedBefore({ 0: false, 1: true }, 3)).toBe(true);
        expect(listedBefore({ 0: true }, 1)).toBe(false);
    });
});

describe("Audit Log", () => {
    it("says a text search matched nothing on the walk's last page (round 18 repro)", async () => {
        window.history.replaceState(null, "", "/#/compliance/audit?text=zzz-nomatch");
        service().fetchAuditLog.mockImplementation(twoPages("entries", [], []));
        render(<ComplianceAuditLog />);
        expect(
            await screen.findByText(
                "The audit entries read for this page do not match the filters or are not visible to you. Open the next page to continue."
            )
        ).toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Next page of audit entries" }));

        expect(await screen.findByText("No audit entries match the filters.")).toBeInTheDocument();
        expect(screen.queryByText(/earlier pages list all of them/)).not.toBeInTheDocument();
    });

    it("says no more entries match after a filtered page that listed some", async () => {
        window.history.replaceState(null, "", "/#/compliance/audit?eventType=compliance_check");
        service().fetchAuditLog.mockImplementation(twoPages("entries", [auditEntry("e1")], []));
        render(<ComplianceAuditLog />);
        await screen.findByText("actor-e1");

        await userEvent.click(screen.getByRole("button", { name: "Next page of audit entries" }));

        expect(
            await screen.findByText(
                "No more audit entries match the filters. The earlier pages list all of them."
            )
        ).toBeInTheDocument();
    });

    it("says no entry is visible when an unfiltered walk listed none", async () => {
        service().fetchAuditLog.mockImplementation(twoPages("entries", [], []));
        render(<ComplianceAuditLog />);
        await screen.findByText(/None of the audit entries read for this page are visible to you/);

        await userEvent.click(screen.getByRole("button", { name: "Next page of audit entries" }));

        expect(await screen.findByText("No audit entries are visible to you.")).toBeInTheDocument();
        expect(screen.queryByText(/earlier pages list all of them/)).not.toBeInTheDocument();
    });
});

describe("Quarantine", () => {
    it("says no quarantined asset matches a text filter, not that every asset is compliant", async () => {
        window.history.replaceState(null, "", "/#/compliance/quarantine?text=zzz-nomatch");
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        expect(
            await screen.findByText(`No quarantined ${Synonyms.assets} match the filters.`)
        ).toBeInTheDocument();
        expect(
            screen.queryByText(/currently compliant or pending evaluation/)
        ).not.toBeInTheDocument();
        expect(service().fetchQuarantinedAssets.mock.calls.at(-1)[1].text).toBe("zzz-nomatch");
    });

    it("still says every asset is compliant when no filter is set", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        expect(
            await screen.findByText(
                `All ${Synonyms.assets} are currently compliant or pending evaluation.`
            )
        ).toBeInTheDocument();
    });
});

describe("Cascade Approvals", () => {
    it("says no cascade matches the filters on a later page when no page listed one", async () => {
        window.history.replaceState(null, "", "/#/compliance/cascades?text=zzz-nomatch");
        service().fetchCascades.mockImplementation(twoPages("cascades", [], []));
        render(<ComplianceCascades />);
        await screen.findByText(
            /The cascades read for this page do not match the filters or are not visible to you/
        );

        await userEvent.click(screen.getByRole("button", { name: "Next page of cascades" }));

        expect(await screen.findByText("No cascades match the filters.")).toBeInTheDocument();
        expect(screen.queryByText(/earlier pages list all of them/)).not.toBeInTheDocument();
    });

    it("says no more cascades match after a filtered page that listed one", async () => {
        window.history.replaceState(null, "", "/#/compliance/cascades?state=completed");
        service().fetchCascades.mockImplementation(twoPages("cascades", [cascade("c-1")], []));
        render(<ComplianceCascades />);
        await screen.findByText("Trigger c-1");

        await userEvent.click(screen.getByRole("button", { name: "Next page of cascades" }));

        expect(
            await screen.findByText(
                "No more cascades match the filters. The earlier pages list all of them."
            )
        ).toBeInTheDocument();
    });
});

describe("Database compliance overview", () => {
    const overview = (assets: any[], nextToken?: string) => ({
        databaseId: "db1",
        totalAssets: 3,
        summary: { compliant: 3 },
        assets,
        nextToken,
    });

    beforeEach(() => {
        service().getDatabaseBindings.mockResolvedValue([
            true,
            { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
        ]);
    });

    it("does not say nothing matches on a filtered page that has a next page", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance?text=zzz-nomatch");
        service().fetchDatabaseComplianceOverview.mockImplementation(
            async (_db: string, params: any) =>
                params?.startingToken === "tok-2"
                    ? [true, overview([])]
                    : [true, overview([], "tok-2")]
        );
        render(<DatabaseCompliancePage />);
        expect(
            await screen.findByText(
                `The ${Synonyms.assets} read for this page do not match the filters or are not visible to you. Open the next page to continue.`
            )
        ).toBeInTheDocument();

        await userEvent.click(
            screen.getByRole("button", { name: `Next page of ${Synonyms.assets}` })
        );

        expect(
            await screen.findByText(`No ${Synonyms.assets} match the filters.`)
        ).toBeInTheDocument();
    });
});

describe("Compliance schemas", () => {
    it("says no schema matches a search that matches none", async () => {
        service().fetchComplianceSchemaListing.mockResolvedValue([
            true,
            {
                schemas: [
                    {
                        schemaName: "cad-quality",
                        description: "CAD deliverable checks",
                        schemaFormat: "vams-rules-v1",
                        schemaBody: { schemaFormat: "vams-rules-v1", rules: {} },
                        version: 1,
                    },
                ],
            },
        ]);
        render(<ComplianceSchemas />);
        await screen.findByText("cad-quality");

        fireEvent.change(screen.getByPlaceholderText("Find schemas"), {
            target: { value: "zzz-nomatch" },
        });

        expect(
            await screen.findByText("No compliance schemas match the filters.")
        ).toBeInTheDocument();
        expect(screen.queryByText(/No compliance schemas registered/)).not.toBeInTheDocument();
    });
});
