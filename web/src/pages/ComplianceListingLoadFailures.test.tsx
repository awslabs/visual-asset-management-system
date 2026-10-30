/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compliance listing whose load failed says so: the flash names what could not be loaded and
 * why, and the table says the list could not be loaded, with no empty-state text ("No compliance
 * audit events have been recorded.", "All assets are currently compliant…"), no "(0)" counter and
 * no rows of an earlier load, and points at the way to try again
 * (components/compliance/listingStates.tsx).
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../synonyms";
import ComplianceAuditLog from "./ComplianceAuditLog";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import DatabaseCompliancePage from "./DatabaseCompliance";
import ComplianceSchemas from "./ComplianceSchemas";
import EvaluationHistoryModal from "../components/compliance/EvaluationHistoryModal";
import { ComplianceTab } from "../components/asset/tabs/ComplianceTab";
import { loadFailureMessage } from "../components/compliance/listingStates";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

// The pages run as a caller who may call no route (so no database list is fetched); the asset tab
// as one who may call every route.
let mockAllowAll = false;
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => mockAllowAll }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchAuditLog: jest.fn(),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemaListing: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
    fetchComplianceState: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

/** The heading's text of the page's table, with its counter. */
const headingWith = (name: RegExp) => screen.getByRole("heading", { name });

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowAll = false;
    window.history.replaceState(null, "", "/");
});

describe("loadFailureMessage", () => {
    it("names what failed and the reason given", () => {
        expect(loadFailureMessage("the audit log", "Failed to fetch")).toBe(
            "Could not load the audit log: Failed to fetch"
        );
        expect(loadFailureMessage("the audit log", undefined)).toBe(
            "Could not load the audit log."
        );
        expect(loadFailureMessage("the audit log", "  ")).toBe("Could not load the audit log.");
    });
});

