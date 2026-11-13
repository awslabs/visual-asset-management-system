/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compliance page reads and writes only its own route's URL (D189). A link, Back or Forward to
 * another page changes the hash while this page is still mounted; that hash's query names the other
 * page's filters (the cascades page's `state=completed` is no state of the database overview's), so
 * the page that is leaving must not follow it and send a request with them. Its own route, with or
 * without a trailing slash and for any database the overview names, is still followed.
 */

import React from "react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import fs from "fs";
import path from "path";
import Synonyms from "../synonyms";
import ComplianceQuarantine, { QUARANTINE_PAGE_ROUTE } from "./ComplianceQuarantine";
import ComplianceCascades, { CASCADE_PAGE_ROUTE } from "./ComplianceCascades";
import ComplianceAuditLog, { AUDIT_PAGE_ROUTE } from "./ComplianceAuditLog";
import DatabaseCompliancePage, { OVERVIEW_PAGE_ROUTE } from "./DatabaseCompliance";
import { isOnRoute, useUrlFilters } from "../components/compliance/listingFilters";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchCascade: jest.fn(),
    fetchAuditLog: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
}));

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(() =>
        Promise.resolve([{ databaseId: "db1" }, { databaseId: "db2" }])
    ),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: false, canOpenDatabaseAssets: false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const setHash = (hash: string) => window.history.replaceState(null, "", `/${hash}`);

/** A link or a pasted URL: a new history entry, then the hash change event the browser fires. */
const followLink = (hash: string) => {
    act(() => {
        window.history.pushState(null, "", `/${hash}`);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
};

/** Back or Forward to an entry with this hash: the URL changes, then popstate fires. */
const goBackOrForwardTo = (hash: string) => {
    act(() => {
        window.history.replaceState(null, "", `/${hash}`);
        window.dispatchEvent(new PopStateEvent("popstate"));
    });
};

/** Gives a reload the time to start before asserting that none did. */
const settle = () =>
    act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
    });

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    service().fetchQuarantinedAssets.mockResolvedValue([true, { quarantinedAssets: [] }]);
    service().fetchCascades.mockResolvedValue([true, { cascades: [] }]);
    service().fetchAuditLog.mockResolvedValue([true, { entries: [] }]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
    ]);
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: null, assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchComplianceSchemas.mockResolvedValue([true, []]);
});

afterEach(() => jest.restoreAllMocks());

describe("each page's route is the one routes.tsx declares for it", () => {
    const routes = fs.readFileSync(path.join(__dirname, "..", "routes.tsx"), "utf8");
    it.each([
        [QUARANTINE_PAGE_ROUTE, "ComplianceQuarantine"],
        [CASCADE_PAGE_ROUTE, "ComplianceCascades"],
        [AUDIT_PAGE_ROUTE, "ComplianceAuditLog"],
        [OVERVIEW_PAGE_ROUTE, "DatabaseCompliance"],
    ])("%s renders %s", (route, page) => {
        expect(routes).toMatch(new RegExp(`path: "${route}",\\s*Page: ${page},`));
    });

    it("matches its own route with and without a trailing slash, and no other", () => {
        setHash("#/compliance/cascades/?state=completed");
        expect(isOnRoute(CASCADE_PAGE_ROUTE)).toBe(true);
        expect(isOnRoute(OVERVIEW_PAGE_ROUTE)).toBe(false);
        setHash("#/databases/db%202/compliance?state=quarantined");
        expect(isOnRoute(OVERVIEW_PAGE_ROUTE)).toBe(true);
        expect(isOnRoute(CASCADE_PAGE_ROUTE)).toBe(false);
        setHash("#/databases/db1/assets");
        expect(isOnRoute(OVERVIEW_PAGE_ROUTE)).toBe(false);
    });
});

