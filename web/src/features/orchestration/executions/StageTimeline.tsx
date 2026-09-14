/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import StatusBadge from "../components/StatusBadge";
import type { ExecutionStatus, SubExecutionStage, SubExecutionStatus } from "../types";

/**
 * Status badge for a sub-process or one of its stages. The two statuses the execution badge does not
 * know — a stage the run never reached, and one whose status could not be resolved — are rendered as
 * neutral text rather than as the raw enum value.
 */
export const StageStatusBadge: React.FC<{ status: SubExecutionStatus; caught?: boolean }> = ({
    status,
    caught,
}) => (
    <span className="inline-flex items-center gap-1.5">
        {status === "NOT_STARTED" || status === "UNKNOWN" ? (
            <span className="orch-outline inline-flex items-center px-2.5 py-1 rounded-full text-sm font-medium border border-border-default text-text-secondary">
                {status === "NOT_STARTED" ? "Not started" : "Unknown"}
            </span>
        ) : (
            <StatusBadge status={status as ExecutionStatus} />
        )}
        {caught && (
            <span
                className="text-xs text-text-secondary"
                title="The state machine caught this failure and ran on to its end state."
            >
                caught
            </span>
        )}
    </span>
);

/** "1h 2m" / "3m 4s" / "5s" between two ISO timestamps; null when either is missing or unreadable. */
export function durationBetween(start?: string, stop?: string): string | null {
    if (!start || !stop) return null;
    const ms = new Date(stop).getTime() - new Date(start).getTime();
    if (!Number.isFinite(ms) || ms < 0) return null;
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    if (hours > 0) return `${hours}h ${minutes % 60}m`;
    if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
    return `${seconds}s`;
}

/** Map-state iteration counts on one line; zero counts are left out so it names only what happened. */
function iterationsText(iterations: NonNullable<SubExecutionStage["iterations"]>): string {
    const parts = (["started", "succeeded", "failed", "aborted"] as const)
        .filter((k) => iterations[k] > 0)
        .map((k) => `${iterations[k]} ${k}`);
    return parts.length ? `iterations: ${parts.join(", ")}` : "";
}

interface StageTimelineProps {
    stages: SubExecutionStage[];
}

/**
 * The stages of a registered sub-state-machine in definition order, one row each: name, state type,
 * status, duration, retries or Map iterations, and the recorded error for a failed stage.
 */
const StageTimeline: React.FC<StageTimelineProps> = ({ stages }) => {
    if (stages.length === 0) {
        return <p className="text-sm text-text-secondary">No stages reported.</p>;
    }
    return (
        <ol aria-label="Stage timeline" data-testid="stage-timeline" className="space-y-1.5">
            {stages.map((stage, i) => {
                const duration = durationBetween(stage.startDate, stage.stopDate);
                const attempts = stage.attempts ?? 0;
                const iterations = stage.iterations ? iterationsText(stage.iterations) : "";
                const errorText = [stage.error, stage.cause].filter(Boolean).join(": ");
                return (
                    <li
                        key={`${stage.stageName}-${i}`}
                        className="orch-outline flex flex-wrap items-center gap-2 px-3 py-1.5 border border-border-default rounded bg-surface-secondary text-sm"
                    >
                        <span className="font-mono break-all">{stage.stageName}</span>
                        {stage.stateType && (
                            <span className="orch-outline px-1.5 py-0.5 text-xs rounded border border-border-default text-text-secondary">
                                {stage.stateType}
                            </span>
                        )}
                        <StageStatusBadge status={stage.status} caught={stage.caught} />
                        {duration && <span className="text-text-secondary">{duration}</span>}
                        {attempts > 1 && (
                            <span className="text-text-secondary">{attempts} attempts</span>
                        )}
                        {iterations && <span className="text-text-secondary">{iterations}</span>}
                        {stage.distributed && (
                            <span className="text-text-secondary">distributed map</span>
                        )}
                        {stage.batch?.jobId && (
                            <span className="font-mono text-xs text-text-secondary">
                                job {stage.batch.jobId}
                            </span>
                        )}
                        {errorText && (
                            <p className="w-full text-vams-error whitespace-pre-wrap break-words">
                                {errorText}
                            </p>
                        )}
                    </li>
                );
            })}
        </ol>
    );
};

export default StageTimeline;
