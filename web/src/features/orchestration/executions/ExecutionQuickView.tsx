/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import QuickView from "../components/QuickView";
import StatusBadge from "../components/StatusBadge";
import { useExecutionDetails } from "../api/queries";

interface ExecutionQuickViewProps {
    open: boolean;
    onClose: () => void;
    executionId: string;
}

const ExecutionQuickView: React.FC<ExecutionQuickViewProps> = ({
    open,
    onClose,
    executionId,
}) => {
    const { data: details, isLoading, error } = useExecutionDetails(executionId);
    const navigate = useNavigate();

    const formatDate = (dateStr?: string) => {
        if (!dateStr) return "—";
        try {
            return new Date(dateStr).toLocaleString();
        } catch {
            return dateStr;
        }
    };

    const calculateDuration = (start?: string, stop?: string) => {
        if (!start) return "—";
        if (!stop) return "In progress";
        try {
            const startTime = new Date(start).getTime();
            const stopTime = new Date(stop).getTime();
            const durationMs = stopTime - startTime;
            const seconds = Math.floor(durationMs / 1000);
            const minutes = Math.floor(seconds / 60);
            const hours = Math.floor(minutes / 60);
            if (hours > 0) return `${hours}h ${minutes % 60}m`;
            if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
            return `${seconds}s`;
        } catch {
            return "—";
        }
    };

    const getResultsText = () => {
        if (!details?.outputs) return null;
        const { results } = details.outputs;
        if (!results || results.length === 0) return null;
        // Simple results extraction - can be enhanced based on actual data structure
        return JSON.stringify(results, null, 2);
    };

    return (
        <QuickView open={open} onClose={onClose} title="Execution Details">
            {isLoading && (
                <div className="text-gray-600 dark:text-gray-400">Loading...</div>
            )}
            {error && (
                <div className="text-red-600 dark:text-red-400">
                    Failed to load execution details
                </div>
            )}
            {details && (
                <div className="space-y-4">
                    {/* Status and timing */}
                    <div className="space-y-2">
                        <div className="flex items-center gap-2">
                            <span className="font-semibold text-gray-900 dark:text-gray-100">
                                Status:
                            </span>
                            <StatusBadge status={details.executionStatus} />
                        </div>
                        <div className="text-sm text-gray-700 dark:text-gray-300">
                            <div>
                                <span className="font-semibold">Started:</span>{" "}
                                {formatDate(details.executionStartDate)}
                            </div>
                            <div>
                                <span className="font-semibold">Stopped:</span>{" "}
                                {formatDate(details.executionStopDate)}
                            </div>
                            <div>
                                <span className="font-semibold">Duration:</span>{" "}
                                {calculateDuration(
                                    details.executionStartDate,
                                    details.executionStopDate
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Trigger info */}
                    <div className="text-sm text-gray-700 dark:text-gray-300">
                        <div>
                            <span className="font-semibold">Trigger:</span> {details.triggerType || "—"}
                        </div>
                        {details.triggeredByUserId && (
                            <div>
                                <span className="font-semibold">User:</span>{" "}
                                {details.triggeredByUserId}
                            </div>
                        )}
                    </div>

                    {/* Error if any */}
                    {details.executionError && (
                        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
                            <div className="font-semibold text-red-800 dark:text-red-300 mb-1">
                                Error:
                            </div>
                            <div className="text-sm text-red-700 dark:text-red-400 whitespace-pre-wrap">
                                {details.executionError}
                            </div>
                        </div>
                    )}

                    {/* Pipeline status strip */}
                    {details.pipelines && details.pipelines.length > 0 && (
                        <div>
                            <div className="font-semibold text-gray-900 dark:text-gray-100 mb-2">
                                Pipelines:
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {details.pipelines.map((pipeline: any, idx: number) => (
                                    <div
                                        key={idx}
                                        className="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                    >
                                        {pipeline.pipelineName || pipeline.pipelineId || `Pipeline ${idx + 1}`}
                                        {pipeline.status && ` (${pipeline.status})`}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Results */}
                    {getResultsText() && (
                        <div>
                            <div className="font-semibold text-gray-900 dark:text-gray-100 mb-2">
                                Results:
                            </div>
                            <pre className="p-3 bg-gray-100 dark:bg-gray-800 rounded text-xs overflow-auto max-h-64 text-gray-900 dark:text-gray-100">
                                {getResultsText()}
                            </pre>
                        </div>
                    )}

                    {/* Truncation notice */}
                    {details.truncatedCollections && details.truncatedCollections.length > 0 && (
                        <div className="text-xs text-yellow-700 dark:text-yellow-400">
                            Note: Some data collections were truncated:{" "}
                            {details.truncatedCollections.join(", ")}
                        </div>
                    )}

                    {/* Open full details link */}
                    <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                        <button
                            onClick={() => {
                                navigate(`/executions/${executionId}`);
                                onClose();
                            }}
                            className="text-blue-600 dark:text-blue-400 hover:underline text-sm"
                        >
                            Open full details →
                        </button>
                    </div>
                </div>
            )}
        </QuickView>
    );
};

export default ExecutionQuickView;
