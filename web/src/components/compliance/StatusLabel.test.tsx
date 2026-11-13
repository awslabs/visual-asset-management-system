/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Compliance status labels stay on one line. Cloudscape's status indicator lets its text break
 * between any two letters, so in the compliance tables, which wrap their columns, "Non-Compliant"
 * read "Non-Complia / nt" wherever its column was narrower than the label. Every state, verdict
 * and cascade status label is wrapped in a no-wrap span with its icon, so the table sizes the
 * column to the label. jsdom does no layout: the test pins the wrapper the browser lays out.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import {
    COMPLIANCE_STATE_INDICATORS,
    ComplianceStateBadge,
    EvaluationErrorIndicator,
    EVALUATION_ERROR_LABEL,
} from "./complianceStateBadge";
import ComplianceCascades from "../../pages/ComplianceCascades";

jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchCascades: jest.fn(),
}));

jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../services/ComplianceService");

/** The one-line wrapper of the label, asserted to hold the icon and the whole label. */
const expectOneLine = (label: string) => {
    const text = screen.getByText(label);
    const wrapper = text.closest("[data-status-label]");
    expect(wrapper).toBeInstanceOf(HTMLElement);
    const span = wrapper as HTMLElement;
    expect(span.style.whiteSpace).toBe("nowrap");
    expect(span.textContent?.trim()).toBe(label);
    // The status icon is inside the same no-wrap span, so it never parts from the label.
    expect(span.querySelector("svg")).not.toBeNull();
};

describe("compliance status labels stay on one line", () => {
    it.each(Object.entries(COMPLIANCE_STATE_INDICATORS))("the %s state", (state, indicator) => {
        render(<ComplianceStateBadge state={state} />);
        expectOneLine(indicator.label);
    });

    it("the evaluation error flag", () => {
        render(<EvaluationErrorIndicator lastEvaluationStatus="error" />);
        expectOneLine(EVALUATION_ERROR_LABEL);
    });

    it("the cascade status on the Cascade Approvals page", async () => {
        service().fetchCascades.mockResolvedValue([
            true,
            {
                cascades: [
                    {
                        cascadeId: "0f6f9c1e-1111-4222-8333-444455556666",
                        state: "pending_approval",
                        triggeredByDatabaseId: "db1",
                        triggeredByAssetId: "a1",
                        createdAt: "2026-10-27T04:46:43+00:00",
                        permissions: { approve: true },
                    },
                ],
            },
        ]);
        render(<ComplianceCascades />);
        await waitFor(() => expect(screen.getByText("Pending Approval")).toBeInTheDocument());
        expectOneLine("Pending Approval");
    });
});
