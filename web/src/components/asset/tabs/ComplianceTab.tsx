/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    StatusIndicator,
    Table,
    Alert,
} from "@cloudscape-design/components";
import { appCache } from "../../../services/appCache";
import { featuresEnabled } from "../../../common/constants/featuresEnabled";
import {
    fetchComplianceState,
    fetchEvaluationHistory,
    evaluateAssetCompliance,
    releaseFromQuarantine,
    grantException,
    ComplianceState,
    EvaluationRecord,
} from "../../../services/ComplianceService";

interface ComplianceTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
}

const stateIndicatorMap: Record<string, { type: string; label: string }> = {
    unknown: { type: "stopped", label: "Unknown" },
    pending_evaluation: { type: "in-progress", label: "Pending Evaluation" },
    compliant: { type: "success", label: "Compliant" },
    non_compliant: { type: "warning", label: "Non-Compliant" },
    quarantined: { type: "error", label: "Quarantined" },
    pending_parent_resolution: { type: "warning", label: "Pending Parent Resolution" },
};

export const ComplianceTab: React.FC<ComplianceTabProps> = ({ databaseId, assetId, isActive }) => {
    const config = appCache.getItem("config");
    const isFMMEnabled = config?.featuresEnabled?.includes(featuresEnabled.FMM);

    const [complianceState, setComplianceState] = useState<ComplianceState | null>(null);
    const [evaluations, setEvaluations] = useState<EvaluationRecord[]>([]);
    const [loading, setLoading] = useState(false);
    const [evaluating, setEvaluating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    useEffect(() => {
        if (isActive && isFMMEnabled && databaseId && assetId) {
            loadComplianceData();
        }
    }, [isActive, databaseId, assetId]);

    const loadComplianceData = async () => {
        setLoading(true);
        setError(null);

        try {
            const [stateSuccess, stateResult] = await fetchComplianceState(databaseId, assetId);
            if (stateSuccess && typeof stateResult !== "string") {
                setComplianceState(stateResult);
            }

            const [evalSuccess, evalResult] = await fetchEvaluationHistory(databaseId, assetId);
            if (evalSuccess && Array.isArray(evalResult)) {
                setEvaluations(evalResult);
            }
        } catch (err: any) {
            setError(err?.message || "Failed to load compliance data");
        } finally {
            setLoading(false);
        }
    };

    const handleEvaluate = async () => {
        setEvaluating(true);
        setActionMessage(null);

        const [success, result] = await evaluateAssetCompliance(databaseId, assetId);
        if (success) {
            setActionMessage("Evaluation triggered successfully");
            await loadComplianceData();
        } else {
            setError(typeof result === "string" ? result : "Evaluation failed");
        }
        setEvaluating(false);
    };

    const handleRelease = async () => {
        const [success, message] = await releaseFromQuarantine(databaseId, assetId);
        if (success) {
            setActionMessage(message);
            await loadComplianceData();
        } else {
            setError(message);
        }
    };

    const handleException = async () => {
        const reason = window.prompt("Enter reason for exception:");
        if (!reason) return;

        const [success, message] = await grantException(databaseId, assetId, reason);
        if (success) {
            setActionMessage(message);
            await loadComplianceData();
        } else {
            setError(message);
        }
    };

    if (!isFMMEnabled) {
        return (
            <Box padding="l">
                <Alert type="info">
                    Federated Model Management (FMM) is not enabled for this deployment. Contact
                    your administrator to enable compliance features.
                </Alert>
            </Box>
        );
    }

    const indicator = complianceState
        ? stateIndicatorMap[complianceState.complianceState] || stateIndicatorMap.unknown
        : stateIndicatorMap.unknown;

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

            <Container
                header={
                    <Header
                        variant="h3"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                {complianceState?.complianceState === "quarantined" && (
                                    <>
                                        <Button onClick={handleRelease}>Release</Button>
                                        <Button onClick={handleException}>Grant Exception</Button>
                                    </>
                                )}
                                <Button
                                    variant="primary"
                                    loading={evaluating}
                                    onClick={handleEvaluate}
                                >
                                    Evaluate Now
                                </Button>
                            </SpaceBetween>
                        }
                    >
                        Compliance Status
                    </Header>
                }
            >
                <SpaceBetween size="m">
                    <div>
                        <Box variant="awsui-key-label">Current State</Box>
                        <StatusIndicator type={indicator.type as any}>
                            {indicator.label}
                        </StatusIndicator>
                    </div>
                    {complianceState?.schemaName && (
                        <div>
                            <Box variant="awsui-key-label">Assigned Schema</Box>
                            <div>{complianceState.schemaName}</div>
                        </div>
                    )}
                    {complianceState?.lastEvaluationAt && (
                        <div>
                            <Box variant="awsui-key-label">Last Evaluated</Box>
                            <div>{new Date(complianceState.lastEvaluationAt).toLocaleString()}</div>
                        </div>
                    )}
                </SpaceBetween>
            </Container>

            <Container header={<Header variant="h3">Evaluation History</Header>}>
                <Table
                    loading={loading}
                    items={evaluations}
                    empty={
                        <Box textAlign="center" padding="l">
                            No evaluations recorded for this asset.
                        </Box>
                    }
                    columnDefinitions={[
                        {
                            id: "evaluatedAt",
                            header: "Date",
                            cell: (item) => new Date(item.evaluatedAt).toLocaleString(),
                            sortingField: "evaluatedAt",
                        },
                        {
                            id: "schemaName",
                            header: "Schema",
                            cell: (item) => item.schemaName,
                        },
                        {
                            id: "result",
                            header: "Result",
                            cell: (item) => (
                                <StatusIndicator
                                    type={item.result === "compliant" ? "success" : "error"}
                                >
                                    {item.result === "compliant" ? "Compliant" : "Non-Compliant"}
                                </StatusIndicator>
                            ),
                        },
                        {
                            id: "violations",
                            header: "Violations",
                            cell: (item) =>
                                item.violations?.length
                                    ? item.violations.join(", ")
                                    : "-",
                        },
                    ]}
                />
            </Container>
        </SpaceBetween>
    );
};

export default ComplianceTab;
