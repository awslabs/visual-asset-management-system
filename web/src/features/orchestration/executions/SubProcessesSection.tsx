/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import StageTimeline, { StageStatusBadge, durationBetween } from "./StageTimeline";
import type { SubExecution } from "../types";

interface SubProcessesSectionProps {
    /** Absent when the details were read without includeSubExecutions; then nothing is rendered. */
    subExecutions?: SubExecution[];
    subExecutionsTruncated?: boolean;
    warnings?: string[];
    /** The server dropped every stage list to keep the response inside its size budget. */
    stagesDropped?: boolean;
}

const formatDate = (iso?: string): string => (iso ? new Date(iso).toLocaleString() : "—");

/** A muted, single-line note under a sub-process header. */
const Note: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <p className="text-xs text-yellow-700 dark:text-yellow-400">{children}</p>
);

/**
 * The sub-processes a pipeline step registered while it ran — its state machine, container jobs, a
 * Deadline Cloud farm job — each with the status resolved for it and, for a state machine, the stage
 * timeline derived from its execution history. Registration is best-effort and the resolution is
 * bounded, so every way the list can be incomplete is stated next to what it qualifies.
 */
const SubProcessesSection: React.FC<SubProcessesSectionProps> = ({
    subExecutions,
    subExecutionsTruncated,
    warnings,
    stagesDropped,
}) => {
    if (!Array.isArray(subExecutions)) return null;
    const warningList = warnings || [];
    return (
        <div className="mb-4" data-testid="sub-processes">
            <h4 className="text-sm font-semibold mb-2">
                Sub-processes{subExecutions.length > 0 ? ` (${subExecutions.length})` : ""}
            </h4>
            {subExecutions.length === 0 && !subExecutionsTruncated && (
                <p className="text-sm text-text-secondary">
                    No sub-processes were registered for this step.
                </p>
            )}
            <div className="space-y-3">
                {subExecutions.map((sub, i) => {
                    const title = sub.label || sub.resourceName || sub.resourceType;
                    const duration = durationBetween(sub.startDate, sub.stopDate);
                    const errorText = [sub.error, sub.cause].filter(Boolean).join(": ");
                    const showStages = !stagesDropped && sub.stageSource !== "none";
                    return (
                        <div
                            key={`${sub.resourceType}-${sub.resourceName || ""}-${i}`}
                            data-testid="sub-process"
                            className="orch-outline p-3 border border-border-default rounded"
                        >
                            <div className="flex flex-wrap items-center gap-2 mb-1">
                                <span className="text-sm font-semibold">{title}</span>
                                {sub.resourceName && sub.resourceName !== title && (
                                    <span className="font-mono text-xs text-text-secondary break-all">
                                        {sub.resourceName}
                                    </span>
                                )}
                                {sub.stageName && (
                                    <span className="text-xs text-text-secondary">
                                        in {sub.stageName}
                                    </span>
                                )}
                                <StageStatusBadge status={sub.status} />
                            </div>
                            <p className="text-xs text-text-secondary mb-2">
                                {sub.resourceType} · {formatDate(sub.startDate)} →{" "}
                                {formatDate(sub.stopDate)}
                                {duration ? ` · ${duration}` : ""}
                            </p>
                            {sub.resourceType === "deadlineCloudJob" && sub.deadline && (
                                <p className="font-mono text-xs text-text-secondary break-all mb-2">
                                    Deadline Cloud job {sub.deadline.jobId} · farm{" "}
                                    {sub.deadline.farmId} · queue {sub.deadline.queueId}
                                </p>
                            )}
                            {errorText && (
                                <p className="text-sm text-vams-error whitespace-pre-wrap break-words mb-2">
                                    {errorText}
                                </p>
                            )}
                            {/* A drop empties each stage list and flags it stagesTruncated as well,
                                so that flag is only a definition-frame cap when nothing was dropped;
                                a sub-process that never had stages keeps its own reason. */}
                            {stagesDropped && sub.stageSource !== "none" && (
                                <Note>
                                    Stage details were left out because this execution&apos;s
                                    details exceeded the response size limit.
                                </Note>
                            )}
                            {sub.stageSource === "none" && (
                                <Note>No stage information is available for this sub-process.</Note>
                            )}
                            {!stagesDropped && sub.stageSource === "history" && (
                                <Note>
                                    The state machine definition could not be read; stages are
                                    listed in the order they were entered.
                                </Note>
                            )}
                            {!stagesDropped && sub.stagesTruncated && (
                                <Note>
                                    The state machine defines more stages than are listed here.
                                </Note>
                            )}
                            {sub.historyTruncated && (
                                <Note>
                                    Stage statuses come from a partial execution history and may lag
                                    behind the run.
                                </Note>
                            )}
                            {showStages && (
                                <div className="mt-2">
                                    <StageTimeline stages={sub.stages || []} />
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
            {subExecutionsTruncated && (
                <Note>More sub-processes were registered than this view reports.</Note>
            )}
            {warningList.length > 0 && (
                <ul className="mt-2 ml-4 list-disc text-xs text-text-secondary space-y-0.5">
                    {warningList.map((w, i) => (
                        <li key={i}>{w}</li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default SubProcessesSection;
