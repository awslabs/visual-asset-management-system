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
import Link from "@cloudscape-design/components/link";
import Synonyms from "../synonyms";
import {
    fetchQuarantinedAssets,
    releaseFromQuarantine,
    grantException,
    QuarantinedAsset,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import ReasonModal from "../components/compliance/ReasonModal";
import { ComplianceStateBadge } from "../components/compliance/complianceStateBadge";

const ComplianceQuarantine: React.FC = () => {
    const [items, setItems] = useState<QuarantinedAsset[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    const [selectedItems, setSelectedItems] = useState<QuarantinedAsset[]>([]);
    const [exceptionTarget, setExceptionTarget] = useState<QuarantinedAsset | null>(null);
    const [granting, setGranting] = useState(false);

    // Server-side token paging: tokens[i] is the startingToken that fetches page i
    // (tokens[0] is undefined). hasMore tracks whether the loaded page reported a
    // NextToken, which is what Pagination's openEnd renders.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    const loadPage = useCallback(async (pageNumber: number, startingToken: string | undefined) => {
        setLoading(true);
        setError(null);
        setSelectedItems([]);
        const [success, result] = await fetchQuarantinedAssets({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken,
        });
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
    }, []);

    // A reload restarts the walk: tokens held from a previous listing address rows that
    // may no longer be quarantined.
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

    const handleRelease = async (item: QuarantinedAsset) => {
        setActionMessage(null);
        setError(null);
        const [success, message] = await releaseFromQuarantine(item.databaseId, item.assetId);
        if (success) {
            setActionMessage(message);
            await loadData();
        } else {
            setError(message);
        }
    };

    const handleException = async (reason: string) => {
        if (!exceptionTarget) return;
        const { databaseId, assetId } = exceptionTarget;
        setGranting(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await grantException(databaseId, assetId, reason);
        setGranting(false);
        setExceptionTarget(null);
        if (success) {
            setActionMessage(message);
            await loadData();
        } else {
            setError(message);
        }
    };

    return (
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
                        id: "assetName",
                        header: `${Synonyms.Asset} Name`,
                        cell: (item) => (
                            <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                                {item.assetName || item.assetId}
                            </Link>
                        ),
                        sortingField: "assetName",
                    },
                    {
                        id: "assetId",
                        header: `${Synonyms.Asset} ID`,
                        cell: (item) => item.assetId,
                        sortingField: "assetId",
                    },
                    {
                        id: "databaseId",
                        header: Synonyms.Database,
                        cell: (item) => item.databaseId,
                        sortingField: "databaseId",
                    },
                    {
                        id: "schemaName",
                        header: "Schema",
                        cell: (item) => item.schemaName || "-",
                        sortingField: "schemaName",
                    },
                    {
                        id: "quarantineReason",
                        header: "Reason",
                        cell: (item) => item.quarantineReason || "-",
                    },
                    {
                        id: "complianceState",
                        header: "State",
                        cell: (item) => <ComplianceStateBadge state={item.complianceState} />,
                        sortingField: "complianceState",
                    },
                    {
                        id: "updatedAt",
                        header: "Quarantined At",
                        cell: (item) =>
                            item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "-",
                        sortingField: "updatedAt",
                    },
                    {
                        id: "actions",
                        header: "Actions",
                        cell: (item) => (
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="normal" onClick={() => handleRelease(item)}>
                                    Release
                                </Button>
                                <Button variant="normal" onClick={() => setExceptionTarget(item)}>
                                    Exception
                                </Button>
                            </SpaceBetween>
                        ),
                    },
                ]}
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
    );
};

export default ComplianceQuarantine;
