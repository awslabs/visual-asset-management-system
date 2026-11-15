/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import {
    fetchEvaluationHistory,
    erroredRuleNames,
    evaluationClosedMessage,
    evaluationNotAppliedMessage,
    waitingRuleNames,
    EVALUATION_NOT_APPLIED_LABEL,
    EVALUATION_NOT_COMPLETED_LABEL,
    PIPELINE_RULE_WAITING_MESSAGE,
    ComplianceRuleResult,
    EvaluationRecord,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../../services/ComplianceService";
import Synonyms from "../../synonyms";
import { StatusLabel } from "./complianceStateBadge";
import BreakableIdentifier from "./BreakableIdentifier";
import ExecutionFailureReasons from "./ExecutionFailureReasons";
import { useLatestRequest } from "./listingFilters";
import { ListingLoadFailed, loadFailureMessage } from "./listingStates";
import { PAGE_SORTING_NOTE, byDate, byText, useSortedItems } from "../list/tableSorting";

const VERDICTS: Record<string, { type: string; label: string }> = {
    compliant: { type: "success", label: "Compliant" },
    non_compliant: { type: "warning", label: "Non-Compliant" },
    quarantined: { type: "error", label: "Quarantined" },
    pending_pipeline: { type: "in-progress", label: "Pipeline Running" },
    error: { type: "error", label: "Error" },
};

/** The rule results of an evaluation; the history listing carries them JSON-encoded. */
export const evaluationRuleResults = (evaluation: EvaluationRecord): ComplianceRuleResult[] => {
    let results = evaluation.ruleResults;
    if (typeof results === "string") {
        try {
            results = JSON.parse(results);
        } catch {
            return [];
        }
    }
    return Array.isArray(results) ? results : [];
};

/** "name (version N)", or the name alone for an evaluation that records no version. */
export const schemaWithVersion = (evaluation: EvaluationRecord): string =>
    evaluation.schemaVersion !== undefined && evaluation.schemaVersion !== null
        ? `${evaluation.schemaName} (version ${evaluation.schemaVersion})`
        : evaluation.schemaName || "-";

/**
 * The result label an evaluation row leads with, which its Result column sorts by: the verdict,
 * or "not completed" for an evaluation closed without one.
 */
export const evaluationResultLabel = (evaluation: EvaluationRecord): string =>
    evaluationClosedMessage(evaluation)
        ? EVALUATION_NOT_COMPLETED_LABEL
        : (VERDICTS[evaluation.verdict || evaluation.result] || VERDICTS.error).label;

/**
 * One rule's result: the rule name, its outcome as a one-line status label, and the rule's message
 * beneath, which wraps between words. The whole line used to be one status indicator, whose text
 * breaks between any two letters, so a long message could not wrap without splitting words.
 */
const RuleResultLine: React.FC<{ rule: ComplianceRuleResult }> = ({ rule }) => {
    const errored = rule.status === "error";
    const type = errored ? "error" : rule.passed ? "success" : "warning";
    const label = errored ? "Error (not evaluated)" : rule.passed ? "Passed" : "Failed";
    return (
        <div data-rule-result="">
            <Box variant="span" fontWeight="bold">
                <BreakableIdentifier text={rule.ruleName} />
            </Box>{" "}
            <StatusLabel type={type as any}>{label}</StatusLabel>
            {rule.message && (
                <Box variant="small" display="block" color="text-body-secondary">
                    {rule.message}
                </Box>
            )}
            <ExecutionFailureReasons entries={[rule]} showRuleName={false} />
        </div>
    );
};

/**
 * A pipeline rule of a pending evaluation that waits for a running execution of its workflow. The
 * execution is not named: it may be another user's run, which the reader may not open.
 */
const WaitingRuleLine: React.FC<{ ruleName: string }> = ({ ruleName }) => (
    <div data-rule-waiting="">
        <Box variant="span" fontWeight="bold">
            <BreakableIdentifier text={ruleName} />
        </Box>{" "}
        <StatusLabel type="pending">Waiting</StatusLabel>
        <Box variant="small" display="block" color="text-body-secondary">
            {PIPELINE_RULE_WAITING_MESSAGE}
        </Box>
    </div>
);

export interface EvaluationHistoryModalProps {
    visible: boolean;
    databaseId: string;
    assetId: string;
    /** How the asset is named in the title: its name, else its id. */
    assetLabel: string;
    onDismiss: () => void;
}

/**
 * The evaluation history of one asset (GET /compliance/evaluations/{databaseId}/{assetId}), newest
 * first and paged: verdict, schema and version, and each rule's result including rules whose
 * tooling failed (`error`, not evaluated). Opened from the database compliance overview, so a role
 * that cannot open asset pages still sees the history.
 */
const EvaluationHistoryModal: React.FC<EvaluationHistoryModalProps> = ({
    visible,
    databaseId,
    assetId,
    assetLabel,
    onDismiss,
}) => {
    const [evaluations, setEvaluations] = useState<EvaluationRecord[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // tokens[i] is the startingToken that fetches page i (tokens[0] is undefined).
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    // A load is dropped when a later one has begun (another asset, another page), so the history
    // shown is always the latest request's.
    const beginLoad = useLatestRequest();
    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            const isCurrent = beginLoad();
            setLoading(true);
            setError(null);
            const [success, result] = await fetchEvaluationHistory(databaseId, assetId, {
                maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
                startingToken,
            });
            if (!isCurrent()) return;
            if (success && typeof result !== "string") {
                setEvaluations(result.evaluations);
                setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                if (result.nextToken) {
                    setTokens((prev) => ({ ...prev, [pageNumber + 1]: result.nextToken }));
                    setHasMore(true);
                } else {
                    setHasMore(false);
                }
            } else {
                setEvaluations([]);
                setHasMore(false);
                setError(loadFailureMessage("the evaluation history", result));
            }
            setLoading(false);
        },
        [beginLoad, databaseId, assetId]
    );

    useEffect(() => {
        if (!visible) return;
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        loadPage(0, undefined);
    }, [visible, loadPage]);

    // The evaluations of the page shown, in the order of the column header chosen.
    const { items: sortedEvaluations, sortingProps } = useSortedItems(evaluations);
    const isPaging = hasMore || currentPageIndex > 1;

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            size="max"
            header={`Evaluation history: ${assetLabel}`}
            closeAriaLabel="Close dialog"
            footer={
                <Box float="right">
                    <Button onClick={onDismiss}>Close</Button>
                </Box>
            }
        >
            <SpaceBetween size="m">
                {error && <Alert type="error">{error}</Alert>}
                {isPaging && (
                    <Box variant="small" color="text-body-secondary">
                        {PAGE_SORTING_NOTE}
                    </Box>
                )}
                <Table
                    // Columns wrap, so long rule messages stay within the dialog.
                    wrapLines
                    loading={loading}
                    items={sortedEvaluations}
                    {...sortingProps}
                    variant="embedded"
                    pagination={
                        <Pagination
                            currentPageIndex={currentPageIndex}
                            pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                            openEnd={hasMore}
                            disabled={loading}
                            onChange={({ detail }) => {
                                setCurrentPageIndex(detail.currentPageIndex);
                                loadPage(
                                    detail.currentPageIndex - 1,
                                    tokens[detail.currentPageIndex - 1]
                                );
                            }}
                            ariaLabels={{
                                nextPageLabel: "Next page of evaluations",
                                previousPageLabel: "Previous page of evaluations",
                                pageLabel: (pageNumber) => `Page ${pageNumber} of evaluations`,
                            }}
                        />
                    }
                    empty={
                        error ? (
                            <ListingLoadFailed
                                what="The evaluation history"
                                onRetry={() =>
                                    loadPage(currentPageIndex - 1, tokens[currentPageIndex - 1])
                                }
                            />
                        ) : (
                            <Box textAlign="center" padding="l">
                                No evaluations recorded for this {Synonyms.asset}.
                            </Box>
                        )
                    }
                    columnDefinitions={[
                        {
                            id: "evaluatedAt",
                            header: "Date",
                            cell: (item) =>
                                item.evaluatedAt
                                    ? new Date(item.evaluatedAt).toLocaleString()
                                    : "-",
                            sortingField: "evaluatedAt",
                            sortingComparator: byDate((item) => item.evaluatedAt),
                        },
                        {
                            id: "schema",
                            header: "Schema",
                            cell: (item) => schemaWithVersion(item),
                            sortingField: "schemaName",
                            sortingComparator: byText(schemaWithVersion),
                        },
                        {
                            id: "verdict",
                            header: "Result",
                            sortingField: "result",
                            sortingComparator: byText(evaluationResultLabel),
                            cell: (item) => {
                                // Closed without a verdict: the reason replaces the verdict.
                                const closed = evaluationClosedMessage(item);
                                if (closed) {
                                    return (
                                        <SpaceBetween size="xxs">
                                            <StatusLabel type="stopped">
                                                {EVALUATION_NOT_COMPLETED_LABEL}
                                            </StatusLabel>
                                            <Box variant="small" color="text-body-secondary">
                                                {closed}
                                            </Box>
                                        </SpaceBetween>
                                    );
                                }
                                const verdict =
                                    VERDICTS[item.verdict || item.result] || VERDICTS.error;
                                return (
                                    <SpaceBetween size="xxs">
                                        <StatusLabel type={verdict.type as any}>
                                            {verdict.label}
                                        </StatusLabel>
                                        {item.exceptionApplied && (
                                            <StatusLabel type="info">Exception applied</StatusLabel>
                                        )}
                                        {evaluationNotAppliedMessage(item) && (
                                            <StatusLabel type="stopped">
                                                {EVALUATION_NOT_APPLIED_LABEL}
                                            </StatusLabel>
                                        )}
                                        {evaluationNotAppliedMessage(item) && (
                                            <Box variant="small" color="text-body-secondary">
                                                {evaluationNotAppliedMessage(item)}
                                            </Box>
                                        )}
                                        {(item.hasRuleErrors ||
                                            erroredRuleNames(item).length > 0) && (
                                            <StatusLabel type="error">Rule errors</StatusLabel>
                                        )}
                                        {waitingRuleNames(item).length > 0 && (
                                            <StatusLabel type="pending">Waiting</StatusLabel>
                                        )}
                                    </SpaceBetween>
                                );
                            },
                        },
                        {
                            id: "rules",
                            header: "Rule results",
                            cell: (item) => {
                                const rules = evaluationRuleResults(item);
                                const waiting = waitingRuleNames(item);
                                if (rules.length === 0 && waiting.length === 0) return "-";
                                return (
                                    <SpaceBetween size="xxs">
                                        {rules.map((rule) => (
                                            <RuleResultLine key={rule.ruleName} rule={rule} />
                                        ))}
                                        {waiting.map((ruleName) => (
                                            <WaitingRuleLine key={ruleName} ruleName={ruleName} />
                                        ))}
                                    </SpaceBetween>
                                );
                            },
                        },
                    ]}
                />
            </SpaceBetween>
        </Modal>
    );
};

export default EvaluationHistoryModal;
