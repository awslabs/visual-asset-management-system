/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A row action the API refuses on the Quarantine page is followed by a fresh read of the listing,
 * as a successful one is (D162). An asset released in another tab, or whose record changed while
 * the request was made (D159), kept its row with Release and Exception until the page was reloaded,
 * because the listing was read again only after a success. Now the refusal stays on the page and
 * names the asset, and the rows follow the records as the API holds them after the refusal.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../synonyms";
import ComplianceQuarantine from "./ComplianceQuarantine";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    releaseFromQuarantine: jest.fn(),
    grantException: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) =>
            method === "POST" && path.startsWith("/compliance/quarantine/"),
    }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const RECORD_CHANGED =
    "The asset's compliance record changed while the request was made; try again";

const quarantined = (assetId: string) => ({
    databaseId: "db1",
    assetId,
    assetName: `Name of ${assetId}`,
    complianceState: "quarantined",
    schemaName: "std",
    quarantineReason: "metadata missing",
    permissions: { release: true, exception: true },
});

// The listing each read answers, in order; the last one repeats.
const listingsRead = (...pages: object[][]) => {
    let reads = 0;
    service().fetchQuarantinedAssets.mockImplementation(async () => {
        const page = pages[Math.min(reads, pages.length - 1)];
        reads += 1;
        return [true, { quarantinedAssets: page, nextToken: undefined }];
    });
};

const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

const confirmIn = async (dialogName: string, reasonLabel: string, confirm: string) => {
    const dialog = screen.getByRole("dialog", { name: dialogName });
    await userEvent.type(within(dialog).getByLabelText(reasonLabel), "Reviewed");
    await userEvent.click(within(dialog).getByRole("button", { name: confirm }));
};

beforeEach(() => {
    jest.clearAllMocks();
});

describe("ComplianceQuarantine after a refused action (D162)", () => {
    it("reads the listing again after a refused release and drops the released row", async () => {
        // asset-1 was released in another tab; this page still lists it.
        listingsRead([quarantined("asset-1"), quarantined("asset-2")], [quarantined("asset-2")]);
        service().releaseFromQuarantine.mockResolvedValue([false, "Asset is not quarantined"]);

        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        await userEvent.click(
            within(rowOf("Name of asset-1")).getByRole("button", { name: "Release" })
        );
        await confirmIn(
            "Release Name of asset-1 from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );

        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByText("Name of asset-1")).toBeNull());
        // The refusal stays and names the asset, whose row has left the page.
        expect(
            screen.getByText("Name of asset-1 was not released: Asset is not quarantined")
        ).toBeInTheDocument();
        expect(screen.getByText("Name of asset-2")).toBeInTheDocument();
        expect(screen.getByText("(1)")).toBeInTheDocument();
    });

    it("reads the listing again after a refused exception grant", async () => {
        listingsRead([quarantined("asset-1")], []);
        service().grantException.mockResolvedValue([false, RECORD_CHANGED]);

        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        await userEvent.click(
            within(rowOf("Name of asset-1")).getByRole("button", { name: "Exception" })
        );
        await confirmIn(
            "Grant exception for Name of asset-1",
            "Reason for exception",
            "Grant exception"
        );

        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByText("Name of asset-1")).toBeNull());
        expect(
            screen.getByText(`No exception was granted for Name of asset-1: ${RECORD_CHANGED}`)
        ).toBeInTheDocument();
    });

    it("keeps the refusal while a row that is still quarantined offers its actions again", async () => {
        listingsRead([quarantined("asset-1")]);
        service().releaseFromQuarantine.mockResolvedValue([false, RECORD_CHANGED]);

        render(<ComplianceQuarantine />);
        await screen.findByText("Name of asset-1");
        await userEvent.click(
            within(rowOf("Name of asset-1")).getByRole("button", { name: "Release" })
        );
        await confirmIn(
            "Release Name of asset-1 from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );

        await waitFor(() => expect(service().fetchQuarantinedAssets).toHaveBeenCalledTimes(2));
        expect(
            await screen.findByText(`Name of asset-1 was not released: ${RECORD_CHANGED}`)
        ).toBeInTheDocument();
        expect(
            within(rowOf("Name of asset-1")).getByRole("button", { name: "Release" })
        ).toBeEnabled();
    });
});
