/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback, useRef } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { StatusLabel } from "../components/compliance/complianceStateBadge";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Synonyms from "../synonyms";
import {
    fetchCascades,
    fetchCascade,
    approveCascade,
    rejectCascade,
    itemAllows,
    CascadeRecord,
    COMPLIANCE_LISTING_PAGE_SIZE,
    PagedCascades,
} from "../services/ComplianceService";
import {
    CascadeNodesModal,
    cascadeNodeEntries,
    cascadeProgress,
} from "../components/compliance/cascadeNodes";
import {
    DatabaseFilter,
    TextFilterInput,
    useLatestRequest,
    useUrlFilters,
} from "../components/compliance/listingFilters";
import ReasonModal from "../components/compliance/ReasonModal";
import { ROW_ACTIONS_STICKY_COLUMNS, VAMS_PAGE_PADDING } from "../components/compliance/rowActions";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";

/** The listing's server-side filters, kept in the URL; an empty state is pending approval. */
export const CASCADE_FILTER_KEYS = ["state", "databaseId", "text"] as const;

export const CASCADE_STATE_FILTER_OPTIONS = [
    { value: "pending_approval", label: "Pending approval" },
    { value: "executing", label: "Executing" },
    { value: "completed", label: "Completed" },
    { value: "aborted", label: "Aborted" },
];

const statusIndicatorMap: Record<string, { type: string; label: string }> = {
    pending_approval: { type: "pending", label: "Pending Approval" },
    executing: { type: "in-progress", label: "Executing" },
    completed: { type: "success", label: "Completed" },
    aborted: { type: "error", label: "Aborted" },
    rejected: { type: "stopped", label: "Rejected" },
};

/** How often, and how many times, an approved cascade is re-read while it is executing. */
export const CASCADE_POLL_INTERVAL_MS = 3000;
export const CASCADE_POLL_ATTEMPTS = 20;

/** An approved cascade whose execution the page follows until it leaves `executing`. */
interface TrackedCascade {
    cascadeId: string;
    record: CascadeRecord | null;
    polling: boolean;
    error?: string;
}

const shortId = (cascadeId: string) => `${cascadeId.substring(0, 8)}...`;

/** The trigger asset's database: the listing's `databaseId`, else the row's `triggeredBy*` id. */
export const cascadeTriggerDatabaseId = (record: CascadeRecord): string =>
    record.databaseId || record.triggeredByDatabaseId || "";

export const cascadeTriggerAssetId = (record: CascadeRecord): string =>
    record.assetId || record.triggeredByAssetId || "";

/**
 * The cascade's progress: the nodes evaluated of the total, the skipped ones with their reasons,
 * the failed ones and those still to go (components/compliance/cascadeNodes.tsx).
 */
export { cascadeProgress };

/** Whether the row carries nodes to list in the "View nodes" dialog. */
const hasNodes = (record: CascadeRecord | null): boolean =>
    (cascadeNodeEntries(record) || []).length > 0;

/**
 * Width (px) of the Actions column: the Approve and Reject inline links side by side (about 120 px
 * of labels and gap) with the cell padding. See ROW_ACTIONS_STICKY_COLUMNS.
 */
export const CASCADE_ACTIONS_COLUMN_WIDTH = 180;

// The routes behind the row actions (backend/backend/common/apiRoutes.py); each is offered only to a
// caller who may call it, none while the routes load.
export const CASCADE_APPROVE_API_ROUTE = "/compliance/cascades/{cascadeId}/approve";
export const CASCADE_REJECT_API_ROUTE = "/compliance/cascades/{cascadeId}/reject";

