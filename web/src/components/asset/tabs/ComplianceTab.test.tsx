/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComplianceTab } from "./ComplianceTab";
import * as permissions from "../../../features/orchestration/permissions/useAllowedRoutes";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../../../services/ComplianceService";

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/ComplianceService", () => ({
    ...jest.requireActual("../../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
    evaluateAssetCompliance: jest.fn(),
    releaseFromQuarantine: jest.fn(),
    grantException: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../../services/ComplianceService");

const evaluation = (evaluationId: string, schemaName: string) => ({
    evaluationId,
    databaseId: "db1",
    assetId: "asset-1",
    schemaName,
    result: "compliant",
    evaluatedAt: "2026-01-01T00:00:00Z",
});

const renderTab = () => render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);

describe("ComplianceTab", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "quarantined" },
        ]);
    });

    it("pages the evaluation history with the backend token", async () => {
        service().fetchEvaluationHistory.mockImplementation(
            async (_db: string, _asset: string, paging: any) => {
                if (paging.startingToken === "tok-2") {
                    return [
                        true,
                        {
                            evaluations: [evaluation("ev-2", "second-page-schema")],
                            nextToken: undefined,
                        },
                    ];
                }
                return [
                    true,
                    { evaluations: [evaluation("ev-1", "first-page-schema")], nextToken: "tok-2" },
                ];
            }
        );

        renderTab();
        await screen.findByText("first-page-schema");
        expect(screen.getByText("(1+)")).toBeInTheDocument();
        expect(service().fetchEvaluationHistory.mock.calls[0]).toEqual([
            "db1",
            "asset-1",
            { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken: undefined },
        ]);

        await userEvent.click(screen.getByRole("button", { name: "Next page of evaluations" }));

        await screen.findByText("second-page-schema");
        expect(screen.queryByText("first-page-schema")).not.toBeInTheDocument();
        expect(service().fetchEvaluationHistory.mock.calls[1][2]).toEqual({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken: "tok-2",
        });
    });

    it("collects the exception reason in a modal and sends it", async () => {
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);
        service().grantException.mockResolvedValue([true, "Exception granted"]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Grant Exception" }));

        await userEvent.type(screen.getByLabelText("Reason for exception"), "Accepted risk");
        await userEvent.click(screen.getByRole("button", { name: "Grant exception" }));

        await waitFor(() => {
            expect(service().grantException).toHaveBeenCalledWith(
                "db1",
                "asset-1",
                "Accepted risk"
            );
        });
        expect(await screen.findByText("Exception granted")).toBeInTheDocument();
    });
});
