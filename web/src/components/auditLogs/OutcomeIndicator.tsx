/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { visibleText } from "./auditText";

const TYPES: Record<string, StatusIndicatorProps.Type> = {
    success: "success",
    failed: "error",
    denied: "warning",
};

const LABELS: Record<string, string> = {
    success: "Success",
    failed: "Failed",
    denied: "Denied",
};

/** An outcome as an icon and a word, so colour is never the only carrier. */
export const OutcomeIndicator: React.FC<{ outcome?: string | null }> = ({ outcome }) => {
    if (!outcome) return <span>-</span>;
    const key = outcome.toLowerCase();
    return (
        <StatusIndicator type={TYPES[key] ?? "info"}>
            {LABELS[key] ?? visibleText(outcome)}
        </StatusIndicator>
    );
};

export default OutcomeIndicator;
