/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Synonyms from "../synonyms";
import {
    fetchQuarantinedAssets,
    releaseFromQuarantine,
    grantException,
    itemAllows,
    awaitingReevaluationText,
    QuarantinedAsset,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import ReasonModal from "../components/compliance/ReasonModal";
import ComplianceAssetCell from "../components/compliance/ComplianceAssetCell";
import ExecutionFailureReasons from "../components/compliance/ExecutionFailureReasons";
import { useAssetPageAccess } from "../hooks/useAssetPageAccess";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";
import {
    ROW_ACTIONS_STICKY_COLUMNS,
    TIMESTAMP_COLUMN_MIN_WIDTH,
    VAMS_PAGE_PADDING,
    WRAP_ANYWHERE_STYLE,
} from "../components/compliance/rowActions";
import {
    DatabaseFilter,
    TextFilterInput,
    useLatestRequest,
    useUrlFilters,
} from "../components/compliance/listingFilters";
import {
    ListingLoadFailed,
    listedBefore,
    listingEmptyText,
    loadFailureMessage,
} from "../components/compliance/listingStates";
import ListToolbar from "../components/list/ListToolbar";
import { PAGE_SORTING_NOTE, byDate, byText, useSortedItems } from "../components/list/tableSorting";

/** The listing's server-side filters, kept in the URL. */
export const QUARANTINE_FILTER_KEYS = ["databaseId", "text"] as const;

/**
 * Width (px) of the Actions column: the Release and Exception inline links side by side (about
 * 140 px of labels and gap) with the cell padding. See ROW_ACTIONS_STICKY_COLUMNS.
 */
export const QUARANTINE_ACTIONS_COLUMN_WIDTH = 200;

// The routes behind the row actions (backend/backend/common/apiRoutes.py); each is offered only to a
// caller who may call it, none while the routes load.
export const QUARANTINE_RELEASE_API_ROUTE = "/compliance/quarantine/{databaseId}/{assetId}/release";
export const QUARANTINE_EXCEPTION_API_ROUTE =
    "/compliance/quarantine/{databaseId}/{assetId}/exception";

/** How an asset is named in the page's messages: its name, else its id (never `db:asset`). */
const assetLabel = (item: QuarantinedAsset): string => item.assetName || item.assetId;

const ComplianceQuarantine: React.FC = () => {
    // Asset names link to the asset page only when the caller may open it.
    const { canOpenAsset } = useAssetPageAccess();
    const { can: canCallRoute } = useAllowedRoutes();
    const canRelease = canCallRoute("POST", QUARANTINE_RELEASE_API_ROUTE);
    const canGrantException = canCallRoute("POST", QUARANTINE_EXCEPTION_API_ROUTE);
    const [items, setItems] = useState<QuarantinedAsset[]>([]);
    const [loading, setLoading] = useState(true);
    // Why the last listing load failed.
    const [error, setError] = useState<string | null>(null);
    // Whether the last listing load failed: the table then says the list could not be loaded,
    // not that every asset is compliant.
    const [loadFailed, setLoadFailed] = useState(false);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    // Why the API refused the last row action. Kept apart from `error`, which every load clears,
    // so the refusal stays on the page while the listing is read again after it.
    const [actionError, setActionError] = useState<string | null>(null);
    const [selectedItems, setSelectedItems] = useState<QuarantinedAsset[]>([]);
    const [exceptionTarget, setExceptionTarget] = useState<QuarantinedAsset | null>(null);
    const [granting, setGranting] = useState(false);
    const [releaseTarget, setReleaseTarget] = useState<QuarantinedAsset | null>(null);
    const [releasing, setReleasing] = useState(false);
    const [filters, setFilters] = useUrlFilters(QUARANTINE_FILTER_KEYS);

    // Server-side token paging: tokens[i] is the startingToken that fetches page i
    // (tokens[0] is undefined). hasMore tracks whether the loaded page reported a
    // NextToken, which is what Pagination's openEnd renders.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);
    // Which loaded pages (0-based) listed an asset (listingStates.listedBefore).
    const [listedPages, setListedPages] = useState<Record<number, boolean>>({});
    const filtered = QUARANTINE_FILTER_KEYS.some((key) => filters[key]);
    // The rows of the page shown, in the order of the column header chosen.
    const { items: sortedItems, sortingProps } = useSortedItems(items);
    const isPaging = hasMore || currentPageIndex > 1;

    // A load is dropped when a later one has begun (other filters, another page), so the rows
    // shown are always the latest request's.
    const beginLoad = useLatestRequest();
    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            const isCurrent = beginLoad();
            setLoading(true);
            setError(null);
            setLoadFailed(false);
            setSelectedItems([]);
            const [success, result] = await fetchQuarantinedAssets(
                { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken },
                { databaseId: filters.databaseId, text: filters.text }
            );
            if (!isCurrent()) return;
            if (success && typeof result !== "string") {
                setItems(result.quarantinedAssets);
                setListedPages((prev) => ({
                    ...prev,
                    [pageNumber]: result.quarantinedAssets.length > 0,
                }));
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (result.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: result.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setError(loadFailureMessage(`the quarantined ${Synonyms.assets}`, result));
                setLoadFailed(true);
                setItems([]);
                setHasMore(false);
            }
            setLoading(false);
        },
        [beginLoad, filters.databaseId, filters.text]
    );

    // A reload (or a filter change) restarts the walk: tokens held from a previous listing
    // address rows that may no longer be quarantined, and a token is issued for its filters.
    const loadData = useCallback(() => {
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        setListedPages({});
        setHasMore(false);
        return loadPage(0, undefined);
    }, [loadPage]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        const newIndex = detail.currentPageIndex;
        setCurrentPageIndex(newIndex);
        loadPage(newIndex - 1, tokens[newIndex - 1]);
    };

    // Clears the last action's outcome before the next action is sent.
    const beginAction = () => {
        setActionMessage(null);
        setActionError(null);
    };

    // Reports a row action's outcome, then reads the listing again whether the action succeeded
    // or was refused (D162). A refusal usually means the row is out of date: another tab or user
    // released the asset ("Asset is not quarantined"), or its record changed while the request was
    // made (D159). After the read the rows and their actions follow the records as the API now
    // holds them; the refusal stays shown and names the asset, whose row may have left the page.
    const finishAction = async (outcome: { message: string } | { refusal: string }) => {
        if ("message" in outcome) {
            setActionMessage(outcome.message);
        } else {
            setActionError(outcome.refusal);
        }
        await loadData();
    };

    // A release needs a reason, as on the asset's Compliance tab; it is recorded in the audit log.
    const handleRelease = async (reason: string) => {
        if (!releaseTarget) return;
        const item = releaseTarget;
        setReleasing(true);
        beginAction();
        const [success, message] = await releaseFromQuarantine(
            item.databaseId,
            item.assetId,
            reason
        );
        setReleasing(false);
        setReleaseTarget(null);
        await finishAction(
            success
                ? { message: `${assetLabel(item)} released from quarantine.` }
                : { refusal: `${assetLabel(item)} was not released: ${message}` }
        );
    };

    const handleException = async (reason: string) => {
        if (!exceptionTarget) return;
        const target = exceptionTarget;
        const { databaseId, assetId } = target;
        setGranting(true);
        beginAction();
        const [success, message] = await grantException(databaseId, assetId, reason);
        setGranting(false);
        setExceptionTarget(null);
        await finishAction(
            success
                ? { message: `Exception granted for ${assetLabel(target)}.` }
                : { refusal: `No exception was granted for ${assetLabel(target)}: ${message}` }
        );
    };

    return (
        // The page margins every VAMS list page uses (VAMS_PAGE_PADDING).
        <Box padding={VAMS_PAGE_PADDING}>
            <SpaceBetween size="l">
                {actionError && (
                    <Alert type="error" dismissible onDismiss={() => setActionError(null)}>
                        {actionError}
                    </Alert>
                )}
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

                <Table
                    wrapLines
                    stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
                    header={
                        <Header
                            variant="h1"
                            counter={
                                loadFailed ? undefined : `(${items.length}${hasMore ? "+" : ""})`
                            }
                            description={isPaging ? PAGE_SORTING_NOTE : undefined}
                        >
                            Quarantined {Synonyms.Assets}
                        </Header>
                    }
                    filter={
                        <ListToolbar
                            search={
                                <TextFilterInput
                                    value={filters.text}
                                    onCommit={(text) => setFilters({ text })}
                                    placeholder={`Search ${Synonyms.asset} name or ID`}
                                    ariaLabel={`Search quarantined ${Synonyms.assets}`}
                                />
                            }
                            onRefresh={loadData}
                            refreshing={loading}
                            refreshLabel={`Refresh quarantined ${Synonyms.assets}`}
                            filters={
                                <DatabaseFilter
                                    value={filters.databaseId}
                                    onChange={(databaseId) => setFilters({ databaseId })}
                                />
                            }
                        />
                    }
                    loading={loading}
                    items={sortedItems}
                    {...sortingProps}
                    selectedItems={selectedItems}
                    onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
                    selectionType="single"
                    pagination={
                        <Pagination
                            currentPageIndex={currentPageIndex}
                            pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                            openEnd={hasMore}
                            onChange={handlePageChange}
                            disabled={loading}
                            ariaLabels={{
                                nextPageLabel: `Next page of quarantined ${Synonyms.assets}`,
                                previousPageLabel: `Previous page of quarantined ${Synonyms.assets}`,
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of quarantined ${Synonyms.assets}`,
                            }}
                        />
                    }
                    empty={
                        loadFailed ? (
                            <ListingLoadFailed what={`The quarantined ${Synonyms.assets}`} />
                        ) : (
                            <Box textAlign="center" padding="l">
                                <b>No quarantined {Synonyms.assets}</b>
                                <Box variant="p" color="inherit">
                                    {listingEmptyText(
                                        {
                                            hasMore,
                                            pageIndex: currentPageIndex,
                                            listedEarlier: listedBefore(
                                                listedPages,
                                                currentPageIndex
                                            ),
                                            filtered,
                                        },
                                        `quarantined ${Synonyms.assets}`,
                                        `All ${Synonyms.assets} are currently compliant or pending evaluation.`
                                    )}
                                </Box>
                            </Box>
                        )
                    }
                    columnDefinitions={[
                        {
                            id: "asset",
                            header: Synonyms.Asset,
                            cell: (item) => (
                                <ComplianceAssetCell
                                    databaseId={item.databaseId}
                                    assetId={item.assetId}
                                    assetName={item.assetName}
                                    linked={canOpenAsset}
                                />
                            ),
                            sortingField: "assetName",
                            sortingComparator: byText(assetLabel),
                        },
                        {
                            id: "databaseId",
                            header: Synonyms.Database,
                            cell: (item) => item.databaseId,
                            sortingField: "databaseId",
                            sortingComparator: byText((item) => item.databaseId),
                        },
                        {
                            id: "schemaName",
                            header: "Bound schema",
                            cell: (item) => item.schemaName || "-",
                            sortingField: "schemaName",
                            sortingComparator: byText((item) => item.schemaName),
                        },
                        {
                            id: "quarantineReason",
                            header: "Reason",
                            // A reason quotes rule messages (paths, patterns) that may hold long
                            // unbroken tokens; they wrap anywhere rather than widen the table. The
                            // failed pipeline rules' execution failure reasons follow, for a caller
                            // who may read those executions.
                            cell: (item) => (
                                <>
                                    <span style={WRAP_ANYWHERE_STYLE}>
                                        {item.quarantineReason || "-"}
                                    </span>
                                    <ExecutionFailureReasons
                                        entries={item.quarantineExecutionFailures}
                                    />
                                    {awaitingReevaluationText(item) && (
                                        <Box variant="small" color="text-status-warning">
                                            {awaitingReevaluationText(item)}
                                        </Box>
                                    )}
                                </>
                            ),
                        },
                        // No State column: every asset on this page is quarantined. The time the
                        // quarantine started, as the API records it; `updatedAt` moves with every
                        // change to the record (a binding change that keeps the quarantine, D137).
                        {
                            id: "quarantinedAt",
                            header: "Quarantined At",
                            minWidth: TIMESTAMP_COLUMN_MIN_WIDTH,
                            cell: (item) =>
                                item.quarantinedAt
                                    ? new Date(item.quarantinedAt).toLocaleString()
                                    : "-",
                            sortingField: "quarantinedAt",
                            sortingComparator: byDate((item) => item.quarantinedAt),
                        },
                        ...(canRelease || canGrantException
                            ? [
                                  {
                                      id: "actions",
                                      header: "Actions",
                                      width: QUARANTINE_ACTIONS_COLUMN_WIDTH,
                                      minWidth: QUARANTINE_ACTIONS_COLUMN_WIDTH,
                                      cell: (item: QuarantinedAsset) => (
                                          <SpaceBetween direction="horizontal" size="s">
                                              {/* Per row: the service computes whether the
                                                  caller may act in the row's database. */}
                                              {canRelease && itemAllows(item, "release") && (
                                                  <Button
                                                      variant="inline-link"
                                                      onClick={() => setReleaseTarget(item)}
                                                  >
                                                      Release
                                                  </Button>
                                              )}
                                              {canGrantException &&
                                                  itemAllows(item, "exception") && (
                                                      <Button
                                                          variant="inline-link"
                                                          onClick={() => setExceptionTarget(item)}
                                                      >
                                                          Exception
                                                      </Button>
                                                  )}
                                          </SpaceBetween>
                                      ),
                                  },
                              ]
                            : []),
                    ]}
                />

                <ReasonModal
                    visible={releaseTarget !== null}
                    header={`Release ${
                        releaseTarget ? assetLabel(releaseTarget) : ""
                    } from quarantine`}
                    label="Reason for release"
                    description={`Why this ${Synonyms.asset} leaves quarantine. Recorded in the compliance audit log; the next evaluation may quarantine it again.`}
                    confirmLabel={`Release ${Synonyms.asset}`}
                    loading={releasing}
                    onConfirm={handleRelease}
                    onDismiss={() => setReleaseTarget(null)}
                />

                <ReasonModal
                    visible={exceptionTarget !== null}
                    header={`Grant exception for ${
                        exceptionTarget?.assetName || exceptionTarget?.assetId || ""
                    }`}
                    label="Reason for exception"
                    description={`Why this ${Synonyms.asset} may stay in use while quarantined. Recorded in the compliance audit log.`}
                    confirmLabel="Grant exception"
                    loading={granting}
                    onConfirm={handleException}
                    onDismiss={() => setExceptionTarget(null)}
                />
            </SpaceBetween>
        </Box>
    );
};

export default ComplianceQuarantine;
