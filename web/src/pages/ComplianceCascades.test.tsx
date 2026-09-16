/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceCascades, {
    CASCADE_POLL_INTERVAL_MS,
    cascadeProgress,
    cascadeTriggerAssetId,
    cascadeTriggerDatabaseId,
} from "./ComplianceCascades";
import { REASON_REQUIRED_MESSAGE } from "../components/compliance/ReasonModal";

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

describe("ComplianceCascades", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().fetchCascades.mockResolvedValue([true, [pendingCascade()]]);
    });

    it("collects the rejection reason in a modal and sends it with the rejection", async () => {
        service().rejectCascade.mockResolvedValue([true, "Cascade rejected"]);

        render(<ComplianceCascades />);
        await screen.findByText("manual trigger");
        // The modal's confirm button shares the label; the table's button comes first in the DOM,
        // the modal portal's last.
        const tableReject = screen.getAllByRole("button", { name: "Reject" })[0];
        await userEvent.click(tableReject);

        // Confirming without a reason keeps the dialog open and sends nothing.
        const confirmButtons = screen.getAllByRole("button", { name: "Reject" });
        await userEvent.click(confirmButtons[confirmButtons.length - 1]);
        expect(screen.getByText(REASON_REQUIRED_MESSAGE)).toBeInTheDocument();
        expect(service().rejectCascade).not.toHaveBeenCalled();

        await userEvent.type(screen.getByLabelText("Reason for rejection"), "Not needed");
        const confirm = screen.getAllByRole("button", { name: "Reject" });
        await userEvent.click(confirm[confirm.length - 1]);

        await waitFor(() => {
            expect(service().rejectCascade).toHaveBeenCalledWith(CASCADE_ID, "Not needed");
        });
        expect(await screen.findByText("Cascade rejected")).toBeInTheDocument();
        expect(service().fetchCascades).toHaveBeenCalledTimes(2);
    });

    it("reports execution started on approval and follows the cascade to completion", async () => {
        service().approveCascade.mockResolvedValue([
            true,
            { message: "Cascade approved", cascadeId: CASCADE_ID, state: "executing" },
        ]);
        service().fetchCascade.mockResolvedValue([
            true,
            {
                ...pendingCascade(),
                state: "completed",
                nodes: JSON.stringify({ "db1#asset-1": "compliant", "db1#asset-2": "skipped" }),
                totalNodes: 2,
            },
        ]);

        render(<ComplianceCascades />);
        await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

        await waitFor(() => {
            expect(service().approveCascade).toHaveBeenCalledWith(CASCADE_ID);
        });
        expect(await screen.findByText(/execution started/)).toBeInTheDocument();
        await waitFor(() => {
            expect(service().fetchCascade).toHaveBeenCalledWith(CASCADE_ID);
        });
        expect(await screen.findByText(/completed$/)).toBeInTheDocument();
        expect(screen.getByText("2 of 2 nodes evaluated")).toBeInTheDocument();
        // The queue is re-read so the approved cascade leaves the pending list.
        expect(service().fetchCascades).toHaveBeenCalledTimes(2);
    });

    it("keeps polling while the cascade executes and shows the abort reason", async () => {
        jest.useFakeTimers();
        const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
        try {
            service().approveCascade.mockResolvedValue([
                true,
                { message: "Cascade approved", cascadeId: CASCADE_ID, state: "executing" },
            ]);
            service()
                .fetchCascade.mockResolvedValueOnce([
                    true,
                    {
                        ...pendingCascade(),
                        state: "executing",
                        nodes: JSON.stringify({ "db1#asset-1": "evaluating" }),
                        totalNodes: 1,
                    },
                ])
                .mockResolvedValueOnce([
                    true,
                    { ...pendingCascade(), state: "aborted", abortReason: "Executor failed" },
                ]);

            render(<ComplianceCascades />);
            await user.click(await screen.findByRole("button", { name: "Approve" }));

            expect(await screen.findByText(/is executing/)).toBeInTheDocument();
            expect(screen.getByText("0 of 1 nodes evaluated")).toBeInTheDocument();
            expect(service().fetchCascade).toHaveBeenCalledTimes(1);

            await act(async () => {
                jest.advanceTimersByTime(CASCADE_POLL_INTERVAL_MS);
            });

            expect(await screen.findByText(/aborted$/)).toBeInTheDocument();
            expect(screen.getByText("Executor failed")).toBeInTheDocument();
            expect(service().fetchCascade).toHaveBeenCalledTimes(2);
        } finally {
            jest.useRealTimers();
        }
    });

    it("labels the refresh control", async () => {
        render(<ComplianceCascades />);
        expect(await screen.findByRole("button", { name: "Refresh cascades" })).toBeInTheDocument();
    });

    it("shows the trigger asset from the listing's databaseId/assetId", async () => {
        service().fetchCascades.mockResolvedValue([
            true,
            [
                {
                    ...pendingCascade(),
                    databaseId: "listing-db",
                    assetId: "listing-asset",
                },
            ],
        ]);

        render(<ComplianceCascades />);
        expect(await screen.findByText("listing-db")).toBeInTheDocument();
        expect(screen.getByText("listing-asset")).toBeInTheDocument();
        expect(screen.queryByText("db1")).not.toBeInTheDocument();
    });

    it("falls back to the triggeredBy ids when the row carries no databaseId/assetId", async () => {
        render(<ComplianceCascades />);
        expect(await screen.findByText("db1")).toBeInTheDocument();
        expect(screen.getByText("asset-1")).toBeInTheDocument();
    });
});

describe("cascade trigger ids", () => {
    const base = {
        cascadeId: "c",
        state: "executing" as const,
        triggeredByDatabaseId: "db1",
        triggeredByAssetId: "a1",
        requireApproval: true,
        createdAt: "2026-01-01T00:00:00Z",
    };

    it("prefers the listing ids and falls back to the triggeredBy ids", () => {
        expect(cascadeTriggerDatabaseId(base)).toBe("db1");
        expect(cascadeTriggerAssetId(base)).toBe("a1");
        expect(cascadeTriggerDatabaseId({ ...base, databaseId: "db2", assetId: "a2" })).toBe("db2");
        expect(cascadeTriggerAssetId({ ...base, databaseId: "db2", assetId: "a2" })).toBe("a2");
    });
});

describe("cascadeProgress", () => {
    it("counts nodes that left pending/evaluating", () => {
        expect(
            cascadeProgress({
                cascadeId: "c",
                state: "executing",
                triggeredByDatabaseId: "db1",
                triggeredByAssetId: "a1",
                requireApproval: true,
                createdAt: "2026-01-01T00:00:00Z",
                nodes: JSON.stringify({
                    a: "pending",
                    b: "evaluating",
                    c: "compliant",
                    d: "error",
                }),
                totalNodes: 4,
            })
        ).toBe("2 of 4 nodes evaluated");
    });

    it("returns null without a node map or with an unreadable one", () => {
        const base = {
            cascadeId: "c",
            state: "executing" as const,
            triggeredByDatabaseId: "db1",
            triggeredByAssetId: "a1",
            requireApproval: true,
            createdAt: "2026-01-01T00:00:00Z",
        };
        expect(cascadeProgress(null)).toBeNull();
        expect(cascadeProgress(base)).toBeNull();
        expect(cascadeProgress({ ...base, nodes: "{not json" })).toBeNull();
    });
});
