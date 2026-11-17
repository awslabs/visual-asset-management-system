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
import { VAMS_PAGE_PADDING } from "../../components/compliance/rowActions";
import { isOnRoute } from "../../components/compliance/listingFilters";
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
    writeAuditFiltersToUrl,
} from "../../components/auditLogs/auditSearchUrl";
import { DEFAULT_LIMITS } from "../../components/auditLogs/auditSearchValidation";
import { DENIED_MESSAGE, useAuditSearch } from "../../components/auditLogs/useAuditSearch";
import { visibleText } from "../../components/auditLogs/auditText";
import { fetchAuditLogGroups } from "../../services/APIService";
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
        "Some filters cannot narrow the scan in advance. If a search reaches the record limit, narrow the time range.",
    recheckTimeBudget:
        "Checking the records took longer than the time allowed. The records shown are only part of the result.",
};

type GroupsState =
    | { kind: "loading" }
    | { kind: "denied" }
    | { kind: "failed"; message: string }
    | { kind: "ready"; data: AuditLogGroupsResult };

const AuditLogSearch: React.FC = () => {
    const [groupsState, setGroupsState] = useState<GroupsState>({ kind: "loading" });
    const [filters, setFilters] = useState<AuditFilters>(() =>
        parseAuditSearchUrl(window.location.hash || "")
    );
    const [selected, setSelected] = useState<AuditRow | null>(null);
    const [exportOpen, setExportOpen] = useState(false);
    const clearSorting = useRef<() => void>(() => undefined);
    const { canOpenAsset } = useAssetPageAccess();

    const groups = groupsState.kind === "ready" ? groupsState.data : null;
    const limits = groups?.limits ?? DEFAULT_LIMITS;
    const { state, run, stop, goToPage, clearError, isActive } = useAuditSearch(
        limits.longPollWaitSeconds ?? 20,
        filters.sort
    );

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
    // and never runs the search.
    useEffect(() => {
        const follow = () => {
            if (isOnRoute(AUDIT_SEARCH_ROUTE))
                setFilters(parseAuditSearchUrl(window.location.hash));
        };
        window.addEventListener("hashchange", follow);
        window.addEventListener("popstate", follow);
        return () => {
            window.removeEventListener("hashchange", follow);
            window.removeEventListener("popstate", follow);
        };
    }, []);

    const changeFilters = useCallback((next: AuditFilters) => {
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
    const emptyState: "notRun" | "noRecords" | "failed" =
        state.phase === "error" ? "failed" : hasRun && finished ? "noRecords" : "notRun";

    const notices: { key: string; type: "info" | "warning"; text: string }[] = [];
    if (statistics.candidateLimitReached) {
        notices.push({
            key: "candidate",
            type: "warning",
            text: "The search reached its candidate limit, so matching records may be missing. Narrow the time range to be sure.",
        });
    }
    for (const code of warnings) {
        notices.push({
            key: `warning-${code}`,
            type: code === "recheckTimeBudget" ? "warning" : "info",
            text: WARNING_TEXT[code] ?? visibleText(code),
        });
    }
    if (state.rows.some((row) => row.truncated)) {
        notices.push({
            key: "truncated",
            type: "info",
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
                    <Alert
                        type="error"
                        header="The audit log groups could not be loaded"
                        action={<Button onClick={() => void loadGroups()}>Retry</Button>}
                    >
                        {groupsState.message}
                    </Alert>
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
                        />

                        {isActive && (
                            <Alert type="info" header="Searching the audit logs">
                                <span aria-live="polite" data-testid="audit-progress">
                                    {statistics.recordsMatched !== undefined
                                        ? `${statistics.recordsMatched} matched`
                                        : "Waiting for the first results"}
                                    {statistics.recordsScanned !== undefined
                                        ? `, ${statistics.recordsScanned} scanned`
                                        : ""}
                                    . You can stop the search at any time.
                                </span>
                            </Alert>
                        )}

                        {state.error && (
                            <Alert
                                type={state.error.kind === "denied" ? "warning" : "error"}
                                header={errorHeader}
                                dismissible
                                onDismiss={clearError}
                            >
                                {state.error.message}
                            </Alert>
                        )}

                        {state.phase === "stopped" && (
                            <Alert type="info" header="Search stopped">
                                The records found before you stopped the search are shown.
                            </Alert>
                        )}

                        {notices.map((notice) => (
                            <Alert key={notice.key} type={notice.type}>
                                {notice.text}
                            </Alert>
                        ))}

                        <SpaceBetween size="s">
                            <Box float="right">
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

export default AuditLogSearch;
