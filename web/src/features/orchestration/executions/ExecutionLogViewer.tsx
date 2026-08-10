/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getExecutionLogs } from "../api/executions";
import ConfigEditor from "../components/ConfigEditor";
import { findMatches, filterToMatches, stepIndex } from "./logSearch";

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
    // Find-in-log. Entirely local over the already-fetched text, so stepping through matches costs
    // no further CloudWatch reads.
    const [query, setQuery] = useState("");
    const [caseSensitive, setCaseSensitive] = useState(false);
    const [matchIndex, setMatchIndex] = useState(0);
    // "Only matching lines" reduces a multi-thousand-line log to its hits, which is usually faster
    // than stepping when the question is "did X happen at all, and how often".
    const [onlyMatches, setOnlyMatches] = useState(false);

    const matches = useMemo(
        () => findMatches(logText, query, caseSensitive),
        [logText, query, caseSensitive]
    );
    // A new search invalidates the cursor; without this a narrowing query keeps a stale index and
    // the "n of m" counter reads past the end.
    useEffect(() => setMatchIndex(0), [query, caseSensitive, logText]);

    const current = matches[matchIndex];
    // The editor stays mounted; it is told which line/column to reveal and select.
    const displayText =
        onlyMatches && query ? filterToMatches(logText, query, caseSensitive) : logText;

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
                        className="orch-outline px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
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
                        className="orch-outline px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
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

            {/* Find in log — local over the fetched text, so no extra CloudWatch reads. */}
            {logText && (
                <div className="flex flex-wrap items-center gap-2">
                    <input
                        type="search"
                        aria-label="Find in log"
                        placeholder="Find in log…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={(e) => {
                            // Enter / Shift+Enter steps matches, matching find-in-page muscle memory.
                            if (e.key === "Enter") {
                                e.preventDefault();
                                setMatchIndex((i) =>
                                    stepIndex(i, matches.length, e.shiftKey ? -1 : 1)
                                );
                            }
                        }}
                        className="orch-outline px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
                    />
                    <span className="text-sm text-text-secondary" data-testid="log-match-count">
                        {query
                            ? matches.length
                                ? `${matchIndex + 1} of ${matches.length}`
                                : "No matches"
                            : ""}
                    </span>
                    <button
                        type="button"
                        aria-label="Previous match"
                        disabled={!matches.length}
                        onClick={() => setMatchIndex((i) => stepIndex(i, matches.length, -1))}
                        className="orch-outline px-2 py-1 text-sm border border-border-default rounded text-text-primary hover:bg-surface-hover disabled:opacity-50"
                    >
                        ↑
                    </button>
                    <button
                        type="button"
                        aria-label="Next match"
                        disabled={!matches.length}
                        onClick={() => setMatchIndex((i) => stepIndex(i, matches.length, 1))}
                        className="orch-outline px-2 py-1 text-sm border border-border-default rounded text-text-primary hover:bg-surface-hover disabled:opacity-50"
                    >
                        ↓
                    </button>
                    <label className="flex items-center gap-1.5 text-sm text-text-primary">
                        <input
                            type="checkbox"
                            checked={caseSensitive}
                            onChange={(e) => setCaseSensitive(e.target.checked)}
                        />
                        Match case
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-text-primary">
                        <input
                            type="checkbox"
                            checked={onlyMatches}
                            onChange={(e) => setOnlyMatches(e.target.checked)}
                        />
                        Only matching lines
                    </label>
                    {current && !onlyMatches && (
                        <span className="text-sm text-text-secondary">line {current.line}</span>
                    )}
                </div>
            )}

            {loading ? (
                <div className="flex items-center gap-2">
                    <div className="orch-outline inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600 dark:border-blue-400" />
                    <span>Loading logs…</span>
                </div>
            ) : errorMsg ? (
                <p className="text-vams-error text-sm">{errorMsg}</p>
            ) : logText ? (
                <div className="orch-outline border border-border-default rounded overflow-hidden">
                    <ConfigEditor
                        // NOT keyed on the match: the editor stays mounted and is told where to go,
                        // so stepping scrolls and re-selects in place. Remounting per step lost the
                        // selection, which made stepping look like it did nothing.
                        value={displayText}
                        language="plaintext"
                        readOnly
                        height="500px"
                        startLine={onlyMatches ? undefined : current?.line}
                        startColumn={onlyMatches ? undefined : current?.column}
                        // Selecting the matched text is what makes the hit visible on the line.
                        selectionLength={onlyMatches ? undefined : query.length || undefined}
                    />
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