describe("Audit Log", () => {
    it("says the log could not be loaded instead of that nothing was recorded", async () => {
        service().fetchAuditLog.mockResolvedValue([false, "Failed to fetch"]);
        render(<ComplianceAuditLog />);

        expect(
            await screen.findByText("Could not load the audit log: Failed to fetch")
        ).toBeInTheDocument();
        expect(screen.getByText("The audit log could not be loaded")).toBeInTheDocument();
        expect(screen.getByText("Use the refresh button to try again.")).toBeInTheDocument();
        expect(screen.queryByText(/have been recorded/)).not.toBeInTheDocument();
        expect(headingWith(/Compliance Audit Log/).textContent).not.toMatch(/\(0/);
    });

    it("lists the entries once the refresh button reloads them", async () => {
        service()
            .fetchAuditLog.mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue([
                true,
                {
                    entries: [
                        {
                            entryId: "e1",
                            eventType: "compliance_check",
                            databaseId: "db1",
                            assetId: "asset-1",
                            actor: "reloaded-actor",
                            timestamp: "2026-01-01T00:00:00Z",
                        },
                    ],
                },
            ]);
        render(<ComplianceAuditLog />);
        await screen.findByText("The audit log could not be loaded");

        await userEvent.click(screen.getByRole("button", { name: "Refresh audit log" }));

        expect(await screen.findByText("reloaded-actor")).toBeInTheDocument();
        expect(screen.queryByText("The audit log could not be loaded")).not.toBeInTheDocument();
        expect(screen.queryByText(/Could not load the audit log/)).not.toBeInTheDocument();
    });
});

describe("Quarantine", () => {
    it("says the list could not be loaded instead of that every asset is compliant", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([false, "Internal server error"]);
        render(<ComplianceQuarantine />);

        expect(
            await screen.findByText(
                `Could not load the quarantined ${Synonyms.assets}: Internal server error`
            )
        ).toBeInTheDocument();
        expect(
            screen.getByText(`The quarantined ${Synonyms.assets} could not be loaded`)
        ).toBeInTheDocument();
        expect(
            screen.queryByText(/currently compliant or pending evaluation/)
        ).not.toBeInTheDocument();
        expect(headingWith(/Quarantined/).textContent).not.toMatch(/\(0/);
    });
});

describe("Cascade Approvals", () => {
    const cascade = {
        cascadeId: "c-1",
        state: "pending_approval",
        databaseId: "db1",
        assetId: "trigger-1",
        assetName: "Trigger one",
        createdAt: "2026-01-01T00:00:00Z",
    };

    it("says the list could not be loaded, with a filter set too", async () => {
        window.history.replaceState(null, "", "/#/compliance/cascades?state=completed");
        service().fetchCascades.mockResolvedValue([false, "Failed to fetch"]);
        render(<ComplianceCascades />);

        expect(
            await screen.findByText("Could not load the cascades: Failed to fetch")
        ).toBeInTheDocument();
        expect(screen.getByText("The cascades could not be loaded")).toBeInTheDocument();
        expect(screen.queryByText("No cascades match the filters.")).not.toBeInTheDocument();
        expect(headingWith(/Cascade Approval Queue/).textContent).not.toMatch(/\(0/);
    });

    it("drops the rows of an earlier load when a refresh fails", async () => {
        service()
            .fetchCascades.mockResolvedValueOnce([true, { cascades: [cascade] }])
            .mockResolvedValue([false, "Failed to fetch"]);
        render(<ComplianceCascades />);
        await screen.findByText("Trigger one");

        await userEvent.click(screen.getByRole("button", { name: "Refresh cascades" }));

        expect(await screen.findByText("The cascades could not be loaded")).toBeInTheDocument();
        expect(screen.queryByText("Trigger one")).not.toBeInTheDocument();
    });
});

describe("Database compliance overview", () => {
    const row = (assetId: string) => ({
        databaseId: "db1",
        assetId,
        assetName: `Name of ${assetId}`,
        complianceState: "compliant",
        schemaName: "std",
        schemaSource: "database",
    });

    beforeEach(() => {
        service().getDatabaseBindings.mockResolvedValue([
            true,
            { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
        ]);
    });

    it("shows no rows of the earlier page when a page fails, and retries that page", async () => {
        const page2 = jest
            .fn()
            .mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue([
                true,
                {
                    databaseId: "db1",
                    totalAssets: 2,
                    summary: { compliant: 2 },
                    assets: [row("a2")],
                },
            ]);
        service().fetchDatabaseComplianceOverview.mockImplementation(
            async (_db: string, params: any) =>
                params?.startingToken === "tok-2"
                    ? page2()
                    : [
                          true,
                          {
                              databaseId: "db1",
                              totalAssets: 2,
                              summary: { compliant: 2 },
                              assets: [row("a1")],
                              nextToken: "tok-2",
                          },
                      ]
        );
        render(<DatabaseCompliancePage />);
        await screen.findByText("Name of a1");

        await userEvent.click(
            screen.getByRole("button", { name: `Next page of ${Synonyms.assets}` })
        );

        expect(
            await screen.findByText(
                `Could not load the ${Synonyms.database} compliance overview: Failed to fetch`
            )
        ).toBeInTheDocument();
        expect(
            screen.getByText(`The ${Synonyms.asset} compliance states could not be loaded`)
        ).toBeInTheDocument();
        expect(screen.queryByText("Name of a1")).not.toBeInTheDocument();
        expect(
            headingWith(new RegExp(`${Synonyms.Asset} Compliance States`)).textContent
        ).not.toMatch(/\(\d/);

        await userEvent.click(screen.getByRole("button", { name: "Retry" }));

        expect(await screen.findByText("Name of a2")).toBeInTheDocument();
        expect(page2).toHaveBeenCalledTimes(2);
    });
});

describe("Compliance schemas", () => {
    it("says the schemas could not be loaded and retries", async () => {
        service()
            .fetchComplianceSchemaListing.mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue([
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

        expect(
            await screen.findByText("Could not load the compliance schemas: Failed to fetch")
        ).toBeInTheDocument();
        expect(screen.getByText("The compliance schemas could not be loaded")).toBeInTheDocument();
        expect(screen.queryByText(/No compliance schemas registered/)).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Retry" }));

        expect(await screen.findByText("cad-quality")).toBeInTheDocument();
    });
});

describe("Evaluation history", () => {
    it("in the overview's dialog, says the history could not be loaded and retries", async () => {
        service()
            .fetchEvaluationHistory.mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue([true, { evaluations: [], nextToken: undefined }]);
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="asset-1"
                assetLabel="Asset one"
                onDismiss={jest.fn()}
            />
        );

        expect(
            await screen.findByText("Could not load the evaluation history: Failed to fetch")
        ).toBeInTheDocument();
        expect(screen.getByText("The evaluation history could not be loaded")).toBeInTheDocument();
        expect(screen.queryByText(/No evaluations recorded/)).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Retry" }));

        expect(await screen.findByText(/No evaluations recorded/)).toBeInTheDocument();
    });

    it("on the asset's Compliance tab, says the history could not be loaded", async () => {
        mockAllowAll = true;
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "compliant" },
        ]);
        service()
            .fetchEvaluationHistory.mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue([true, { evaluations: [], nextToken: undefined }]);
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);

        expect(
            await screen.findByText("Could not load the evaluation history: Failed to fetch")
        ).toBeInTheDocument();
        const history = headingWith(/Evaluation History/);
        expect(history.textContent).not.toMatch(/\(0/);
        expect(screen.getByText("The evaluation history could not be loaded")).toBeInTheDocument();
        expect(screen.queryByText(/No evaluations recorded/)).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: "Retry" }));

        expect(await screen.findByText(/No evaluations recorded/)).toBeInTheDocument();
        expect(within(headingWith(/Evaluation History/)).getByText("(0)")).toBeInTheDocument();
    });
});
