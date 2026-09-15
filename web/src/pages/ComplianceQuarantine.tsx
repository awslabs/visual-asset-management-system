/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Link from "@cloudscape-design/components/link";
import Synonyms from "../synonyms";
import { appCache } from "../services/appCache";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import {
    fetchQuarantinedAssets,
    releaseFromQuarantine,
    grantException,
} from "../services/ComplianceService";

const ComplianceQuarantine: React.FC = () => {
    const config = appCache.getItem("config");
    const isComplianceEnabled = config?.featuresEnabled?.includes(featuresEnabled.COMPLIANCE);

    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        const [success, result] = await fetchQuarantinedAssets();
        if (success && Array.isArray(result)) {
            setItems(result);
        } else {
            setError(typeof result === "string" ? result : "Failed to load quarantined assets");
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (isComplianceEnabled) {
            loadData();
        }
    }, [isComplianceEnabled, loadData]);

    const handleRelease = async (item: any) => {
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

    const handleException = async (item: any) => {
        const reason = window.prompt("Enter reason for granting exception:");
        if (!reason) return;
        setActionMessage(null);
        setError(null);
        const [success, message] = await grantException(item.databaseId, item.assetId, reason);
        if (success) {
            setActionMessage(message);
            await loadData();
        } else {
            setError(message);
        }
    };

    if (!isComplianceEnabled) {
        return (
            <Box padding="l">
                <Alert type="info">
                    Compliance is not enabled for this deployment.
                </Alert>
            </Box>
        );
    }

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
                        counter={`(${items.length})`}
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button iconName="refresh" onClick={loadData} />
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
                empty={
                    <Box textAlign="center" padding="l">
                        <b>No quarantined {Synonyms.assets}</b>
                        <Box variant="p" color="inherit">
                            All {Synonyms.assets} are currently compliant or pending evaluation.
                        </Box>
                    </Box>
                }
                columnDefinitions={[
                    {
                        id: "assetName",
                        header: `${Synonyms.Asset} Name`,
                        cell: (item: any) => (
                            <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                                {item.assetName || item.assetId}
                            </Link>
                        ),
                        sortingField: "assetName",
                    },
                    {
                        id: "assetId",
                        header: `${Synonyms.Asset} ID`,
                        cell: (item: any) => item.assetId,
                        sortingField: "assetId",
                    },
                    {
                        id: "databaseId",
                        header: Synonyms.Database,
                        cell: (item: any) => item.databaseId,
                        sortingField: "databaseId",
                    },
                    {
                        id: "schemaName",
                        header: "Schema",
                        cell: (item: any) => item.schemaName || "-",
                        sortingField: "schemaName",
                    },
                    {
                        id: "quarantineReason",
                        header: "Reason",
                        cell: (item: any) => item.quarantineReason || "-",
                    },
                    {
                        id: "exceptionGranted",
                        header: "Exception",
                        cell: (item: any) =>
                            item.exceptionGranted ? (
                                <StatusIndicator type="warning">Granted</StatusIndicator>
                            ) : (
                                <StatusIndicator type="stopped">None</StatusIndicator>
                            ),
                    },
                    {
                        id: "updatedAt",
                        header: "Quarantined At",
                        cell: (item: any) =>
                            item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "-",
                        sortingField: "updatedAt",
                    },
                    {
                        id: "actions",
                        header: "Actions",
                        cell: (item: any) => (
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="normal" onClick={() => handleRelease(item)}>
                                    Release
                                </Button>
                                <Button variant="normal" onClick={() => handleException(item)}>
                                    Exception
                                </Button>
                            </SpaceBetween>
                        ),
                    },
                ]}
            />
        </SpaceBetween>
    );
};

export default ComplianceQuarantine;
