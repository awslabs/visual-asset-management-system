/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useExecutionDetails } from "../api/queries";
import { getExecutionLogs } from "../api/executions";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import StatusBadge from "../components/StatusBadge";
import ConfigEditor from "../components/ConfigEditor";
import type { ExecutionStatus } from "../types";

interface ExecutionDetailPageProps {
    executionId: string;
}

const ExecutionDetailPage: React.FC<ExecutionDetailPageProps> = ({ executionId }) => {
    const { data: execution, isLoading, error } = useExecutionDetails(executionId);
    const { can } = useAllowedRoutes();
    const [activeTab, setActiveTab] = useState<"inputs" | "pipelines" | "outputs" | "logs">("inputs");
    const [logs, setLogs] = useState<string | null>(null);
    const [loadingLogs, setLoadingLogs] = useState(false);

    const canViewLogs = can("GET", "/workflows/executions/{executionId}/logs");

    const fetchLogs = async () => {
        if (loadingLogs || logs !== null) return;
        setLoadingLogs(true);
        try {
            const [ok, data] = await getExecutionLogs(executionId);
            if (ok && typeof data === "object") {
                setLogs(JSON.stringify(data, null, 2));
            } else {
                setLogs("Failed to load logs");
            }
        } catch (err: any) {
            setLogs(`Error: ${err?.message || "Unknown error"}`);
        } finally {
            setLoadingLogs(false);
        }
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4" />
                    <p>Loading execution details...</p>
                </div>
            </div>
        );
    }

    if (error || !execution) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <p className="text-red-600 dark:text-red-400 text-xl mb-2">Error Loading Execution</p>
                    <p className="text-gray-600 dark:text-gray-400">
                        {error ? String(error) : "Execution not found"}
                    </p>
                </div>
            </div>
        );
    }

    const duration = execution.executionStartDate && execution.executionStopDate
        ? calculateDuration(execution.executionStartDate, execution.executionStopDate)
        : null;

    return (
        <div className="min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 p-6">
            {/* Header */}
            <div className="mb-6 border-b border-gray-200 dark:border-gray-700 pb-4">
                <div className="flex items-center gap-3 mb-3">
                    <h1 className="text-2xl font-bold">Execution Detail</h1>
                    <StatusBadge status={execution.executionStatus as ExecutionStatus} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Execution ID:</span>{" "}
                        <span className="font-mono">{execution.workflowExecutionId}</span>
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Workflow ID:</span>{" "}
                        <span className="font-mono">{execution.workflowId}</span>
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Database ID:</span>{" "}
                        <span className="font-mono">{execution.workflowDatabaseId}</span>
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Trigger:</span>{" "}
                        {execution.triggerType || "N/A"} {execution.triggeredByUserId && `by ${execution.triggeredByUserId}`}
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Start:</span>{" "}
                        {execution.executionStartDate ? formatDate(execution.executionStartDate) : "N/A"}
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">Stop:</span>{" "}
                        {execution.executionStopDate ? formatDate(execution.executionStopDate) : "N/A"}
                    </div>
                    {duration && (
                        <div>
                            <span className="text-gray-500 dark:text-gray-400">Duration:</span>{" "}
                            {duration}
                        </div>
                    )}
                    {execution.executionGroupId && (
                        <div>
                            <span className="text-gray-500 dark:text-gray-400">Group ID:</span>{" "}
                            <span className="font-mono">{execution.executionGroupId}</span>
                        </div>
                    )}
                </div>
                {execution.executionError && (
                    <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
                        <p className="text-sm font-semibold text-red-800 dark:text-red-300 mb-1">Execution Error:</p>
                        <p className="text-sm text-red-700 dark:text-red-400">{execution.executionError}</p>
                    </div>
                )}
                {execution.truncatedCollections && execution.truncatedCollections.length > 0 && (
                    <div className="mt-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded">
                        <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300 mb-1">Warning:</p>
                        <p className="text-sm text-yellow-700 dark:text-yellow-400">
                            Some collections were truncated: {execution.truncatedCollections.join(", ")}
                        </p>
                    </div>
                )}
            </div>

            {/* Tabs */}
            <div className="mb-6">
                <div className="border-b border-gray-200 dark:border-gray-700">
                    <nav className="flex gap-4">
                        <button
                            onClick={() => setActiveTab("inputs")}
                            className={`px-4 py-2 border-b-2 font-medium text-sm transition-colors ${
                                activeTab === "inputs"
                                    ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                    : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
                            }`}
                        >
                            Inputs
                        </button>
                        <button
                            onClick={() => setActiveTab("pipelines")}
                            className={`px-4 py-2 border-b-2 font-medium text-sm transition-colors ${
                                activeTab === "pipelines"
                                    ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                    : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
                            }`}
                        >
                            Pipelines
                        </button>
                        <button
                            onClick={() => setActiveTab("outputs")}
                            className={`px-4 py-2 border-b-2 font-medium text-sm transition-colors ${
                                activeTab === "outputs"
                                    ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                    : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
                            }`}
                        >
                            Outputs
                        </button>
                        {canViewLogs && (
                            <button
                                onClick={() => {
                                    setActiveTab("logs");
                                    fetchLogs();
                                }}
                                className={`px-4 py-2 border-b-2 font-medium text-sm transition-colors ${
                                    activeTab === "logs"
                                        ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                        : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
                                }`}
                            >
                                Logs
                            </button>
                        )}
                    </nav>
                </div>
            </div>

            {/* Tab Content */}
            <div>
                {activeTab === "inputs" && (
                    <div>
                        <h2 className="text-xl font-semibold mb-4">Input Files</h2>
                        {execution.inputFiles && execution.inputFiles.length > 0 ? (
                            <div className="space-y-2">
                                {execution.inputFiles.map((input: any, idx: number) => (
                                    <div
                                        key={idx}
                                        className="p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded"
                                    >
                                        <div className="grid grid-cols-2 gap-2 text-sm">
                                            <div>
                                                <span className="text-gray-500 dark:text-gray-400">Database:</span>{" "}
                                                <span className="font-mono">{input.databaseId}</span>
                                            </div>
                                            <div>
                                                <span className="text-gray-500 dark:text-gray-400">Asset:</span>{" "}
                                                <span className="font-mono">{input.assetId}</span>
                                            </div>
                                            <div>
                                                <span className="text-gray-500 dark:text-gray-400">File:</span>{" "}
                                                <span className="font-mono">{input.inputAssetFileKey}</span>
                                            </div>
                                            {input.versionId && (
                                                <div>
                                                    <span className="text-gray-500 dark:text-gray-400">Version:</span>{" "}
                                                    <span className="font-mono">{input.versionId}</span>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-gray-500 dark:text-gray-400">No input files</p>
                        )}
                    </div>
                )}

                {activeTab === "pipelines" && (
                    <div>
                        <h2 className="text-xl font-semibold mb-4">Pipeline Timeline</h2>
                        {execution.pipelines && execution.pipelines.length > 0 ? (
                            <div className="space-y-6">
                                {execution.pipelines.map((pipeline: any, idx: number) => (
                                    <div
                                        key={idx}
                                        className="p-4 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded"
                                    >
                                        <div className="flex items-center gap-3 mb-3">
                                            <h3 className="text-lg font-semibold">{pipeline.pipelineName || "Unknown Pipeline"}</h3>
                                            {pipeline.executionStatus && (
                                                <StatusBadge status={pipeline.executionStatus as ExecutionStatus} />
                                            )}
                                        </div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm mb-4">
                                            <div>
                                                <span className="text-gray-500 dark:text-gray-400">Pipeline ID:</span>{" "}
                                                <span className="font-mono">{pipeline.pipelineId}</span>
                                            </div>
                                            {pipeline.executionStartDate && (
                                                <div>
                                                    <span className="text-gray-500 dark:text-gray-400">Start:</span>{" "}
                                                    {formatDate(pipeline.executionStartDate)}
                                                </div>
                                            )}
                                            {pipeline.executionStopDate && (
                                                <div>
                                                    <span className="text-gray-500 dark:text-gray-400">Stop:</span>{" "}
                                                    {formatDate(pipeline.executionStopDate)}
                                                </div>
                                            )}
                                        </div>

                                        {/* Template Snapshot */}
                                        {(pipeline.templateId || pipeline.templateTags || pipeline.customTemplateOverrideUsed !== undefined) && (
                                            <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded">
                                                <h4 className="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-2">Template Snapshot</h4>
                                                {pipeline.templateId && (
                                                    <div className="text-sm mb-1">
                                                        <span className="text-blue-700 dark:text-blue-400">Template ID:</span>{" "}
                                                        <span className="font-mono">{pipeline.templateId}</span>
                                                    </div>
                                                )}
                                                {pipeline.templateTags && Object.keys(pipeline.templateTags).length > 0 && (
                                                    <div className="text-sm mb-1">
                                                        <span className="text-blue-700 dark:text-blue-400">Tags:</span>{" "}
                                                        <span className="font-mono">{JSON.stringify(pipeline.templateTags)}</span>
                                                    </div>
                                                )}
                                                {pipeline.customTemplateOverrideUsed !== undefined && (
                                                    <div className="text-sm">
                                                        <span className="text-blue-700 dark:text-blue-400">Custom Override:</span>{" "}
                                                        {pipeline.customTemplateOverrideUsed ? "Yes" : "No"}
                                                    </div>
                                                )}
                                            </div>
                                        )}

                                        {/* Rendered Config Body */}
                                        {pipeline.renderedConfigBody && (
                                            <div>
                                                <h4 className="text-sm font-semibold mb-2">Executed Configuration</h4>
                                                <div className="border border-gray-300 dark:border-gray-600 rounded overflow-hidden">
                                                    <ConfigEditor
                                                        value={pipeline.renderedConfigBody}
                                                        language={pipeline.configFormat || "json"}
                                                        readOnly
                                                        height="300px"
                                                    />
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-gray-500 dark:text-gray-400">No pipeline data</p>
                        )}
                    </div>
                )}

                {activeTab === "outputs" && (
                    <div>
                        <h2 className="text-xl font-semibold mb-4">Outputs</h2>

                        {/* Files */}
                        {execution.outputs?.files && execution.outputs.files.length > 0 && (
                            <div className="mb-6">
                                <h3 className="text-lg font-semibold mb-3">Files</h3>
                                <div className="space-y-2">
                                    {execution.outputs.files.map((file: any, idx: number) => (
                                        <div
                                            key={idx}
                                            className="p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded"
                                        >
                                            <div className="text-sm">
                                                <span className="text-gray-500 dark:text-gray-400">Path:</span>{" "}
                                                <span className="font-mono">{file.relativeFilePath}</span>
                                            </div>
                                            {file.size && (
                                                <div className="text-sm">
                                                    <span className="text-gray-500 dark:text-gray-400">Size:</span>{" "}
                                                    {formatBytes(file.size)}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Metadata */}
                        {execution.outputs?.metadata && execution.outputs.metadata.length > 0 && (
                            <div className="mb-6">
                                <h3 className="text-lg font-semibold mb-3">Metadata</h3>
                                <div className="space-y-2">
                                    {execution.outputs.metadata.map((meta: any, idx: number) => (
                                        <div
                                            key={idx}
                                            className="p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded"
                                        >
                                            <pre className="text-xs overflow-auto">{JSON.stringify(meta, null, 2)}</pre>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Results */}
                        {execution.outputs?.results && execution.outputs.results.length > 0 && (
                            <div className="mb-6">
                                <h3 className="text-lg font-semibold mb-3">Results</h3>
                                <div className="space-y-2">
                                    {execution.outputs.results.map((result: any, idx: number) => (
                                        <div
                                            key={idx}
                                            className="p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded"
                                        >
                                            {result.resultsContentTruncated && (
                                                <div className="mb-2 text-sm text-yellow-600 dark:text-yellow-400">
                                                    (Content truncated)
                                                </div>
                                            )}
                                            <pre className="text-xs overflow-auto">{result.resultsContent || JSON.stringify(result, null, 2)}</pre>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {(!execution.outputs?.files?.length && !execution.outputs?.metadata?.length && !execution.outputs?.results?.length) && (
                            <p className="text-gray-500 dark:text-gray-400">No outputs</p>
                        )}
                    </div>
                )}

                {activeTab === "logs" && canViewLogs && (
                    <div>
                        <h2 className="text-xl font-semibold mb-4">Logs</h2>
                        {loadingLogs ? (
                            <div className="flex items-center gap-2">
                                <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600 dark:border-blue-400" />
                                <span>Loading logs...</span>
                            </div>
                        ) : logs ? (
                            <div className="border border-gray-300 dark:border-gray-600 rounded overflow-hidden">
                                <ConfigEditor value={logs} language="plaintext" readOnly height="500px" />
                            </div>
                        ) : (
                            <p className="text-gray-500 dark:text-gray-400">No logs available</p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

// Helper functions
function formatDate(dateString: string): string {
    try {
        return new Date(dateString).toLocaleString();
    } catch {
        return dateString;
    }
}

function calculateDuration(start: string, stop: string): string {
    try {
        const startMs = new Date(start).getTime();
        const stopMs = new Date(stop).getTime();
        const durationMs = stopMs - startMs;

        const seconds = Math.floor(durationMs / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);

        if (hours > 0) {
            return `${hours}h ${minutes % 60}m`;
        } else if (minutes > 0) {
            return `${minutes}m ${seconds % 60}s`;
        } else {
            return `${seconds}s`;
        }
    } catch {
        return "N/A";
    }
}

function formatBytes(bytes: number): string {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + " " + sizes[i];
}

export default ExecutionDetailPage;
