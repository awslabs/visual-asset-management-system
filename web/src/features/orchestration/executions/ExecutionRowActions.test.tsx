/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionRowActions from "./ExecutionRowActions";
import { SYSTEM_RECORD_REASONS } from "../permissions/systemRecordReasons";
import type { Execution } from "../types";

const run = (over: Partial<Execution> = {}): Execution => ({
    workflowExecutionId: "e1",
    workflowId: "wf-1",
    workflowDatabaseId: "db1",
    executionStatus: "SUCCEEDED",
    ...over,
});

const renderActions = (execution: Execution, onRerun = jest.fn()) => {
    render(
        <ExecutionRowActions
            execution={execution}
            can={() => true}
            onView={jest.fn()}
            onAbort={jest.fn()}
            onRerun={onRerun}
            onLogs={jest.fn()}
            onPermanentDelete={jest.fn()}
            onOpenDetails={jest.fn()}
        />
    );
    return onRerun;
};

describe("ExecutionRowActions Rerun", () => {
    it("lists Rerun disabled with its reason for a run a system feature started", async () => {
        const onRerun = renderActions(run({ triggerType: "System-ZipDownload", isSystem: true }));
        await userEvent.click(screen.getByLabelText("Execution actions"));

        const rerun = screen.getByRole("menuitem", { name: "Rerun" });
        expect(rerun).toHaveAttribute("aria-disabled", "true");
        expect(rerun).toHaveAttribute("aria-description", SYSTEM_RECORD_REASONS.rerunSystemRun);
        await userEvent.click(rerun);
        expect(onRerun).not.toHaveBeenCalled();
    });

    it.each(["manual", "fileUpload", undefined])(
        "[control] keeps Rerun available for a run with trigger %s",
        async (triggerType) => {
            const onRerun = renderActions(run({ triggerType }));
            await userEvent.click(screen.getByLabelText("Execution actions"));
            const rerun = screen.getByRole("menuitem", { name: "Rerun" });
            expect(rerun).not.toHaveAttribute("aria-disabled");
            await userEvent.click(rerun);
            expect(onRerun).toHaveBeenCalledTimes(1);
        }
    );
});
