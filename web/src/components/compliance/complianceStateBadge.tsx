/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { BadgeProps } from "@cloudscape-design/components/badge";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { ComplianceStateValue } from "../../services/ComplianceService";

export interface ComplianceStateIndicator {
    type: StatusIndicatorProps.Type;
    label: string;
    /** Color of the state where it is shown as a Cloudscape Badge rather than a status indicator. */
    badgeColor: NonNullable<BadgeProps["color"]>;
}

/** Indicator type, label and badge color of every compliance state, as rendered wherever a state is shown. */
export const COMPLIANCE_STATE_INDICATORS: Record<ComplianceStateValue, ComplianceStateIndicator> = {
    unknown: { type: "stopped", label: "Unknown", badgeColor: "grey" },
    pending_evaluation: { type: "in-progress", label: "Pending Evaluation", badgeColor: "grey" },
    compliant: { type: "success", label: "Compliant", badgeColor: "green" },
    non_compliant: { type: "warning", label: "Non-Compliant", badgeColor: "red" },
    quarantined: { type: "error", label: "Quarantined", badgeColor: "red" },
    exception: { type: "info", label: "Exception", badgeColor: "blue" },
    pending_parent_resolution: {
        type: "warning",
        label: "Pending Parent Resolution",
        badgeColor: "grey",
    },
};

/** The indicator of a state; a missing or unrecognised state renders as Unknown. */
export function complianceStateIndicator(state?: string | null): ComplianceStateIndicator {
    if (state && state in COMPLIANCE_STATE_INDICATORS) {
        return COMPLIANCE_STATE_INDICATORS[state as ComplianceStateValue];
    }
    return COMPLIANCE_STATE_INDICATORS.unknown;
}

/** The Badge color of a state, with the same Unknown fallback as the indicator. */
export function complianceStateBadgeColor(state?: string | null): NonNullable<BadgeProps["color"]> {
    return complianceStateIndicator(state).badgeColor;
}

interface ComplianceStateBadgeProps {
    state?: string | null;
}

/** A compliance state as a Cloudscape status indicator. */
export const ComplianceStateBadge: React.FC<ComplianceStateBadgeProps> = ({ state }) => {
    const indicator = complianceStateIndicator(state);
    return <StatusIndicator type={indicator.type}>{indicator.label}</StatusIndicator>;
};

/** Label of the indicator shown beside a state whose last evaluation ended in an error. */
export const EVALUATION_ERROR_LABEL = "Evaluation error";

interface EvaluationErrorIndicatorProps {
    /** The record's `lastEvaluationStatus`; the indicator renders only for `error`. */
    lastEvaluationStatus?: string | null;
}

/**
 * Flags a compliance record whose last evaluation could not produce a verdict — every rule of it
 * errored, or its schema could not be loaded — so the state shown beside it is the one the asset
 * held before that evaluation. Renders nothing for any other status.
 */
export const EvaluationErrorIndicator: React.FC<EvaluationErrorIndicatorProps> = ({
    lastEvaluationStatus,
}) => {
    if (lastEvaluationStatus !== "error") {
        return null;
    }
    return <StatusIndicator type="error">{EVALUATION_ERROR_LABEL}</StatusIndicator>;
};

export default ComplianceStateBadge;
