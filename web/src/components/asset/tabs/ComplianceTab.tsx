/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import { useAllowedRoutes } from "../../../features/orchestration/permissions/useAllowedRoutes";
import {
    EXECUTION_DETAILS_API_ROUTE,
    executionDetailPath,
} from "../../filemanager/utils/executionLinks";
import {
    fetchComplianceState,
    fetchEvaluationHistory,
    evaluateAssetCompliance,
    releaseFromQuarantine,
    grantException,
    revokeException,
    erroredRuleNames,
    executionFailureReasons,
    ComplianceState,
    EvaluationRecord,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../../../services/ComplianceService";
import ReasonModal from "../../compliance/ReasonModal";
import RevokeExceptionModal from "../../compliance/RevokeExceptionModal";
import ExecutionFailureReasons from "../../compliance/ExecutionFailureReasons";
import { evaluationRuleResults } from "../../compliance/EvaluationHistoryModal";
import {
    ComplianceStateBadge,
    EvaluationErrorIndicator,
    StatusLabel,
    schemaSourceLabel,
} from "../../compliance/complianceStateBadge";
import Synonyms from "../../../synonyms";
import { useLatestRequest } from "../../compliance/listingFilters";

interface ComplianceTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
}

// API routes the tab reads and acts through; each control is shown only when its route is allowed.
const COMPLIANCE_STATE_API_ROUTE = "/compliance/state/{databaseId}/{assetId}";
const COMPLIANCE_EVALUATE_API_ROUTE = "/compliance/evaluate/{databaseId}/{assetId}";
const COMPLIANCE_RELEASE_API_ROUTE = "/compliance/quarantine/{databaseId}/{assetId}/release";
const COMPLIANCE_EXCEPTION_API_ROUTE = "/compliance/quarantine/{databaseId}/{assetId}/exception";

const verdictIndicatorMap: Record<string, { type: string; label: string }> = {
    compliant: { type: "success", label: "Compliant" },
    non_compliant: { type: "warning", label: "Non-Compliant" },
    quarantined: { type: "error", label: "Quarantined" },
    pending_pipeline: { type: "in-progress", label: "Pipeline Running" },
    error: { type: "error", label: "Error" },
};

const formatTimestamp = (value?: string) => (value ? new Date(value).toLocaleString() : "-");

