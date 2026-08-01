/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";
import { getExecutionLogs } from "../api/executions";
import ConfigEditor from "../components/ConfigEditor";

interface ExecutionLogViewerProps {
    executionId: string;
    /** The execution's pipeline steps (from details); each carries a pipelineExecutionId + name. */
    pipelines: any[];
}

// Log retrieval modes exposed by the backend logs endpoint.
//   full      = live CloudWatch search (reliable; the stored log is often empty because the
//               end-state lambda captures it before CloudWatch finishes ingesting the run).
//   truncated = the stored log, with a server-side live fallback when the stored copy is empty.
type LogSource = "full" | "truncated";

// "__execution__" scopes the search to the whole workflow execution; any other value is a
// pipelineExecutionId scoping the search to that single pipeline step.
const WHOLE_EXECUTION = "__execution__";

// logsSource values the endpoint reports for the text it returned.
const LOGS_SOURCE_LABELS: Record<string, string> = {
    stored: "Stored",
    live: "Live (CloudWatch)",
    sfnHistory: "Execution history (Step Functions)",
};

const ExecutionLogViewer: React.FC<ExecutionLogViewerProps> = ({ executionId, pipelines }) => {
    const [scope, setScope] = useState<string>(WHOLE_EXECUTION);
    const [source, setSource] = useState<LogSource>("full");
    const [loading, setLoading] = useState(false);
    const [logText, setLogText] = useState<string>("");
    const [emptyReason, setEmptyReason] = useState<string | null>(null);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    // Where the returned text actually came from ("stored" | "live" | "sfnHistory"); Stored mode
    // falls back to live CloudWatch and the Step Functions history server-side.
    const [logsSource, setLogsSource] = useState<string | null>(null);

    // Steps that carry a pipelineExecutionId can be scoped individually.
    const scopedPipelines = (pipelines || []).filter((p) => p && p.pipelineExecutionId);

    const fetchLogs = useCallback(async () => {
        setLoading(true);
        setErrorMsg(null);
        setEmptyReason(null);
        setLogsSource(null);
        try {
            const params: Record<string, string> = { mode: source };
            if (scope !== WHOLE_EXECUTION) params.pipelineExecutionId = scope;
            const [ok, data] = await getExecutionLogs(executionId, params);
            if (!ok || typeof data !== "object" || data === null) {
                setErrorMsg(typeof data === "string" ? data : "Failed to load logs");
                setLogText("");
                return;
            }
            const text = extractLogText(data);
            setLogText(text);
            setLogsSource(typeof data.logsSource === "string" ? data.logsSource : null);
            if (!text) {
                setEmptyReason(
                    source === "full"
                        ? "No log events found for this scope yet. Logs can take a short time to appear in CloudWatch after a run completes."
                        : "No stored logs for this scope. Switch Source to Live (CloudWatch) — it also reads the sub-process logs this step registered and the Step Functions history."
                );
            }
        } catch (err: any) {
            setErrorMsg(err?.message || "Unknown error");
            setLogText("");
        } finally {
            setLoading(false);
        }
    }, [executionId, scope, source]);

    // Fetch on mount and whenever the scope or source changes.
    useEffect(() => {
        fetchLogs();
    }, [fetchLogs]);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-text-primary">
                    Scope
                    <select
                        aria-label="Log scope"
                        value={scope}
                        onChange={(e) => setScope(e.target.value)}
                        className="px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
                    >
                        <option value={WHOLE_EXECUTION}>Whole execution</option>
                        {scopedPipelines.map((p, idx) => (
                            <option key={p.pipelineExecutionId} value={p.pipelineExecutionId}>
                                {`Step ${idx + 1}: ${
                                    p.name || p.pipelineId || p.pipelineExecutionId
                                }`}
                            </option>
                        ))}
                    </select>
                </label>

                <label className="flex items-center gap-2 text-sm text-text-primary">
                    Source
                    <select
                        aria-label="Log source"
                        value={source}
                        onChange={(e) => setSource(e.target.value as LogSource)}
                        className="px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
                    >
                        <option value="full">Live (CloudWatch)</option>
                        <option value="truncated">Stored</option>
                    </select>
                </label>

                <button
                    onClick={() => fetchLogs()}
                    disabled={loading}
                    className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                >
                    {loading ? "Loading…" : "Refresh"}
                </button>

                {logsSource && !loading && (
                    <span className="text-sm text-text-secondary">
                        Source: {LOGS_SOURCE_LABELS[logsSource] || logsSource}
                    </span>
                )}
            </div>

            {loading ? (
                <div className="flex items-center gap-2">
                    <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600 dark:border-blue-400" />
                    <span>Loading logs…</span>
                </div>
            ) : errorMsg ? (
                <p className="text-vams-error text-sm">{errorMsg}</p>
            ) : logText ? (
                <div className="border border-border-default rounded overflow-hidden">
                    <ConfigEditor value={logText} language="plaintext" readOnly height="500px" />
                </div>
            ) : (
                <p className="text-text-secondary text-sm">{emptyReason || "No logs available"}</p>
            )}
        </div>
    );
};

/**
 * Normalize the logs endpoint's several response shapes into displayable plain text:
 *  - full mode: { events: [{ timestamp, message }], sfnHistoryEvents?, subProcessEvents?, warnings? }
 *  - truncated whole-execution: { executionLog, executionError }
 *  - truncated per-pipeline: { resultLog, errorLog }
 */
function extractLogText(data: any): string {
    // Full (live) mode — render events chronologically with a readable timestamp prefix.
    if (Array.isArray(data.events)) {
        const lines: string[] = [];
        const render = (evts: any[]) =>
            evts.forEach((e) => {
                const ts = e?.timestamp ? new Date(e.timestamp).toISOString() : "";
                lines.push(ts ? `${ts}  ${e.message ?? ""}` : String(e?.message ?? ""));
            });
        render(data.events);
        // The Step Functions execution history is the authoritative state timeline for the whole
        // run; it is always available (no CloudWatch ingestion lag) and often the only content.
        if (Array.isArray(data.sfnHistoryEvents) && data.sfnHistoryEvents.length) {
            lines.push("", "──── execution history (Step Functions) ────");
            render(data.sfnHistoryEvents);
        }
        if (Array.isArray(data.subProcessEvents) && data.subProcessEvents.length) {
            lines.push("", "──── sub-process logs ────");
            render(data.subProcessEvents);
        }
        if (Array.isArray(data.warnings) && data.warnings.length) {
            lines.push("", "──── warnings ────", ...data.warnings);
        }
        return lines.join("\n").trim();
    }

    // Truncated (stored / live-fallback) mode — concatenate whichever fields are present.
    const parts: string[] = [];
    if (data.executionError) parts.push(`ERROR:\n${data.executionError}`);
    if (data.executionLog) parts.push(data.executionLog);
    if (data.errorLog) parts.push(`ERROR:\n${data.errorLog}`);
    if (data.resultLog) parts.push(data.resultLog);
    return parts.join("\n\n").trim();
}

export default ExecutionLogViewer;
