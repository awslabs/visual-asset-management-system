/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { ComplianceStateValue } from "../../services/ComplianceService";

export interface ComplianceStateIndicator {
    type: StatusIndicatorProps.Type;
    label: string;
}

/** Indicator type and label of every compliance state, as rendered wherever a state is shown. */
export const COMPLIANCE_STATE_INDICATORS: Record<ComplianceStateValue, ComplianceStateIndicator> = {
    unknown: { type: "stopped", label: "Unknown" },
    pending_evaluation: { type: "in-progress", label: "Pending Evaluation" },
    compliant: { type: "success", label: "Compliant" },
    non_compliant: { type: "warning", label: "Non-Compliant" },
    quarantined: { type: "error", label: "Quarantined" },
    exception: { type: "info", label: "Exception" },
    pending_parent_resolution: { type: "warning", label: "Pending Parent Resolution" },
};

/** The indicator of a state; a missing or unrecognised state renders as Unknown. */
export function complianceStateIndicator(state?: string | null): ComplianceStateIndicator {
    if (state && state in COMPLIANCE_STATE_INDICATORS) {
        return COMPLIANCE_STATE_INDICATORS[state as ComplianceStateValue];
    }
    return COMPLIANCE_STATE_INDICATORS.unknown;
}

interface ComplianceStateBadgeProps {
    state?: string | null;
}

/** A compliance state as a Cloudscape status indicator. */
export const ComplianceStateBadge: React.FC<ComplianceStateBadgeProps> = ({ state }) => {
    const indicator = complianceStateIndicator(state);
    return <StatusIndicator type={indicator.type}>{indicator.label}</StatusIndicator>;
};

export default ComplianceStateBadge;
