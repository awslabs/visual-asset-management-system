/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Synonyms from "../synonyms";
import { appCache } from "../services/appCache";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import {
    fetchCascades,
    approveCascade,
    rejectCascade,
} from "../services/ComplianceService";

const statusIndicatorMap: Record<string, { type: string; label: string }> = {
    pending_approval: { type: "pending", label: "Pending Approval" },
    executing: { type: "in-progress", label: "Executing" },
    completed: { type: "success", label: "Completed" },
    aborted: { type: "error", label: "Aborted" },
};

const ComplianceCascades: React.FC = () => {
    const config = appCache.getItem("config");
    const isFMMEnabled = config?.featuresEnabled?.includes(featuresEnabled.FMM);

    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        const [success, result] = await fetchCascades();
        if (success && Array.isArray(result)) {
            setItems(result);
        } else {
            setError(typeof result === "string" ? result : "Failed to load cascades");
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (isFMMEnabled) {
            loadData();
        }
    }, [isFMMEnabled, loadData]);

    const handleApprove = async (cascadeId: string) => {
        setActionMessage(null);
        setError(null);
        const [success, message] = await approveCascade(cascadeId);
        if (success) {
            setActionMessage(message);
            await loadData();
        } else {
            setError(message);
        }
    };

    const handleReject = async (cascadeId: string) => {
        const reason = window.prompt("Enter reason for rejection:");
        if (!reason) return;
        setActionMessage(null);
        setError(null);
        const [success, message] = await rejectCascade(cascadeId);
        if (success) {
            setActionMessage(message);
            await loadData();
        } else {
            setError(message);
        }
    };

    if (!isFMMEnabled) {
        return (
            <Box padding="l">
                <Alert type="info">
                    Federated Model Management (FMM) is not enabled for this deployment.
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
                            <Button iconName="refresh" onClick={loadData} />
                        }
                    >
                        Cascade Approval Queue
                    </Header>
                }
                loading={loading}
                items={items}
                empty={
                    <Box textAlign="center" padding="l">
                        <b>No cascades</b>
                        <Box variant="p" color="inherit">
                            No cascade operations are pending approval.
                        </Box>
                    </Box>
                }
                columnDefinitions={[
                    {
                        id: "cascadeId",
                        header: "Cascade ID",
                        cell: (item: any) => item.cascadeId?.substring(0, 8) + "...",
                        sortingField: "cascadeId",
                    },
                    {
                        id: "state",
                        header: "Status",
                        cell: (item: any) => {
                            const indicator = statusIndicatorMap[item.state] || {
                                type: "stopped",
                                label: item.state || "Unknown",
                            };
                            return (
                                <StatusIndicator type={indicator.type as any}>
                                    {indicator.label}
                                </StatusIndicator>
                            );
                        },
                        sortingField: "state",
                    },
                    {
                        id: "triggeredByDatabaseId",
                        header: `Trigger ${Synonyms.Database}`,
                        cell: (item: any) => item.triggeredByDatabaseId || "-",
                        sortingField: "triggeredByDatabaseId",
                    },
                    {
                        id: "triggeredByAssetId",
                        header: `Trigger ${Synonyms.Asset}`,
                        cell: (item: any) => item.triggeredByAssetId || "-",
                        sortingField: "triggeredByAssetId",
                    },
                    {
                        id: "triggerReason",
                        header: "Reason",
                        cell: (item: any) => item.triggerReason || "-",
                    },
                    {
                        id: "createdAt",
                        header: "Created",
                        cell: (item: any) =>
                            item.createdAt ? new Date(item.createdAt).toLocaleString() : "-",
                        sortingField: "createdAt",
                    },
                    {
                        id: "actor",
                        header: "Actor",
                        cell: (item: any) => item.actor || "-",
                    },
                    {
                        id: "actions",
                        header: "Actions",
                        cell: (item: any) =>
                            item.state === "pending_approval" ? (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button
                                        variant="primary"
                                        onClick={() => handleApprove(item.cascadeId)}
                                    >
                                        Approve
                                    </Button>
                                    <Button
                                        variant="normal"
                                        onClick={() => handleReject(item.cascadeId)}
                                    >
                                        Reject
                                    </Button>
                                </SpaceBetween>
                            ) : (
                                "-"
                            ),
                    },
                ]}
            />
        </SpaceBetween>
    );
};

export default ComplianceCascades;
