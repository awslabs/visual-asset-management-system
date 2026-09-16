/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import {
    COMPLIANCE_STATE_INDICATORS,
    ComplianceStateBadge,
    EvaluationErrorIndicator,
    complianceStateBadgeColor,
    complianceStateIndicator,
} from "./complianceStateBadge";

describe("complianceStateBadge", () => {
    it("maps every compliance state, with exception as an info indicator", () => {
        expect(Object.keys(COMPLIANCE_STATE_INDICATORS).sort()).toEqual([
            "compliant",
            "exception",
            "non_compliant",
            "pending_evaluation",
            "pending_parent_resolution",
            "quarantined",
            "unknown",
        ]);
        expect(complianceStateIndicator("exception")).toEqual({
            type: "info",
            label: "Exception",
            badgeColor: "blue",
        });
        expect(complianceStateIndicator("quarantined")).toEqual({
            type: "error",
            label: "Quarantined",
            badgeColor: "red",
        });
    });

    it("falls back to Unknown for a missing or unrecognised state", () => {
        expect(complianceStateIndicator(undefined)).toEqual(COMPLIANCE_STATE_INDICATORS.unknown);
        expect(complianceStateIndicator(null)).toEqual(COMPLIANCE_STATE_INDICATORS.unknown);
        expect(complianceStateIndicator("bogus")).toEqual(COMPLIANCE_STATE_INDICATORS.unknown);
    });

    it("gives every state a badge color from the same map, with the same fallback", () => {
        expect(complianceStateBadgeColor("compliant")).toBe("green");
        expect(complianceStateBadgeColor("quarantined")).toBe("red");
        expect(complianceStateBadgeColor("exception")).toBe("blue");
        expect(complianceStateBadgeColor("bogus")).toBe(
            COMPLIANCE_STATE_INDICATORS.unknown.badgeColor
        );
        expect(complianceStateBadgeColor(undefined)).toBe("grey");
    });

    it("renders the label of the state", () => {
        render(<ComplianceStateBadge state="exception" />);
        expect(screen.getByText("Exception")).toBeInTheDocument();
    });

    it("renders the evaluation-error indicator only for an errored last evaluation", () => {
        const { rerender } = render(<EvaluationErrorIndicator lastEvaluationStatus="error" />);
        expect(screen.getByText("Evaluation error")).toBeInTheDocument();

        rerender(<EvaluationErrorIndicator lastEvaluationStatus="completed" />);
        expect(screen.queryByText("Evaluation error")).not.toBeInTheDocument();

        rerender(<EvaluationErrorIndicator lastEvaluationStatus={undefined} />);
        expect(screen.queryByText("Evaluation error")).not.toBeInTheDocument();
    });
});
