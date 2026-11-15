/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An approval or rejection the API refuses on the Cascade Approvals page is followed by a fresh read
 * of the listing, as a successful one is (D162). A cascade approved or rejected in another tab, or
 * whose approval timed out, kept its pending row with Approve and Reject until the page was
 * reloaded. Now the refusal stays on the page and names the cascade, and the rows follow the
 * cascades as the API holds them after the refusal.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceCascades from "./ComplianceCascades";

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchCascades: jest.fn(),
    fetchCascade: jest.fn(),
    approveCascade: jest.fn(),
    rejectCascade: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const CASCADE_ID = "0f6f9c1e-1111-4222-8333-444455556666";
const SHORT_ID = `${CASCADE_ID.slice(0, 8)}...`;

const pendingCascade = () => ({
    cascadeId: CASCADE_ID,
    state: "pending_approval",
    triggeredByDatabaseId: "db1",
    triggeredByAssetId: "asset-1",
    triggerReason: "manual trigger",
    createdAt: "2026-01-01T00:00:00Z",
    actor: "reviewer",
    requireApproval: true,
});

// The pending listing each read answers, in order; the last one repeats.
const listingsRead = (...pages: object[][]) => {
    let reads = 0;
    service().fetchCascades.mockImplementation(async () => {
        const page = pages[Math.min(reads, pages.length - 1)];
        reads += 1;
        return [true, { cascades: page }];
    });
};

// The row's button opens the reason dialog, whose confirm button shares the label (the table's
// button comes first in the DOM, the modal portal's last). The reason is optional and left empty.
const decide = async (label: "Approve" | "Reject") => {
    await screen.findByText("manual trigger");
    await userEvent.click(screen.getAllByRole("button", { name: label })[0]);
    const confirm = screen.getAllByRole("button", { name: label });
    await userEvent.click(confirm[confirm.length - 1]);
};

beforeEach(() => {
    jest.clearAllMocks();
});

describe("ComplianceCascades after a refused decision (D162)", () => {
    it("reads the listing again after a refused approval and drops the decided cascade", async () => {
        // Another user approved it; this page still lists it as pending.
        listingsRead([pendingCascade()], []);
        service().approveCascade.mockResolvedValue([false, "Cascade is not pending approval"]);

        render(<ComplianceCascades />);
        await decide("Approve");

        await waitFor(() => expect(service().fetchCascades).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByText("manual trigger")).toBeNull());
        expect(
            screen.getByText(
                `Cascade ${SHORT_ID} was not approved: Cascade is not pending approval`
            )
        ).toBeInTheDocument();
        // No cascade is followed: none was started.
        expect(service().fetchCascade).not.toHaveBeenCalled();
    });

    it("reads the listing again after a refused rejection", async () => {
        listingsRead([pendingCascade()], []);
        service().rejectCascade.mockResolvedValue([false, "Cascade is not pending approval"]);

        render(<ComplianceCascades />);
        await decide("Reject");

        await waitFor(() => expect(service().fetchCascades).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByText("manual trigger")).toBeNull());
        expect(
            screen.getByText(
                `Cascade ${SHORT_ID} was not rejected: Cascade is not pending approval`
            )
        ).toBeInTheDocument();
    });
});