const ComplianceTabForAsset: React.FC<ComplianceTabProps> = ({ databaseId, assetId, isActive }) => {
    const navigate = useNavigate();
    const { can: canCallRoute, loading: routesLoading } = useAllowedRoutes();
    const canViewCompliance = canCallRoute("GET", COMPLIANCE_STATE_API_ROUTE);
    const canEvaluate = canCallRoute("POST", COMPLIANCE_EVALUATE_API_ROUTE);
    const canRelease = canCallRoute("POST", COMPLIANCE_RELEASE_API_ROUTE);
    const canGrantException = canCallRoute("POST", COMPLIANCE_EXCEPTION_API_ROUTE);
    const canRevokeException = canCallRoute("DELETE", COMPLIANCE_EXCEPTION_API_ROUTE);
    const canViewExecution = canCallRoute("GET", EXECUTION_DETAILS_API_ROUTE);

    const [complianceState, setComplianceState] = useState<ComplianceState | null>(null);
    const [evaluations, setEvaluations] = useState<EvaluationRecord[]>([]);
    const [loading, setLoading] = useState(false);
    const [evaluating, setEvaluating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    const [releaseModalVisible, setReleaseModalVisible] = useState(false);
    const [releasing, setReleasing] = useState(false);
    const [exceptionModalVisible, setExceptionModalVisible] = useState(false);
    const [granting, setGranting] = useState(false);
    const [revokeModalVisible, setRevokeModalVisible] = useState(false);
    const [revoking, setRevoking] = useState(false);

    // Server-side token paging of the evaluation history: tokens[i] is the startingToken that
    // fetches page i (tokens[0] is undefined).
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    // Each load is dropped when a later one has begun (another asset, another page, a reload
    // after an action), so the state and history shown are always the latest request's.
    const beginStateLoad = useLatestRequest();
    const beginHistoryLoad = useLatestRequest();
    const loadEvaluationsPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            const isCurrent = beginHistoryLoad();
            const [evalSuccess, evalResult] = await fetchEvaluationHistory(databaseId, assetId, {
                maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
                startingToken,
            });
            if (!isCurrent()) return;
            if (evalSuccess && typeof evalResult !== "string") {
                setEvaluations(evalResult.evaluations);
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (evalResult.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: evalResult.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setHasMore(false);
            }
        },
        [beginHistoryLoad, databaseId, assetId]
    );

    // Reads the current state and restarts the history walk from its first page.
    const loadComplianceData = useCallback(async () => {
        const isCurrent = beginStateLoad();
        setLoading(true);
        setError(null);

        try {
            const [stateSuccess, stateResult] = await fetchComplianceState(databaseId, assetId);
            if (!isCurrent()) return;
            if (stateSuccess && typeof stateResult !== "string") {
                setComplianceState(stateResult);
            }

            setTokens({});
            setCurrentPageIndex(1);
            setLoadedPages(1);
            setHasMore(false);
            await loadEvaluationsPage(0, undefined);
        } catch (err: any) {
            if (isCurrent()) {
                setError(err?.message || "Failed to load compliance data");
            }
        } finally {
            if (isCurrent()) {
                setLoading(false);
            }
        }
    }, [beginStateLoad, databaseId, assetId, loadEvaluationsPage]);

    useEffect(() => {
        if (isActive && canViewCompliance && databaseId && assetId) {
            loadComplianceData();
        }
    }, [isActive, canViewCompliance, databaseId, assetId, loadComplianceData]);

    const handlePageChange = async ({ detail }: { detail: { currentPageIndex: number } }) => {
        const newIndex = detail.currentPageIndex;
        setCurrentPageIndex(newIndex);
        setLoading(true);
        try {
            await loadEvaluationsPage(newIndex - 1, tokens[newIndex - 1]);
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

    const handleRelease = async (reason: string) => {
        setReleasing(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await releaseFromQuarantine(databaseId, assetId, reason);
        setReleasing(false);
        setReleaseModalVisible(false);
        if (success) {
            setActionMessage(`The ${Synonyms.asset} was released from quarantine.`);
            await loadComplianceData();
        } else {
            setError(message);
        }
    };

    const handleException = async (reason: string) => {
        setGranting(true);
        const [success, message] = await grantException(databaseId, assetId, reason);
        setGranting(false);
        setExceptionModalVisible(false);
        if (success) {
            setActionMessage(`Exception granted for this ${Synonyms.asset}.`);
            await loadComplianceData();
        } else {
            setError(message);
        }
    };

    const handleRevokeException = async () => {
        setRevoking(true);
        setActionMessage(null);
        setError(null);
        const [success, result] = await revokeException(databaseId, assetId);
        setRevoking(false);
        setRevokeModalVisible(false);
        if (success && typeof result !== "string") {
            setActionMessage(result.message);
            await loadComplianceData();
        } else {
            setError(typeof result === "string" ? result : "Failed to revoke exception");
        }
    };

    if (!routesLoading && !canViewCompliance) {
        return (
            <Box padding="l">
                <Alert type="info">
                    You do not have permission to view compliance information for this{" "}
                    {Synonyms.asset}.
                </Alert>
            </Box>
        );
    }

    const exceptionActive = !!complianceState?.exceptionGranted;
    const lastEvaluated = complianceState?.lastEvaluatedAt || complianceState?.lastEvaluationAt;
    // An archived asset is not evaluated while it is archived; its record keeps the state it had.
    const archived = complianceState?.complianceState === "archived";

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
                                {complianceState?.complianceState === "quarantined" &&
                                    canRelease && (
                                        <Button onClick={() => setReleaseModalVisible(true)}>
                                            Release
                                        </Button>
                                    )}
                                {complianceState?.complianceState === "quarantined" &&
                                    canGrantException && (
                                        <Button onClick={() => setExceptionModalVisible(true)}>
                                            Grant Exception
                                        </Button>
                                    )}
                                {exceptionActive && canRevokeException && !archived && (
                                    <Button onClick={() => setRevokeModalVisible(true)}>
                                        Revoke exception
                                    </Button>
                                )}
                                {canEvaluate && !archived && (
                                    <Button
                                        variant="primary"
                                        loading={evaluating}
                                        onClick={handleEvaluate}
                                    >
                                        Evaluate Now
                                    </Button>
                                )}
                            </SpaceBetween>
                        }
                    >
                        Compliance Status
                    </Header>
                }
            >
                <SpaceBetween size="m">
                    {archived && (
                        <Alert type="info" header={`Archived ${Synonyms.asset}`}>
                            This {Synonyms.asset} is archived, so it is not evaluated and it is left
                            out of the compliance overview of its {Synonyms.database} and the
                            quarantine list. Its compliance record is kept: the state below applies
                            again once the {Synonyms.asset} is unarchived.
                        </Alert>
                    )}
                    <div>
                        <Box variant="awsui-key-label">Current State</Box>
                        <SpaceBetween direction="horizontal" size="xs">
                            <ComplianceStateBadge state={complianceState?.complianceState} />
                            <EvaluationErrorIndicator
                                lastEvaluationStatus={complianceState?.lastEvaluationStatus}
                            />
                        </SpaceBetween>
                        {complianceState?.lastEvaluationStatus === "error" && (
                            <Box variant="small" color="text-status-error">
                                The last evaluation produced no verdict, so the state is the one the{" "}
                                {Synonyms.asset} held before it. The evaluation history shows the
                                cause.
                            </Box>
                        )}
                        {complianceState?.complianceState === "quarantined" && (
                            <ExecutionFailureReasons
                                entries={complianceState.quarantineExecutionFailures}
                            />
                        )}
                    </div>
                    {archived && complianceState?.recordedComplianceState && (
                        <div>
                            <Box variant="awsui-key-label">Recorded State</Box>
                            <ComplianceStateBadge state={complianceState.recordedComplianceState} />
                        </div>
                    )}
                    {complianceState?.schemaName && (
                        <div>
                            <Box variant="awsui-key-label">Assigned Schema</Box>
                            <div>{complianceState.schemaName}</div>
                            {complianceState.schemaSource && (
                                <Box variant="small" color="text-body-secondary">
                                    {schemaSourceLabel(complianceState.schemaSource)}
                                </Box>
                            )}
                        </div>
                    )}
                    {lastEvaluated && (
                        <div>
                            <Box variant="awsui-key-label">Last Evaluated</Box>
                            <div>{formatTimestamp(lastEvaluated)}</div>
                        </div>
                    )}
                    {exceptionActive && (
                        <Container
                            header={
                                <Header
                                    variant="h3"
                                    description={`This ${Synonyms.asset} stays released from quarantine while the exception holds. It ends when it is revoked or when the schema it was granted against changes.`}
                                >
                                    Active exception
                                </Header>
                            }
                        >
                            <ColumnLayout columns={2} variant="text-grid">
                                <div>
                                    <Box variant="awsui-key-label">Reason</Box>
                                    <div>{complianceState?.exceptionReason || "-"}</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Granted by</Box>
                                    <div>{complianceState?.exceptionGrantedBy || "-"}</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Granted at</Box>
                                    <div>
                                        {formatTimestamp(complianceState?.exceptionGrantedAt)}
                                    </div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Schema</Box>
                                    <div>
                                        {complianceState?.exceptionSchemaName
                                            ? complianceState.exceptionSchemaVersion !== undefined
                                                ? `${complianceState.exceptionSchemaName} (version ${complianceState.exceptionSchemaVersion})`
                                                : complianceState.exceptionSchemaName
                                            : "-"}
                                    </div>
                                </div>
                            </ColumnLayout>
                        </Container>
                    )}
                </SpaceBetween>
            </Container>

            <Container
                header={
                    <Header variant="h3" counter={`(${evaluations.length}${hasMore ? "+" : ""})`}>
                        Evaluation History
                    </Header>
                }
            >
                <Table
                    // Columns wrap, so the history fits the tab at 1280 px with the navigation open
                    // instead of scrolling sideways and cutting off the Execution column.
                    wrapLines
                    loading={loading}
                    items={evaluations}
                    pagination={
                        <Pagination
                            currentPageIndex={currentPageIndex}
                            pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                            openEnd={hasMore}
                            onChange={handlePageChange}
                            disabled={loading}
                            ariaLabels={{
                                nextPageLabel: "Next page of evaluations",
                                previousPageLabel: "Previous page of evaluations",
                                pageLabel: (pageNumber) => `Page ${pageNumber} of evaluations`,
                            }}
                        />
                    }
                    empty={
                        <Box textAlign="center" padding="l">
                            No evaluations recorded for this {Synonyms.asset}.
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
                            cell: (item) => {
                                const verdict =
                                    verdictIndicatorMap[item.verdict || item.result] ||
                                    verdictIndicatorMap.error;
                                return (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <StatusLabel type={verdict.type as any}>
                                            {verdict.label}
                                        </StatusLabel>
                                        {item.exceptionApplied && (
                                            <StatusLabel type="info">Exception applied</StatusLabel>
                                        )}
                                        {(item.hasRuleErrors ||
                                            erroredRuleNames(item).length > 0) && (
                                            <StatusLabel type="error">Rule errors</StatusLabel>
                                        )}
                                    </SpaceBetween>
                                );
                            },
                        },
                        {
                            id: "violations",
                            header: "Violations",
                            cell: (item) => {
                                // An errored rule was not evaluated, so it is listed apart from the
                                // rules that failed: its enforcement did not apply to the verdict.
                                // Each violation is a line of its own that wraps between words;
                                // the rule names that were not evaluated follow in error text,
                                // which (unlike a status indicator) does not break mid-word.
                                const errored = erroredRuleNames(item);
                                const violations = item.violations || [];
                                const results = evaluationRuleResults(item);
                                if (
                                    violations.length === 0 &&
                                    errored.length === 0 &&
                                    executionFailureReasons(results).length === 0
                                ) {
                                    return "-";
                                }
                                return (
                                    <SpaceBetween size="xxs">
                                        {violations.map((violation, index) => (
                                            <span key={index}>{violation}</span>
                                        ))}
                                        {violations.length === 0 && <span>-</span>}
                                        <ExecutionFailureReasons entries={results} />
                                        {errored.length > 0 && (
                                            <Box color="text-status-error">
                                                Not evaluated: {errored.join(", ")}
                                            </Box>
                                        )}
                                    </SpaceBetween>
                                );
                            },
                        },
                        {
                            id: "executionId",
                            header: "Execution",
                            cell: (item) => {
                                const executionId = item.executionId;
                                if (!executionId) return "-";
                                if (!canViewExecution) return executionId;
                                return (
                                    <Link
                                        href={`#${executionDetailPath(executionId)}`}
                                        onFollow={(e) => {
                                            e.preventDefault();
                                            navigate(executionDetailPath(executionId));
                                        }}
                                    >
                                        {executionId}
                                    </Link>
                                );
                            },
                        },
                    ]}
                />
            </Container>

            <ReasonModal
                visible={releaseModalVisible}
                header="Release from quarantine"
                label="Reason for release"
                description={`Why this ${Synonyms.asset} leaves quarantine. Recorded in the compliance audit log; the next evaluation may quarantine it again.`}
                confirmLabel={`Release ${Synonyms.asset}`}
                loading={releasing}
                onConfirm={handleRelease}
                onDismiss={() => setReleaseModalVisible(false)}
            />

            <ReasonModal
                visible={exceptionModalVisible}
                header="Grant compliance exception"
                label="Reason for exception"
                description={`Why this ${Synonyms.asset} may stay in use while quarantined. Recorded in the compliance audit log.`}
                confirmLabel="Grant exception"
                loading={granting}
                onConfirm={handleException}
                onDismiss={() => setExceptionModalVisible(false)}
            />

            <RevokeExceptionModal
                visible={revokeModalVisible}
                revoking={revoking}
                onConfirm={handleRevokeException}
                onDismiss={() => setRevokeModalVisible(false)}
            />
        </SpaceBetween>
    );
};

/**
 * The Compliance tab of the asset the route names. Its body is keyed by the database and asset ids,
 * so an in-app navigation to another asset starts it afresh: nothing read for the previous asset
 * (state, history, messages) is shown, and a load still in flight for it is dropped (each load is
 * checked with `useLatestRequest`, which a remount invalidates).
 */
export const ComplianceTab: React.FC<ComplianceTabProps> = (props) => (
    <ComplianceTabForAsset key={JSON.stringify([props.databaseId, props.assetId])} {...props} />
);

export default ComplianceTab;
