/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceQuarantine from "./ComplianceQuarantine";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../services/ComplianceService";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    releaseFromQuarantine: jest.fn(),
    grantException: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const quarantined = (assetId: string) => ({
    databaseId: "db1",
    assetId,
    assetName: `Name of ${assetId}`,
    complianceState: "quarantined",
    schemaName: "std",
    quarantineReason: "metadata missing",
    exceptionGranted: false,
    updatedAt: "2026-01-01T00:00:00Z",
});

describe("ComplianceQuarantine", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("pages the listing with the backend token", async () => {
        service().fetchQuarantinedAssets.mockImplementation(async (paging: any) => {
            if (paging.startingToken === "tok-2") {
                return [
                    true,
                    { quarantinedAssets: [quarantined("asset-2")], nextToken: undefined },
                ];
            }
            return [true, { quarantinedAssets: [quarantined("asset-1")], nextToken: "tok-2" }];
        });

        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        expect(screen.getByText("(1+)")).toBeInTheDocument();
        expect(service().fetchQuarantinedAssets.mock.calls[0][0]).toEqual({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken: undefined,
        });

        await userEvent.click(screen.getByRole("button", { name: /Next page of quarantined/ }));

        await screen.findByText("Name of asset-2");
        expect(screen.queryByText("Name of asset-1")).not.toBeInTheDocument();
        expect(service().fetchQuarantinedAssets.mock.calls[1][0]).toEqual({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken: "tok-2",
        });
        expect(screen.getByText("(1)")).toBeInTheDocument();
    });

    it("explains an empty page that still has a continuation", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: "tok-2" },
        ]);

        render(<ComplianceQuarantine />);

        expect(await screen.findByText(/Open the next page to continue/)).toBeInTheDocument();
        expect(screen.getByText("(0+)")).toBeInTheDocument();
    });

    it("collects the exception reason in a modal and sends it", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        service().grantException.mockResolvedValue([true, "Exception granted"]);

        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        await userEvent.click(screen.getByRole("button", { name: "Exception" }));

        expect(screen.getByText("Grant exception for Name of asset-1")).toBeInTheDocument();
        await userEvent.type(
            screen.getByLabelText("Reason for exception"),
            "Legacy scan, accepted risk"
        );
        await userEvent.click(screen.getByRole("button", { name: "Grant exception" }));

        await waitFor(() => {
            expect(service().grantException).toHaveBeenCalledWith(
                "db1",
                "asset-1",
                "Legacy scan, accepted risk"
            );
        });
        expect(await screen.findByText("Exception granted")).toBeInTheDocument();
        // The listing restarts from the first page after the action.
        expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2);
        expect(service().fetchQuarantinedAssets.mock.calls[1][0].startingToken).toBeUndefined();
    });

    it("labels the refresh control", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        expect(
            await screen.findByRole("button", { name: /Refresh quarantined/ })
        ).toBeInTheDocument();
    });

    it("labels the schema column as the bound schema", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");

        // The column shows the state row's bound schema, which is not necessarily the schema
        // whose evaluation produced the quarantine verdict.
        expect(screen.getByRole("columnheader", { name: "Bound schema" })).toBeInTheDocument();
        expect(screen.queryByRole("columnheader", { name: "Schema" })).not.toBeInTheDocument();
    });

    it("renders each row's state through the shared badge", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            {
                quarantinedAssets: [
                    quarantined("asset-1"),
                    { ...quarantined("asset-2"), complianceState: "exception" },
                ],
                nextToken: undefined,
            },
        ]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;
        expect(within(rowOf("Name of asset-1")).getByText("Quarantined")).toBeInTheDocument();
        // The badge shares its label with the row's "Exception" action button.
        expect(within(rowOf("Name of asset-1")).getAllByText("Exception")).toHaveLength(1);
        expect(within(rowOf("Name of asset-2")).getAllByText("Exception")).toHaveLength(2);
    });
});
