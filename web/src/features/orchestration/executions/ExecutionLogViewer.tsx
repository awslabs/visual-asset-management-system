/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getExecutionLogs } from "../api/executions";
import type { AvailableLog, LogSourceReport, LogSourceStatus } from "../types";
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
    // One of the selected step's registered logs (its logId), or "" for all of them together.
    const [logId, setLogId] = useState<string>("");
    const [loading, setLoading] = useState(false);
    const [logText, setLogText] = useState<string>("");
    const [emptyReason, setEmptyReason] = useState<string | null>(null);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    // Where the returned text actually came from ("stored" | "live" | "sfnHistory"); Stored mode
    // falls back to live CloudWatch and the Step Functions history server-side.
    const [logsSource, setLogsSource] = useState<string | null>(null);
    // Every source the last Live read consulted, with how the read of each one went.
    const [logSources, setLogSources] = useState<LogSourceReport[]>([]);
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
    // The logs the selected step can be read from (from details). Only a single step in Live mode
    // can be narrowed to one of them: the logs API takes logId in full mode with a pipeline scope only.
    const selectedPipeline = scopedPipelines.find((p) => p.pipelineExecutionId === scope);
    const availableLogs: AvailableLog[] = Array.isArray(selectedPipeline?.availableLogs)
        ? selectedPipeline.availableLogs
        : [];
    const canPickLogSource = scope !== WHOLE_EXECUTION && source === "full";

    // Token of the request whose result may still be rendered. Live CloudWatch reads take seconds
    // while a stored read returns at once, so responses arrive out of order: without this the earlier
    // one lands last and paints another scope's log under the current selection, with the Source badge
    // and the spinner taken from the superseded response too.
    const requestTokenRef = useRef(0);

    const fetchLogs = useCallback(async () => {
        const token = ++requestTokenRef.current;
        const isCurrent = () => token === requestTokenRef.current;
        setLoading(true);
        setErrorMsg(null);
        setEmptyReason(null);
        setLogsSource(null);
        setLogSources([]);
        try {
            const params: Record<string, string> = { mode: source };
            if (scope !== WHOLE_EXECUTION) params.pipelineExecutionId = scope;
            if (canPickLogSource && logId) params.logId = logId;
            const [ok, data] = await getExecutionLogs(executionId, params);
            if (!isCurrent()) return;
            if (!ok || typeof data !== "object" || data === null) {
                setErrorMsg(typeof data === "string" ? data : "Failed to load logs");
                setLogText("");
                return;
            }
            const text = extractLogText(data);
            setLogText(text);
            setLogsSource(typeof data.logsSource === "string" ? data.logsSource : null);
            setLogSources(Array.isArray(data.logSources) ? data.logSources : []);
            if (!text) {
                setEmptyReason(
                    source === "full"
                        ? "No log events found for this scope yet. Logs can take a short time to appear in CloudWatch after a run completes."
                        : "No stored logs for this scope. Switch Source to Live (CloudWatch) — it also reads the sub-process logs this step registered and the Step Functions history."
                );
            }
        } catch (err: any) {
            if (!isCurrent()) return;
            setErrorMsg(err?.message || "Unknown error");
            setLogText("");
        } finally {
            // A superseded request must not clear the spinner for the one still in flight.
            if (isCurrent()) setLoading(false);
        }
    }, [executionId, scope, source, logId, canPickLogSource]);

    // Fetch on mount and whenever the scope or source changes. Advancing the token on teardown is
    // what retires the request in flight, so a change of selection — or an unmount — discards it.
    useEffect(() => {
        fetchLogs();
        return () => {
            requestTokenRef.current += 1;
        };
    }, [fetchLogs]);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-text-primary">
                    Scope
                    <select
                        aria-label="Log scope"
                        value={scope}
                        onChange={(e) => {
                            setScope(e.target.value);
                            // A source belongs to one step; a new scope starts from all sources.
                            setLogId("");
                        }}
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
                        onChange={(e) => {
                            setSource(e.target.value as LogSource);
                            setLogId("");
                        }}
                        className="orch-outline px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
                    >
                        <option value="full">Live (CloudWatch)</option>
                        <option value="truncated">Stored</option>
                    </select>
                </label>

                {canPickLogSource && availableLogs.length > 0 && (
                    // A span, not a label: the mode select above is already named "Log source", and a
                    // wrapping label would associate this caption with the select as a second such name.
                    <span className="flex items-center gap-2 text-sm text-text-primary">
                        Log source
                        <select
                            aria-label="Available log source"
                            value={logId}
                            onChange={(e) => setLogId(e.target.value)}
                            className="orch-outline px-2 py-1 text-sm border border-border-input rounded bg-surface-input text-text-primary"
                        >
                            <option value="">All sources</option>
                            {availableLogs.map((log) => (
                                <option key={log.logId} value={log.logId}>
                                    {logSourceOptionLabel(log)}
                                </option>
                            ))}
                        </select>
                    </span>
                )}

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

            {!loading && logSources.length > 0 && (
                <div className="flex flex-wrap items-center gap-2" data-testid="log-sources">
                    <span className="text-sm text-text-secondary">Sources</span>
                    <ul className="flex flex-wrap gap-1.5" aria-label="Log sources read">
                        {logSources.map((s) => (
                            <li
                                key={s.logId}
                                className={`orch-outline px-2 py-0.5 text-xs rounded-full border ${logSourceStatusClass(
                                    s.status
                                )}`}
                                title={[s.sourceType, s.stageName, s.logGroupName]
                                    .filter(Boolean)
                                    .join(" · ")}
                            >
                                {`${s.label || s.logGroupName || s.logId} · ${logSourceStatusText(
                                    s
                                )}`}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

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

/** Chip text for how a source's read went; `read` carries the number of events it contributed. */
export function logSourceStatusText(source: LogSourceReport): string {
    switch (source.status) {
        case "read":
            return `read ${source.eventCount ?? 0}`;
        case "notFound":
            return "not found";
        default:
            return source.status;
    }
}

// Red is reserved for the two reads whose cause is known (a permission, a missing group); a read that
// failed for any other reason is amber so it is not mistaken for either.
const logSourceStatusClass = (status: LogSourceStatus): string =>
    status === "read"
        ? "border-green-300 bg-green-100 text-green-800 dark:border-green-800 dark:bg-green-900/30 dark:text-green-300"
        : status === "denied" || status === "notFound"
        ? "border-red-300 bg-red-100 text-red-800 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300"
        : status === "error"
        ? "border-amber-300 bg-amber-100 text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
        : "border-border-default text-text-secondary";

/** Option text for one available log: its label, then the kind of source and the stage it belongs to. */
export function logSourceOptionLabel(log: AvailableLog): string {
    return [log.label || log.logGroupName || log.logId, log.sourceType, log.stageName]
        .filter(Boolean)
        .join(" · ");
}

/**
 * Normalize the logs endpoint's several response shapes into displayable plain text:
 *  - full mode: { events: [{ timestamp, message }], sfnHistoryEvents?, subProcessEvents? (grouped by logId), logSources?, warnings? }
 *  - truncated whole-execution: { executionLog, executionError }
 *  - truncated per-pipeline: { resultLog, errorLog }
 */
export function extractLogText(data: any): string {
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
        // Sub-process logs come from several sources — the step's state machine, each container job —
        // so they are grouped under a heading per source. Events arrive in timestamp order; groups
        // follow the order in which each source first appears.
        if (Array.isArray(data.subProcessEvents) && data.subProcessEvents.length) {
            const labels = sourceLabelsById(data.logSources);
            groupByLogId(data.subProcessEvents).forEach((group) => {
                const label = group.logId ? labels[group.logId] || group.logId : "";
                lines.push(
                    "",
                    label ? `──── sub-process logs: ${label} ────` : "──── sub-process logs ────"
                );
                render(group.events);
            });
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

/**
 * Sub-process events grouped by `logId`, groups ordered by first appearance; events without one form
 * a single group.
 */
export function groupByLogId(events: any[]): Array<{ logId: string; events: any[] }> {
    const groups: Array<{ logId: string; events: any[] }> = [];
    const indexById = new Map<string, number>();
    events.forEach((e) => {
        const id = typeof e?.logId === "string" ? e.logId : "";
        let i = indexById.get(id);
        if (i === undefined) {
            i = groups.length;
            indexById.set(id, i);
            groups.push({ logId: id, events: [] });
        }
        groups[i].events.push(e);
    });
    return groups;
}

/** Display label per `logId` from the response's `logSources`, for the group headings. */
function sourceLabelsById(logSources: any): Record<string, string> {
    const labels: Record<string, string> = {};
    if (!Array.isArray(logSources)) return labels;
    logSources.forEach((s) => {
        if (s && typeof s.logId === "string")
            labels[s.logId] = s.label || s.logGroupName || s.logId;
    });
    return labels;
}

export default ExecutionLogViewer;
