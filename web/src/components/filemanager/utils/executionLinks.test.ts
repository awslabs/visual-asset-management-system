/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    EXECUTION_DETAILS_API_ROUTE,
    WORKFLOW_EXECUTION_CHANGE_SOURCE,
    executionDetailPath,
    linkedExecutionId,
} from "./executionLinks";

describe("executionLinks", () => {
    it("links a workflow-written version to its execution", () => {
        expect(
            linkedExecutionId({
                changeSource: WORKFLOW_EXECUTION_CHANGE_SOURCE,
                changeWorkflowExecutionId: "exec-1",
            })
        ).toBe("exec-1");
    });

    it("does not link when the source is not a workflow execution", () => {
        // An upload stamps the workflow ids blank; a copy of a workflow output could carry the
        // source asset's id through metadata, so the source is what decides, not the id alone.
        expect(
            linkedExecutionId({ changeSource: "upload", changeWorkflowExecutionId: "exec-1" })
        ).toBeUndefined();
        expect(
            linkedExecutionId({ changeSource: "fileCopy", changeWorkflowExecutionId: "exec-1" })
        ).toBeUndefined();
    });

    it("does not link a workflow version that carries no execution id", () => {
        expect(
            linkedExecutionId({
                changeSource: WORKFLOW_EXECUTION_CHANGE_SOURCE,
                changeWorkflowExecutionId: "",
            })
        ).toBeUndefined();
        expect(
            linkedExecutionId({ changeSource: WORKFLOW_EXECUTION_CHANGE_SOURCE })
        ).toBeUndefined();
        expect(linkedExecutionId(null)).toBeUndefined();
        expect(linkedExecutionId(undefined)).toBeUndefined();
    });

    it("targets the execution detail route the quick view itself navigates to", () => {
        expect(executionDetailPath("abc123")).toBe("/executions/abc123");
    });

    it("gates on the execution details API route", () => {
        expect(EXECUTION_DETAILS_API_ROUTE).toBe("/workflows/executions/{executionId}/details");
    });
});
