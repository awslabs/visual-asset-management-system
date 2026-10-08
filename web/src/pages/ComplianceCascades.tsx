/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback, useRef } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Synonyms from "../synonyms";
import {
    fetchCascades,
    fetchCascade,
    approveCascade,
    rejectCascade,
    CascadeRecord,
} from "../services/ComplianceService";
import ReasonModal from "../components/compliance/ReasonModal";

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

/** Node states that mean the node has not been evaluated yet. */
const NODE_STATES_IN_FLIGHT = new Set(["pending", "evaluating"]);

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

/** "n of total" nodes evaluated, read from the row's JSON node map. */
export const cascadeProgress = (record: CascadeRecord | null): string | null => {
    if (!record?.nodes) return null;
    try {
        const nodes: Record<string, string> = JSON.parse(record.nodes);
        const keys = Object.keys(nodes);
        const total = record.totalNodes ?? keys.length;
        const done = keys.filter((key) => !NODE_STATES_IN_FLIGHT.has(nodes[key])).length;
        return `${done} of ${total} nodes evaluated`;
    } catch {
        return null;
    }
};

const ComplianceCascades: React.FC = () => {
    const [items, setItems] = useState<CascadeRecord[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    const [rejectTarget, setRejectTarget] = useState<CascadeRecord | null>(null);
    const [rejecting, setRejecting] = useState(false);
    const [tracked, setTracked] = useState<Record<string, TrackedCascade>>({});

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

    const handleApprove = async (cascadeId: string) => {
        setActionMessage(null);
        setError(null);
        const [success, result] = await approveCascade(cascadeId);
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
        const [success, message] = await rejectCascade(cascadeId, reason);
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
            body = progress || "All nodes were evaluated.";
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
                </SpaceBetween>
            </Alert>
        );
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
            {Object.values(tracked).map(renderTracked)}

            <Table
                header={
                    <Header
                        variant="h1"
                        counter={`(${items.length})`}
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
                        cell: (item) => shortId(item.cascadeId),
                        sortingField: "cascadeId",
                    },
                    {
                        id: "state",
                        header: "Status",
                        cell: (item) => {
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
                        id: "databaseId",
                        header: `Trigger ${Synonyms.Database}`,
                        cell: (item) => cascadeTriggerDatabaseId(item) || "-",
                        sortingField: "triggeredByDatabaseId",
                    },
                    {
                        id: "assetId",
                        header: `Trigger ${Synonyms.Asset}`,
                        cell: (item) => cascadeTriggerAssetId(item) || "-",
                        sortingField: "triggeredByAssetId",
                    },
                    {
                        id: "triggerReason",
                        header: "Reason",
                        cell: (item) => item.triggerReason || "-",
                    },
                    {
                        id: "createdAt",
                        header: "Created",
                        cell: (item) =>
                            item.createdAt ? new Date(item.createdAt).toLocaleString() : "-",
                        sortingField: "createdAt",
                    },
                    {
                        id: "actor",
                        header: "Actor",
                        cell: (item) => item.actor || "-",
                    },
                    {
                        id: "actions",
                        header: "Actions",
                        cell: (item) =>
                            item.state === "pending_approval" ? (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button
                                        variant="primary"
                                        onClick={() => handleApprove(item.cascadeId)}
                                    >
                                        Approve
                                    </Button>
                                    <Button variant="normal" onClick={() => setRejectTarget(item)}>
                                        Reject
                                    </Button>
                                </SpaceBetween>
                            ) : (
                                "-"
                            ),
                    },
                ]}
            />

            <ReasonModal
                visible={rejectTarget !== null}
                header={`Reject cascade ${rejectTarget ? shortId(rejectTarget.cascadeId) : ""}`}
                label="Reason for rejection"
                description="Recorded in the compliance audit log with the rejection."
                confirmLabel="Reject"
                placeholder="Why this cascade must not run"
                loading={rejecting}
                onConfirm={handleReject}
                onDismiss={() => setRejectTarget(null)}
            />
        </SpaceBetween>
    );
};

export default ComplianceCascades;
