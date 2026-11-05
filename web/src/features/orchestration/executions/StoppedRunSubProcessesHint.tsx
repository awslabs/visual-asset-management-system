/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useExecutionActions } from "../api/queries";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import type { ExecutionDetail, NotAbortableSubExecution } from "../types";
import { notAbortableSummary } from "./ExecutionsBoard";

const STOPPED_STATUSES = new Set(["FAILED", "ABORTED", "TIMED_OUT"]);

// Pipeline steps that finished on their own. Abort leaves their sub-processes alone, so a RUNNING
// sub-process under one is not counted: the hint offers Abort only for work Abort stops.
const FINISHED_ON_ITS_OWN_STATUSES = new Set(["SUCCEEDED", "FAILED"]);

/**
 * The sub-processes still RUNNING under an execution that has already stopped. A run stopped outside
 * VAMS (a direct Step Functions stop) ends the outer state machine only: the Batch, Deadline Cloud,
 * SageMaker or nested state-machine work its steps registered runs on, and the details read stops
 * none of it. Counts only steps Abort sweeps (unfinished, or marked ABORTED / TIMED_OUT). Empty for a
 * run that is still going or that finished cleanly.
 */
export function stillRunningSubProcessCount(execution?: ExecutionDetail): number {
    if (!execution || !STOPPED_STATUSES.has(execution.executionStatus)) return 0;
    return (execution.pipelines || []).reduce(
        (n: number, p: any) =>
            n +
            (Array.isArray(p?.subExecutions) &&
            !FINISHED_ON_ITS_OWN_STATUSES.has(p?.executionStatus)
                ? p.subExecutions.filter((s: any) => s?.status === "RUNNING").length
                : 0),
        0
    );
}

/**
 * Says that a stopped run still has sub-processes running, and offers the Abort that stops them. The
 * abort route sweeps an already-stopped run's pipeline steps again, so it is the remedy here too.
 */
const StoppedRunSubProcessesHint: React.FC<{
    executionId: string;
    count: number;
    canAbort: boolean;
}> = ({ executionId, count, canAbort }) => {
    const { abortExecution } = useExecutionActions();
    const toast = useToast();

    const onAbort = async () => {
        try {
            const result: any = await abortExecution.mutateAsync({ executionId });
            const notAbortable: NotAbortableSubExecution[] = Array.isArray(result?.notAbortable)
                ? result.notAbortable
                : [];
            const warnings: string[] = [
                ...(notAbortable.length ? [notAbortableSummary(notAbortable)] : []),
                ...(Array.isArray(result?.warnings) ? result.warnings : []),
            ];
            if (warnings.length) {
                toast.warning("Aborting with warnings", { description: warnings.join(" ") });
            } else {
                toast.success("Aborting sub-processes", {
                    description: "The execution's sub-processes were signalled to stop.",
                });
            }
        } catch (err) {
            toast.error("Abort failed", {
                description: toastErrorMessage(err, "Failed to abort execution"),
            });
        }
    };

    const noun = count === 1 ? "sub-process" : "sub-processes";
    return (
        <div
            role="status"
            data-testid="stopped-run-subprocesses-hint"
            className="orch-outline mt-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded"
        >
            <p className="text-sm text-yellow-800 dark:text-yellow-300">
                This execution has stopped, but {count} {noun} started by its pipeline steps{" "}
                {count === 1 ? "is" : "are"} still running and may keep using compute. Abort stops
                them.
            </p>
            {canAbort && (
                <button
                    type="button"
                    onClick={onAbort}
                    disabled={abortExecution.isPending}
                    className="orch-outline mt-2 px-3 py-1 text-sm font-semibold rounded border border-yellow-700 dark:border-yellow-400 text-yellow-800 dark:text-yellow-200 hover:bg-yellow-100 dark:hover:bg-yellow-900/40 disabled:opacity-50"
                >
                    {abortExecution.isPending ? "Aborting…" : "Abort"}
                </button>
            )}
        </div>
    );
};

export default StoppedRunSubProcessesHint;
