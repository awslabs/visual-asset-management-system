/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import {
    ComplianceExecutionFailure,
    ComplianceRuleResult,
    executionFailureReasons,
} from "../../services/ComplianceService";
import { WRAP_ANYWHERE_STYLE } from "./rowActions";

export interface ExecutionFailureReasonsProps {
    /** Rule results or a quarantined state's execution failures. */
    entries: Array<ComplianceRuleResult | ComplianceExecutionFailure> | null | undefined;
    /** Prefix each line with the rule name (for a list over several rules). */
    showRuleName?: boolean;
}

/**
 * Why the workflow executions of failed pipeline rules failed, one line per rule. The API returns a
 * reason only to a caller who may read that execution, so nothing renders for any other caller and
 * the rule keeps its generic message. A reason quotes file names and parser output that may hold
 * long unbroken tokens, so it wraps anywhere.
 */
const ExecutionFailureReasons: React.FC<ExecutionFailureReasonsProps> = ({
    entries,
    showRuleName = true,
}) => {
    const reasons = executionFailureReasons(entries);
    if (reasons.length === 0) return null;
    return (
        <>
            {reasons.map(({ ruleName, reason }, index) => (
                <Box
                    key={`${ruleName}-${index}`}
                    variant="small"
                    display="block"
                    color="text-body-secondary"
                >
                    <span data-execution-failure-reason="" style={WRAP_ANYWHERE_STYLE}>
                        {showRuleName ? `Pipeline failure (${ruleName}): ` : "Pipeline failure: "}
                        {reason}
                    </span>
                </Box>
            ))}
        </>
    );
};

export default ExecutionFailureReasons;
