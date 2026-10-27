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
    QuarantinedAsset,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import ReasonModal from "../components/compliance/ReasonModal";
import ComplianceAssetCell from "../components/compliance/ComplianceAssetCell";
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
    useUrlFilters,
} from "../components/compliance/listingFilters";

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
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
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

    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            setLoading(true);
            setError(null);
            setSelectedItems([]);
            const [success, result] = await fetchQuarantinedAssets(
                { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken },
                { databaseId: filters.databaseId, text: filters.text }
            );
            if (success && typeof result !== "string") {
                setItems(result.quarantinedAssets);
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (result.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: result.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setError(typeof result === "string" ? result : "Failed to load quarantined assets");
                setItems([]);
                setHasMore(false);
            }
            setLoading(false);
        },
        [filters.databaseId, filters.text]
    );

    // A reload (or a filter change) restarts the walk: tokens held from a previous listing
    // address rows that may no longer be quarantined, and a token is issued for its filters.
    const loadData = useCallback(() => {
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
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

    // A release needs a reason, as on the asset's Compliance tab; it is recorded in the audit log.
    const handleRelease = async (reason: string) => {
        if (!releaseTarget) return;
        const item = releaseTarget;
        setReleasing(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await releaseFromQuarantine(
            item.databaseId,
            item.assetId,
            reason
        );
        setReleasing(false);
        setReleaseTarget(null);
        if (success) {
            setActionMessage(`${assetLabel(item)} released from quarantine.`);
            await loadData();
        } else {
            setError(message);
        }
    };

    const handleException = async (reason: string) => {
        if (!exceptionTarget) return;
        const target = exceptionTarget;
        const { databaseId, assetId } = target;
        setGranting(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await grantException(databaseId, assetId, reason);
        setGranting(false);
        setExceptionTarget(null);
        if (success) {
            setActionMessage(`Exception granted for ${assetLabel(target)}.`);
            await loadData();
        } else {
            setError(message);
        }
    };

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

                <Table
                    wrapLines
                    stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
                    header={
                        <Header
                            variant="h1"
                            counter={`(${items.length}${hasMore ? "+" : ""})`}
                            actions={
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button
                                        iconName="refresh"
                                        ariaLabel={`Refresh quarantined ${Synonyms.assets}`}
                                        onClick={loadData}
                                        loading={loading}
                                    />
                                </SpaceBetween>
                            }
                        >
                            Quarantined {Synonyms.Assets}
                        </Header>
                    }
                    filter={
                        <SpaceBetween direction="horizontal" size="xs">
                            <DatabaseFilter
                                value={filters.databaseId}
                                onChange={(databaseId) => setFilters({ databaseId })}
                            />
                            <TextFilterInput
                                value={filters.text}
                                onCommit={(text) => setFilters({ text })}
                                placeholder={`Search ${Synonyms.asset} name or ID`}
                                ariaLabel={`Search quarantined ${Synonyms.assets}`}
                            />
                        </SpaceBetween>
                    }
                    loading={loading}
                    items={items}
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
                        <Box textAlign="center" padding="l">
                            <b>No quarantined {Synonyms.assets}</b>
                            <Box variant="p" color="inherit">
                                {hasMore
                                    ? `None of the ${Synonyms.assets} on this page are visible to you. Open the next page to continue.`
                                    : `All ${Synonyms.assets} are currently compliant or pending evaluation.`}
                            </Box>
                        </Box>
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
                        },
                        {
                            id: "databaseId",
                            header: Synonyms.Database,
                            cell: (item) => item.databaseId,
                            sortingField: "databaseId",
                        },
                        {
                            id: "schemaName",
                            header: "Bound schema",
                            cell: (item) => item.schemaName || "-",
                            sortingField: "schemaName",
                        },
                        {
                            id: "quarantineReason",
                            header: "Reason",
                            // A reason quotes rule messages (paths, patterns) that may hold long
                            // unbroken tokens; they wrap anywhere rather than widen the table.
                            cell: (item) => (
                                <span style={WRAP_ANYWHERE_STYLE}>
                                    {item.quarantineReason || "-"}
                                </span>
                            ),
                        },
                        // No State column: every asset on this page is quarantined.
                        {
                            id: "updatedAt",
                            header: "Quarantined At",
                            minWidth: TIMESTAMP_COLUMN_MIN_WIDTH,
                            cell: (item) =>
                                item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "-",
                            sortingField: "updatedAt",
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
