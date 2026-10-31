/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../synonyms";
import { REASON_REQUIRED_MESSAGE } from "../components/compliance/ReasonModal";
import ComplianceQuarantine from "./ComplianceQuarantine";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../services/ComplianceService";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    releaseFromQuarantine: jest.fn(),
    grantException: jest.fn(),
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
        // A caller who may release and grant exceptions but not open asset pages.
        mockAllowed = [
            "POST /compliance/quarantine/{databaseId}/{assetId}/release",
            "POST /compliance/quarantine/{databaseId}/{assetId}/exception",
        ];
    });

    it("shows asset names as text to a caller who may not open the asset page", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        const name = await screen.findByText("Name of asset-1");
        expect(name.closest("a")).toBeNull();
    });

    it("links asset names to a caller who may open the asset page", async () => {
        mockAllowed = ["GET /database/{databaseId}/assets/{assetId}"];
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        expect(await screen.findByRole("link", { name: "Name of asset-1" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/asset-1"
        );
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

    it("ends a walk on an empty last page without saying every asset is compliant", async () => {
        // The listing filters each page by permission, so a scoped reader's walk can end on an
        // empty page after pages that listed quarantined assets.
        service().fetchQuarantinedAssets.mockImplementation(async (params: any) =>
            params.startingToken === "tok-2"
                ? [true, { quarantinedAssets: [], nextToken: undefined }]
                : [true, { quarantinedAssets: [quarantined("asset-1")], nextToken: "tok-2" }]
        );
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");

        await userEvent.click(screen.getByRole("button", { name: /Next page of quarantined/ }));

        expect(
            await screen.findByText(/^No more quarantined .*earlier pages list all of them\.$/)
        ).toBeInTheDocument();
        expect(
            screen.queryByText(/currently compliant or pending evaluation/)
        ).not.toBeInTheDocument();
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
        // The message names the asset as people know it, not as databaseId:assetId.
        expect(
            await screen.findByText("Exception granted for Name of asset-1.")
        ).toBeInTheDocument();
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

    it("shows each asset's name with its ID beneath, and no State column", async () => {
        // Every asset the listing returns is quarantined, so a State column would say
        // "Quarantined" on every row; it is left out so the table fits the page at 1280 px.
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        const row = screen.getByText("Name of asset-1").closest("tr") as HTMLElement;
        expect(within(row).getByText("asset-1")).toBeInTheDocument();
        expect(within(row).queryByText("Quarantined")).toBeNull();
        expect(screen.queryByRole("columnheader", { name: /^State/ })).toBeNull();
    });

    it("shows a failed execution's reason beside the quarantine reason when it is returned", async () => {
        // The API returns the reason only to a caller who may read the execution; another caller's
        // entry carries the rule and execution only, and its row shows the generic reason alone.
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            {
                quarantinedAssets: [
                    {
                        ...quarantined("asset-1"),
                        quarantineReason: "Pipeline execution FAILED",
                        quarantineExecutionFailures: [
                            {
                                ruleName: "converts",
                                executionId: "e1",
                                executionFailureReason: "ValueError: latin1.obj is not UTF-8",
                            },
                        ],
                    },
                    {
                        ...quarantined("asset-2"),
                        quarantineReason: "Pipeline execution FAILED",
                        quarantineExecutionFailures: [{ ruleName: "converts", executionId: "e2" }],
                    },
                ],
                nextToken: undefined,
            },
        ]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        const shown = screen.getByText("Name of asset-1").closest("tr") as HTMLElement;
        expect(within(shown).getByText("Pipeline execution FAILED")).toBeInTheDocument();
        expect(
            within(shown).getByText(/Pipeline failure \(converts\): ValueError: latin1\.obj/)
        ).toBeInTheDocument();
        const withheld = screen.getByText("Name of asset-2").closest("tr") as HTMLElement;
        expect(within(withheld).getByText("Pipeline execution FAILED")).toBeInTheDocument();
        expect(within(withheld).queryByText(/Pipeline failure/)).toBeNull();
    });
});

describe("ComplianceQuarantine release", () => {
    beforeEach(() => jest.clearAllMocks());

    it("requires a reason before releasing, sends it, and names the asset in the message", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [quarantined("asset-1")], nextToken: undefined },
        ]);
        service().releaseFromQuarantine.mockResolvedValue([
            true,
            "Asset db1:asset-1 released from quarantine",
        ]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        await userEvent.click(screen.getAllByRole("button", { name: "Release" })[0]);
        expect(screen.getByText("Release Name of asset-1 from quarantine")).toBeInTheDocument();

        const confirm = () =>
            userEvent.click(screen.getByRole("button", { name: `Release ${Synonyms.asset}` }));
        await confirm();
        expect(screen.getByText(REASON_REQUIRED_MESSAGE)).toBeInTheDocument();
        expect(service().releaseFromQuarantine).not.toHaveBeenCalled();

        await userEvent.type(screen.getByLabelText("Reason for release"), "Fixed upstream");
        await confirm();
        await waitFor(() =>
            expect(service().releaseFromQuarantine).toHaveBeenCalledWith(
                "db1",
                "asset-1",
                "Fixed upstream"
            )
        );
        expect(
            await screen.findByText("Name of asset-1 released from quarantine.")
        ).toBeInTheDocument();
        expect(screen.queryByText(/db1:asset-1/)).not.toBeInTheDocument();
    });
});