describe(`the ${Synonyms.database} compliance overview leaving for Cascade Approvals`, () => {
    const overviewCalls = () => service().fetchDatabaseComplianceOverview.mock.calls;

    it.each([
        ["a link", followLink],
        ["Back or Forward", goBackOrForwardTo],
    ])("sends no request with the cascades page's state on %s", async (_how, navigate) => {
        setHash("#/databases/db1/compliance");
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(overviewCalls().length).toBeGreaterThan(0));
        const before = overviewCalls().length;

        navigate("#/compliance/cascades/?state=completed");
        await settle();

        expect(overviewCalls()).toHaveLength(before);
        expect(overviewCalls().some((call: any[]) => call[2]?.state === "completed")).toBe(false);
    });

    it("still follows its own URL, a trailing slash included", async () => {
        setHash("#/databases/db1/compliance");
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(overviewCalls().length).toBeGreaterThan(0));
        followLink("#/databases/db1/compliance/?state=quarantined");
        await waitFor(() =>
            expect(overviewCalls()[overviewCalls().length - 1][2].state).toBe("quarantined")
        );
    });
});

describe("Cascade Approvals leaving for a database overview", () => {
    const cascadeCalls = () => service().fetchCascades.mock.calls;

    it.each([
        ["a link", followLink],
        ["Back or Forward", goBackOrForwardTo],
    ])("sends no request with the overview's state on %s", async (_how, navigate) => {
        setHash("#/compliance/cascades/");
        render(<ComplianceCascades />);
        await screen.findByText("No cascades");
        const before = cascadeCalls().length;

        navigate("#/databases/db1/compliance?state=quarantined&text=blade");
        await settle();

        expect(cascadeCalls()).toHaveLength(before);
        expect(cascadeCalls().some((call: any[]) => call[0]?.state === "quarantined")).toBe(false);
    });

    it("still follows its own URL with a trailing slash", async () => {
        setHash("#/compliance/cascades/");
        render(<ComplianceCascades />);
        await screen.findByText("No cascades");
        followLink("#/compliance/cascades/?state=completed");
        await waitFor(() =>
            expect(cascadeCalls()[cascadeCalls().length - 1][0].state).toBe("completed")
        );
    });
});

describe("Quarantine and the Audit Log leaving for each other", () => {
    it("Quarantine sends no request with the audit page's database and text", async () => {
        setHash("#/compliance/quarantine/");
        render(<ComplianceQuarantine />);
        await screen.findByText(`No quarantined ${Synonyms.assets}`);
        const before = service().fetchQuarantinedAssets.mock.calls.length;

        followLink("#/compliance/audit/?databaseId=db2&text=reviewer");
        await settle();

        expect(service().fetchQuarantinedAssets.mock.calls).toHaveLength(before);
    });

    it("the Audit Log sends no request with the quarantine page's database", async () => {
        setHash("#/compliance/audit/");
        render(<ComplianceAuditLog />);
        await screen.findByText("No audit entries");
        const before = service().fetchAuditLog.mock.calls.length;

        goBackOrForwardTo("#/compliance/quarantine/?databaseId=db2");
        await settle();

        expect(service().fetchAuditLog.mock.calls).toHaveLength(before);
    });
});

describe("a filter committed after the route changed", () => {
    const KEYS = ["state", "text"] as const;

    it("is kept by the page but not written into the other page's URL", () => {
        setHash("#/compliance/cascades/");
        const { result } = renderHook(() => useUrlFilters(KEYS, CASCADE_PAGE_ROUTE));

        // The route moves to the Audit Log, then a debounced search of the cascades page commits.
        setHash("#/compliance/audit/?eventType=compliance_check");
        act(() => result.current[1]({ text: "blade" }));

        expect(result.current[0].text).toBe("blade");
        expect(window.location.hash).toBe("#/compliance/audit/?eventType=compliance_check");
    });

    it("is written into the page's own URL while the page is on it", () => {
        setHash("#/compliance/cascades/");
        const { result } = renderHook(() => useUrlFilters(KEYS, CASCADE_PAGE_ROUTE));
        act(() => result.current[1]({ state: "aborted" }));
        expect(window.location.hash).toBe("#/compliance/cascades/?state=aborted");
    });
});
