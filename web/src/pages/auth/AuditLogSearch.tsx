/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { VAMS_PAGE_PADDING } from "../../components/compliance/rowActions";
import { isOnRoute } from "../../components/compliance/listingFilters";
import AuditInfoPopover from "../../components/auditLogs/AuditInfoPopover";
import AuditDetailPanel from "../../components/auditLogs/AuditDetailPanel";
import AuditExportDialog from "../../components/auditLogs/AuditExportDialog";
import AuditFilterBar from "../../components/auditLogs/AuditFilterBar";
import AuditResultsTable from "../../components/auditLogs/AuditResultsTable";
import { buildStartBody, normalizeLogGroups } from "../../components/auditLogs/auditSearchApiModel";
import {
    AuditFilters,
    AuditLogGroupsResult,
    AuditRow,
} from "../../components/auditLogs/auditSearchTypes";
import {
    defaultAuditFilters,
    parseAuditSearchUrl,
    serializeAuditFilters,
    writeAuditFiltersToUrl,
} from "../../components/auditLogs/auditSearchUrl";
import { DEFAULT_LIMITS } from "../../components/auditLogs/auditSearchValidation";
import { DENIED_MESSAGE, useAuditSearch } from "../../components/auditLogs/useAuditSearch";
import { visibleText } from "../../components/auditLogs/auditText";
import {
    AUDIT_SEARCH_NOT_ENABLED_MESSAGE,
    isAuditLogSearchEnabled,
} from "../../components/auditLogs/auditSearchEnabled";
import { fetchAuditLogGroups } from "../../services/APIService";
import { useToast } from "../../features/orchestration/components/ToastProvider";
import { useAssetPageAccess } from "../../hooks/useAssetPageAccess";

/** The page's route (routes.tsx); the filters are read from and written to this route's URL only. */
export const AUDIT_SEARCH_ROUTE = "/auth/audit-log-search";
/** The web route the API decides on; also what the compliance Audit Log page links to. */
export const AUDIT_SEARCH_HREF = "#/auth/audit-log-search/";
export const COMPLIANCE_AUDIT_HREF = "#/compliance/audit/";

/** What the server's `warnings` codes mean to a reader. Unknown codes are shown as they are. */
export const WARNING_TEXT: Record<string, string> = {
    widerScan:
        "A value you gave could not be used to narrow the scan, so more of the log was read. The result is still exact.",
    candidateLimitMayApply:
        "Some filters cannot narrow the scan in advance. If a search reaches the record limit, narrow the time range. A result without a limit notice is not proof that it is complete.",
    recheckTimeBudget:
        "Checking the records took longer than the time allowed. The records shown are only part of the result.",
};

/** The key of the Authentication log group (a fixed registry key; the page decides nothing from it). */
export const AUTHENTICATION_GROUP_KEY = "auditAuthentication";
/** Shown while the Authentication group is among the searched groups: VAMS writes nothing to it yet. */
export const AUTHENTICATION_NOTE =
    "The Authentication group is empty: VAMS does not record sign-ins itself. Amazon Cognito or your identity provider records them. API requests, allowed and denied, are in the Authorization group.";

type GroupsState =
    | { kind: "loading" }
    | { kind: "denied" }
    | { kind: "failed"; message: string }
    | { kind: "ready"; data: AuditLogGroupsResult };

/**
 * Backstop for the incomplete-result warning toasts: they stay until dismissed, the page dismisses
 * them when the results they describe are replaced or the page is left, and this finite duration
 * (10 minutes) ends one that nothing else did.
 */
export const WARNING_TOAST_BACKSTOP_MS = 600_000;