const ComplianceCascades: React.FC = () => {
    const { can: canCallRoute } = useAllowedRoutes();
    const canApprove = canCallRoute("POST", CASCADE_APPROVE_API_ROUTE);
    const canReject = canCallRoute("POST", CASCADE_REJECT_API_ROUTE);
    const [items, setItems] = useState<CascadeRecord[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    const [rejectTarget, setRejectTarget] = useState<CascadeRecord | null>(null);
    const [rejecting, setRejecting] = useState(false);
    const [approveTarget, setApproveTarget] = useState<CascadeRecord | null>(null);
    const [approving, setApproving] = useState(false);
    const [tracked, setTracked] = useState<Record<string, TrackedCascade>>({});
    // The cascade whose nodes the "View nodes" dialog shows; read from the followed cascade while
    // it is tracked (so the dialog follows its polling), else from the listing row.
    const [nodesCascadeId, setNodesCascadeId] = useState<string | null>(null);
    const [filters, setFilters] = useUrlFilters(CASCADE_FILTER_KEYS);
    const stateFilter = filters.state || "pending_approval";
    // Server-side token paging: tokens[i] fetches page i + 1 (tokens[0] is undefined).
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    const mountedRef = useRef(true);
    const pollTimersRef = useRef<Record<string, number>>({});

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            Object.values(pollTimersRef.current).forEach((timer) => window.clearTimeout(timer));
            pollTimersRef.current = {};
        };
    }, []);

    // A load is dropped when a later one has begun (other filters, another page), so the rows
    // shown are always the latest request's.
    const beginLoad = useLatestRequest();
    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            const isCurrent = beginLoad();
            setLoading(true);
            setError(null);
            const [success, result] = await fetchCascades({
                maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
                startingToken,
                state: stateFilter,
                databaseId: filters.databaseId,
                text: filters.text,
            });
            if (!isCurrent()) return;
            if (success && typeof result !== "string") {
                // An array is the shape of the listing before it was paged.
                const page: PagedCascades = Array.isArray(result)
                    ? { cascades: result as CascadeRecord[] }
                    : result;
                setItems(page.cascades);
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (page.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: page.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setError(typeof result === "string" ? result : "Failed to load cascades");
                setHasMore(false);
            }
            setLoading(false);
        },
        [beginLoad, stateFilter, filters.databaseId, filters.text]
    );

    // A reload or a filter change restarts the walk: a token is issued for its filters.
    const loadData = useCallback(() => {
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        setHasMore(false);
        return loadPage(0, undefined);
    }, [loadPage]);

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        setCurrentPageIndex(detail.currentPageIndex);
        loadPage(detail.currentPageIndex - 1, tokens[detail.currentPageIndex - 1]);
    };

    useEffect(() => {
        loadData();
    }, [loadData]);

    // Re-reads the cascade until it is no longer executing or the attempts run out.
    const trackCascade = useCallback((cascadeId: string, attempt = 0) => {
        delete pollTimersRef.current[cascadeId];
        setTracked((prev) => ({
            ...prev,
            [cascadeId]: { cascadeId, record: prev[cascadeId]?.record ?? null, polling: true },
        }));
        fetchCascade(cascadeId).then(([success, result]) => {
            if (!mountedRef.current) return;
            if (!success || typeof result === "string") {
                setTracked((prev) => ({
                    ...prev,
                    [cascadeId]: {
                        cascadeId,
                        record: prev[cascadeId]?.record ?? null,
                        polling: false,
                        error: typeof result === "string" ? result : "Failed to fetch cascade",
                    },
                }));
                return;
            }
            const keepPolling = result.state === "executing" && attempt + 1 < CASCADE_POLL_ATTEMPTS;
            setTracked((prev) => ({
                ...prev,
                [cascadeId]: { cascadeId, record: result, polling: keepPolling },
            }));
            if (keepPolling) {
                pollTimersRef.current[cascadeId] = window.setTimeout(
                    () => trackCascade(cascadeId, attempt + 1),
                    CASCADE_POLL_INTERVAL_MS
                );
            }
        });
    }, []);

    const untrackCascade = (cascadeId: string) => {
        const timer = pollTimersRef.current[cascadeId];
        if (timer !== undefined) {
            window.clearTimeout(timer);
            delete pollTimersRef.current[cascadeId];
        }
        setTracked((prev) => {
            const next = { ...prev };
            delete next[cascadeId];
            return next;
        });
    };

    // Approve and Reject each take an optional reason; an empty one sends none (the API records
    // "approved" / "rejected").
    const handleApprove = async (reason: string) => {
        if (!approveTarget) return;
        const cascadeId = approveTarget.cascadeId;
        setApproving(true);
        setActionMessage(null);
        setError(null);
        const [success, result] = await approveCascade(cascadeId, reason || undefined);
        setApproving(false);
        setApproveTarget(null);
        if (success && typeof result !== "string") {
            setActionMessage(
                `${result.message}: execution started for cascade ${shortId(cascadeId)}.`
            );
            trackCascade(cascadeId);
            await loadData();
        } else {
            setError(typeof result === "string" ? result : "Failed to approve cascade");
        }
    };

    const handleReject = async (reason: string) => {
        if (!rejectTarget) return;
        const cascadeId = rejectTarget.cascadeId;
        setRejecting(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await rejectCascade(cascadeId, reason || undefined);
        setRejecting(false);
        if (success) {
            setRejectTarget(null);
            setActionMessage(message);
            await loadData();
        } else {
            setRejectTarget(null);
            setError(message);
        }
    };

    const renderTracked = (entry: TrackedCascade) => {
        const state = entry.record?.state;
        const progress = cascadeProgress(entry.record);
        let type: "info" | "success" | "error" | "warning" = "info";
        let header = `Cascade ${shortId(entry.cascadeId)} is executing`;
        let body: React.ReactNode = entry.polling
            ? "Status refreshes automatically while the cascade runs."
            : "The cascade is still running. Check again to refresh its status.";
        if (entry.error) {
            type = "warning";
            header = `Cascade ${shortId(entry.cascadeId)} status unavailable`;
            body = entry.error;
        } else if (state === "completed") {
            type = "success";
            header = `Cascade ${shortId(entry.cascadeId)} completed`;
            body = progress || "The cascade finished.";
        } else if (state === "aborted") {
            type = "error";
            header = `Cascade ${shortId(entry.cascadeId)} aborted`;
            body = entry.record?.abortReason || "The cascade did not finish.";
        }
        const showCheckAgain = !entry.polling && (state === "executing" || !!entry.error);
        return (
            <Alert
                key={entry.cascadeId}
                type={type}
                header={header}
                dismissible
                onDismiss={() => untrackCascade(entry.cascadeId)}
                dismissAriaLabel="Dismiss cascade status"
                action={
                    showCheckAgain ? (
                        <Button onClick={() => trackCascade(entry.cascadeId)}>Check again</Button>
                    ) : undefined
                }
            >
                <SpaceBetween size="xs">
                    {state && state !== "completed" && state !== "aborted" && progress && (
                        <div>{progress}</div>
                    )}
                    <div>{body}</div>
                    {!entry.error && hasNodes(entry.record) && (
                        <Button
                            variant="inline-link"
                            onClick={() => setNodesCascadeId(entry.cascadeId)}
                        >
                            View nodes
                        </Button>
                    )}
                </SpaceBetween>
            </Alert>
        );
    };

    const nodesRecord = nodesCascadeId
        ? tracked[nodesCascadeId]?.record ||
          items.find((item) => item.cascadeId === nodesCascadeId) ||
          null
        : null;

    return (
        // The page margins every VAMS list page uses (VAMS_PAGE_PADDING).
        <Box padding={VAMS_PAGE_PADDING}>
            <SpaceBetween size="l">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}
                {actionMessage && (
                    <Alert type="success" dismissible onDismiss={() => setActionMessage(null)}>
                        {actionMessage}
                    </Alert>
                )}
                {Object.values(tracked).map(renderTracked)}

                <Table
                    wrapLines
                    stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
                    header={
                        <Header
                            variant="h1"
                            counter={`(${items.length}${hasMore ? "+" : ""})`}
                            actions={
                                <Button
                                    iconName="refresh"
                                    ariaLabel="Refresh cascades"
                                    onClick={loadData}
                                    loading={loading}
                                />
                            }
                        >
                            Cascade Approval Queue
                        </Header>
                    }
                    filter={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Select
                                selectedOption={
                                    CASCADE_STATE_FILTER_OPTIONS.find(
                                        (option) => option.value === stateFilter
                                    ) || CASCADE_STATE_FILTER_OPTIONS[0]
                                }
                                options={CASCADE_STATE_FILTER_OPTIONS}
                                ariaLabel="Filter by cascade state"
                                onChange={({ detail }) =>
                                    setFilters({
                                        state:
                                            detail.selectedOption.value === "pending_approval"
                                                ? ""
                                                : detail.selectedOption.value || "",
                                    })
                                }
                            />
                            <DatabaseFilter
                                value={filters.databaseId}
                                onChange={(databaseId) => setFilters({ databaseId })}
                            />
                            <TextFilterInput
                                value={filters.text}
                                onCommit={(text) => setFilters({ text })}
                                placeholder={`Search trigger ${Synonyms.asset} name or ID`}
                                ariaLabel="Search cascades"
                            />
                        </SpaceBetween>
                    }
                    pagination={
                        <Pagination
                            currentPageIndex={currentPageIndex}
                            pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                            openEnd={hasMore}
                            onChange={handlePageChange}
                            disabled={loading}
                            ariaLabels={{
                                nextPageLabel: "Next page of cascades",
                                previousPageLabel: "Previous page of cascades",
                                pageLabel: (pageNumber) => `Page ${pageNumber} of cascades`,
                            }}
                        />
                    }
                    loading={loading}
                    items={items}
                    empty={
                        <Box textAlign="center" padding="l">
                            <b>No cascades</b>
                            <Box variant="p" color="inherit">
                                {hasMore
                                    ? "None of the cascades read for this page are visible to you. Open the next page to continue."
                                    : currentPageIndex > 1
                                    ? "No more cascades. The earlier pages list all of them."
                                    : stateFilter === "pending_approval" &&
                                      !filters.databaseId &&
                                      !filters.text
                                    ? "No cascade operations are pending approval."
                                    : "No cascades match the filters."}
                            </Box>
                        </Box>
                    }
                    columnDefinitions={[
                        {
                            // The cascade's status with its short ID beneath, in one column so
                            // the table fits the page at 1280 px; a cascade that has run also
                            // shows its progress and a link to each node's outcome.
                            id: "state",
                            header: "Cascade",
                            cell: (item) => {
                                const indicator = statusIndicatorMap[item.state] || {
                                    type: "stopped",
                                    label: item.state || "Unknown",
                                };
                                const progress =
                                    item.state === "pending_approval"
                                        ? null
                                        : cascadeProgress(item);
                                return (
                                    <SpaceBetween size="xxxs">
                                        <StatusLabel type={indicator.type as any}>
                                            {indicator.label}
                                        </StatusLabel>
                                        <Box variant="small" color="text-body-secondary">
                                            {shortId(item.cascadeId)}
                                        </Box>
                                        {progress && <Box variant="small">{progress}</Box>}
                                        {progress && hasNodes(item) && (
                                            <Button
                                                variant="inline-link"
                                                ariaLabel={`View nodes of cascade ${shortId(
                                                    item.cascadeId
                                                )}`}
                                                onClick={() => setNodesCascadeId(item.cascadeId)}
                                            >
                                                View nodes
                                            </Button>
                                        )}
                                    </SpaceBetween>
                                );
                            },
                            sortingField: "state",
                        },
                        {
                            // The trigger asset with its database and ID beneath, in one column
                            // so the table fits the page at 1280 px.
                            id: "assetId",
                            header: `Trigger ${Synonyms.Asset}`,
                            cell: (item) => (
                                <SpaceBetween size="xxxs">
                                    <span>
                                        {item.assetName || cascadeTriggerAssetId(item) || "-"}
                                    </span>
                                    <Box variant="small" color="text-body-secondary">
                                        {[
                                            cascadeTriggerDatabaseId(item),
                                            item.assetName ? cascadeTriggerAssetId(item) : "",
                                        ]
                                            .filter(Boolean)
                                            .join(" / ")}
                                    </Box>
                                </SpaceBetween>
                            ),
                            sortingField: "triggeredByAssetId",
                        },
                        {
                            id: "triggerReason",
                            header: "Reason",
                            cell: (item) => item.triggerReason || "-",
                        },
                        {
                            // When the cascade was created, and by whom beneath.
                            id: "createdAt",
                            header: "Created",
                            cell: (item) => (
                                <SpaceBetween size="xxxs">
                                    <span>
                                        {item.createdAt
                                            ? new Date(item.createdAt).toLocaleString()
                                            : "-"}
                                    </span>
                                    {item.actor && (
                                        <Box variant="small" color="text-body-secondary">
                                            by {item.actor}
                                        </Box>
                                    )}
                                </SpaceBetween>
                            ),
                            sortingField: "createdAt",
                        },
                        {
                            id: "approvalTimeoutAt",
                            header: "Approval timeout",
                            cell: (item) =>
                                item.approvalTimeoutAt
                                    ? new Date(item.approvalTimeoutAt).toLocaleString()
                                    : "-",
                            sortingField: "approvalTimeoutAt",
                        },
                        ...(canApprove || canReject
                            ? [
                                  {
                                      id: "actions",
                                      header: "Actions",
                                      width: CASCADE_ACTIONS_COLUMN_WIDTH,
                                      minWidth: CASCADE_ACTIONS_COLUMN_WIDTH,
                                      // Only on the cascades the caller may approve: the service
                                      // computes that per cascade (its parent's database).
                                      cell: (item: CascadeRecord) =>
                                          item.state === "pending_approval" &&
                                          itemAllows(item, "approve") ? (
                                              <SpaceBetween direction="horizontal" size="s">
                                                  {canApprove && (
                                                      <Button
                                                          variant="inline-link"
                                                          onClick={() => setApproveTarget(item)}
                                                      >
                                                          Approve
                                                      </Button>
                                                  )}
                                                  {canReject && (
                                                      <Button
                                                          variant="inline-link"
                                                          onClick={() => setRejectTarget(item)}
                                                      >
                                                          Reject
                                                      </Button>
                                                  )}
                                              </SpaceBetween>
                                          ) : (
                                              "-"
                                          ),
                                  },
                              ]
                            : []),
                    ]}
                />

                <ReasonModal
                    visible={approveTarget !== null}
                    header={`Approve cascade ${
                        approveTarget ? shortId(approveTarget.cascadeId) : ""
                    }`}
                    label="Reason for approval"
                    description="Recorded in the compliance audit log with the approval."
                    confirmLabel="Approve"
                    optional
                    loading={approving}
                    onConfirm={handleApprove}
                    onDismiss={() => setApproveTarget(null)}
                />

                <ReasonModal
                    visible={rejectTarget !== null}
                    header={`Reject cascade ${rejectTarget ? shortId(rejectTarget.cascadeId) : ""}`}
                    label="Reason for rejection"
                    description="Recorded in the compliance audit log with the rejection."
                    confirmLabel="Reject"
                    placeholder="Why this cascade must not run"
                    optional
                    loading={rejecting}
                    onConfirm={handleReject}
                    onDismiss={() => setRejectTarget(null)}
                />

                {nodesRecord && (
                    <CascadeNodesModal
                        record={nodesRecord}
                        onDismiss={() => setNodesCascadeId(null)}
                    />
                )}
            </SpaceBetween>
        </Box>
    );
};

export default ComplianceCascades;