const AuditLogSearchBody: React.FC = () => {
    const [groupsState, setGroupsState] = useState<GroupsState>({ kind: "loading" });
    const [filters, setFilters] = useState<AuditFilters>(() =>
        parseAuditSearchUrl(window.location.hash || "")
    );
    const [selected, setSelected] = useState<AuditRow | null>(null);
    const [exportOpen, setExportOpen] = useState(false);
    const clearSorting = useRef<() => void>(() => undefined);
    const { canOpenAsset } = useAssetPageAccess();

    const resetRef = useRef<() => void>(() => undefined);

    const groups = groupsState.kind === "ready" ? groupsState.data : null;
    const limits = groups?.limits ?? DEFAULT_LIMITS;
    const { state, run, stop, goToPage, clearError, reset, isActive } = useAuditSearch(
        limits.longPollWaitSeconds ?? 20,
        filters.sort
    );

    useEffect(() => {
        resetRef.current = reset;
    }, [reset]);

    // The log groups, actions and limits the caller may use: the server's answer, nothing else.
    const loadGroups = useCallback(async () => {
        setGroupsState({ kind: "loading" });
        const answer: any[] = await fetchAuditLogGroups();
        if (answer[0] === true) {
            setGroupsState({ kind: "ready", data: normalizeLogGroups(answer[1]) });
        } else if (answer[2] === 403) {
            setGroupsState({ kind: "denied" });
        } else {
            setGroupsState({
                kind: "failed",
                message: answer[1] || "The audit log groups could not be loaded.",
            });
        }
    }, []);
    useEffect(() => {
        void loadGroups();
    }, [loadGroups]);

    // The URL follows Back and Forward between two entries of this page; a link restores filters
    // and never runs the search. The results shown belong to the filters of the search that made
    // them: when the address brings other filters (a link opened while results are showing, Back
    // to an earlier entry), the results and any running search are dropped.
    const shownFilters = useRef(serializeAuditFilters(filters));
    useEffect(() => {
        const follow = () => {
            if (!isOnRoute(AUDIT_SEARCH_ROUTE)) return;
            const fromUrl = parseAuditSearchUrl(window.location.hash);
            const text = serializeAuditFilters(fromUrl);
            if (text !== shownFilters.current) {
                shownFilters.current = text;
                setSelected(null);
                resetRef.current();
            }
            setFilters(fromUrl);
        };
        window.addEventListener("hashchange", follow);
        window.addEventListener("popstate", follow);
        return () => {
            window.removeEventListener("hashchange", follow);
            window.removeEventListener("popstate", follow);
        };
    }, []);

    const changeFilters = useCallback((next: AuditFilters) => {
        shownFilters.current = serializeAuditFilters(next);
        setFilters(next);
        if (isOnRoute(AUDIT_SEARCH_ROUTE)) writeAuditFiltersToUrl(next);
    }, []);

    const search = useCallback(
        (using: AuditFilters) => {
            setSelected(null);
            void run(buildStartBody(using));
        },
        [run]
    );

    const showRequest = useCallback(
        (requestId: string) => {
            // The pivot is a new entry in the history, so Back returns to the search it came from.
            const next: AuditFilters = {
                ...defaultAuditFilters(),
                requestIds: [requestId],
                includeProbes: true,
                range: filters.range,
                sort: "asc",
            };
            shownFilters.current = serializeAuditFilters(next);
            setFilters(next);
            if (isOnRoute(AUDIT_SEARCH_ROUTE)) writeAuditFiltersToUrl(next, true);
            search(next);
        },
        [filters.range, search]
    );

    const changeOrder = useCallback(
        (sort: "asc" | "desc") => {
            if (!state.queryId || isActive || state.phase === "error") return;
            clearSorting.current();
            setSelected(null);
            void goToPage(1, sort);
        },
        [goToPage, isActive, state.phase, state.queryId]
    );

    const { statistics, warnings } = state;
    const finished = state.phase === "done" || state.phase === "stopped";
    const hasRun = state.phase !== "idle";
    const emptyState: "notRun" | "noRecords" | "failed" | "stopped" =
        state.phase === "error"
            ? "failed"
            : state.phase === "stopped"
            ? "stopped"
            : hasRun && finished
            ? "noRecords"
            : "notRun";

    // Warnings are toasts; information is an info icon beside the results.
    const warningToasts: { key: string; title: string; text: string }[] = [];
    const infoNotes: { key: string; text: string }[] = [];
    if (statistics.candidateLimitReached) {
        warningToasts.push({
            key: "candidate",
            title: "Results may be incomplete",
            text: "The search reached its record limit, so more matching records may exist. Narrow the time range to be sure.",
        });
    }
    for (const code of warnings) {
        const text = WARNING_TEXT[code] ?? visibleText(code);
        if (code === "recheckTimeBudget") {
            warningToasts.push({ key: `warning-${code}`, title: "Results may be partial", text });
        } else {
            infoNotes.push({ key: `warning-${code}`, text });
        }
    }
    if (state.rows.some((row) => row.truncated)) {
        infoNotes.push({
            key: "truncated",
            text: "Some records hold values that were cut short when they were written. Open a record to see them.",
        });
    }

    const errorHeader =
        state.error?.kind === "throttled"
            ? "The service is busy"
            : state.error?.kind === "expired"
            ? "Results expired"
            : state.error?.kind === "denied"
            ? "No access"
            : state.error?.kind === "timeout"
            ? "The search stopped"
            : "The search could not be completed";

    // Toasts: each distinct message once per search, so long polling never stacks them. A new
    // search starts a new set. Warnings that say the result is not complete stay until dismissed;
    // errors follow the app's default (8 seconds).
    const toast = useToast();
    const shown = useRef<Set<string>>(new Set());
    // The ids of the warning toasts this page raised, so it can dismiss them (and only them).
    const warningIds = useRef<string[]>([]);
    const dismissToast = useRef(toast.dismiss);
    dismissToast.current = toast.dismiss;
    const dismissWarnings = useCallback(() => {
        for (const id of warningIds.current) dismissToast.current(id);
        warningIds.current = [];
    }, []);
    useEffect(() => {
        // A new search, or results cleared (filters from a link, Back), ends the warnings of the
        // results that were shown.
        if (state.phase === "starting" || state.phase === "idle") {
            shown.current.clear();
            dismissWarnings();
        }
    }, [state.phase, dismissWarnings]);
    // Leaving the page takes its warnings with it: the toast stack belongs to the whole app.
    useEffect(() => dismissWarnings, [dismissWarnings]);
    const once = (key: string, raise: () => void) => {
        if (shown.current.has(key)) return;
        shown.current.add(key);
        raise();
    };
    useEffect(() => {
        if (!state.error) return;
        const { kind, message } = state.error;
        once(`error:${kind}:${message}`, () => {
            if (kind === "denied") toast.warning(errorHeader, { description: message });
            else toast.error(errorHeader, { description: message });
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state.error]);
    const warningKeys = warningToasts.map((w) => `${state.queryId}:${w.key}`).join("|");
    useEffect(() => {
        for (const w of warningToasts) {
            once(`${state.queryId}:${w.key}`, () => {
                const id = toast.warning(w.title, {
                    description: w.text,
                    duration: WARNING_TOAST_BACKSTOP_MS,
                });
                if (id) warningIds.current.push(id);
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [warningKeys]);
    const groupsFailure = groupsState.kind === "failed" ? groupsState.message : null;
    useEffect(() => {
        if (groupsFailure !== null) {
            toast.error("The audit log groups could not be loaded", { description: groupsFailure });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [groupsFailure]);

    // The live region exists before its text does, so the first message is announced.
    const status = (
        <span aria-live="polite" data-testid="audit-status-live">
            {isActive ? (
                <span data-testid="audit-progress">
                    <StatusIndicator type="in-progress">
                        {statistics.recordsScanned !== undefined
                            ? `${statistics.recordsScanned} records scanned so far`
                            : "Waiting for the first results"}
                    </StatusIndicator>
                </span>
            ) : state.phase === "stopped" ? (
                <span data-testid="audit-stopped">
                    <StatusIndicator type="stopped">Search stopped</StatusIndicator>
                    <AuditInfoPopover ariaLabel="About the stopped search" header="Search stopped">
                        A stopped search shows no records. Narrow the time range or the filters and
                        search again.
                    </AuditInfoPopover>
                </span>
            ) : null}
        </span>
    );

    return (
        <Box padding={VAMS_PAGE_PADDING}>
            <SpaceBetween size="l">
                <Header
                    variant="h1"
                    description={
                        <>
                            Search the audit logs of this VAMS deployment. For the compliance
                            history of schemas, quarantine and cascades, open the{" "}
                            <Link href={COMPLIANCE_AUDIT_HREF}>Compliance Audit Log</Link>.
                        </>
                    }
                >
                    Audit Log Search
                </Header>

                {groupsState.kind === "loading" && (
                    <Box textAlign="center" padding="l">
                        <Spinner size="large" /> <span>Loading the log groups you can search</span>
                    </Box>
                )}

                {groupsState.kind === "denied" && (
                    <Alert type="warning" header="No access">
                        {DENIED_MESSAGE}
                    </Alert>
                )}

                {groupsState.kind === "failed" && (
                    <SpaceBetween size="xs" direction="horizontal" alignItems="center">
                        <StatusIndicator type="error">
                            The audit log groups could not be loaded
                        </StatusIndicator>
                        <Button onClick={() => void loadGroups()}>Retry</Button>
                    </SpaceBetween>
                )}

                {groups && groups.groups.length === 0 && (
                    <Alert type="info" header="No audit logs available to you">
                        Your roles do not include any audit log group. Contact your administrator if
                        you need to search audit logs.
                    </Alert>
                )}

                {groups && groups.groups.length > 0 && (
                    <>
                        <AuditFilterBar
                            filters={filters}
                            onChange={changeFilters}
                            groups={groups}
                            onSearch={() => search(filters)}
                            onStop={() => void stop()}
                            busy={isActive}
                            disabled={state.loadingPage}
                            onOrderChange={changeOrder}
                            status={status}
                            filterInfo={
                                filters.logGroups.includes(AUTHENTICATION_GROUP_KEY) ? (
                                    <AuditInfoPopover
                                        testId="audit-authentication-note"
                                        label="Authentication group"
                                        ariaLabel="About the Authentication log group"
                                        header="Authentication group"
                                    >
                                        {AUTHENTICATION_NOTE}
                                    </AuditInfoPopover>
                                ) : undefined
                            }
                        />

                        <SpaceBetween size="s">
                            <Box float="right">
                                {infoNotes.length > 0 && (
                                    <AuditInfoPopover
                                        testId="audit-results-info"
                                        ariaLabel="About these results"
                                        header="About these results"
                                    >
                                        <SpaceBetween size="xs">
                                            {infoNotes.map((note) => (
                                                <span key={note.key}>{note.text}</span>
                                            ))}
                                        </SpaceBetween>
                                    </AuditInfoPopover>
                                )}
                                <Button
                                    iconName="download"
                                    onClick={() => setExportOpen(true)}
                                    disabled={!finished || state.rows.length === 0}
                                >
                                    Export
                                </Button>
                            </Box>
                            <AuditResultsTable
                                rows={state.rows}
                                loading={state.phase === "starting" || state.loadingPage}
                                selected={selected}
                                onSelect={setSelected}
                                pageIndex={state.pageIndex}
                                knownPages={state.knownPages}
                                hasMore={!!state.nextToken}
                                onPageChange={(index) => {
                                    setSelected(null);
                                    void goToPage(index);
                                }}
                                emptyState={emptyState}
                                registerClearSorting={(clear) => {
                                    clearSorting.current = clear;
                                }}
                            />
                        </SpaceBetween>

                        {selected && (
                            <AuditDetailPanel
                                row={selected}
                                onClose={() => setSelected(null)}
                                onShowRequest={showRequest}
                                canOpenAsset={canOpenAsset}
                            />
                        )}
                    </>
                )}

                <AuditExportDialog
                    visible={exportOpen}
                    onDismiss={() => setExportOpen(false)}
                    queryId={state.queryId}
                    rowCount={statistics.rowCount}
                    finished={finished}
                />
            </SpaceBetween>
        </Box>
    );
};

/**
 * The page. With Audit Log Search switched off for the deployment (no AUDITLOGSEARCH switch) it shows
 * a short state and makes no API call; a direct URL lands here. The switch grants nothing: with it
 * on, the API still decides who may use the page.
 */
const AuditLogSearch: React.FC = () => {
    if (!isAuditLogSearchEnabled()) {
        return (
            <Box padding={VAMS_PAGE_PADDING}>
                <SpaceBetween size="m">
                    <Header variant="h1">Audit Log Search</Header>
                    <Alert
                        type="info"
                        statusIconAriaLabel="Info"
                        header={AUDIT_SEARCH_NOT_ENABLED_MESSAGE}
                        data-testid="audit-search-not-enabled"
                    >
                        An administrator of this deployment turned Audit Log Search off. Recording
                        of audit events is not affected.
                    </Alert>
                </SpaceBetween>
            </Box>
        );
    }
    return <AuditLogSearchBody />;
};

export default AuditLogSearch;
