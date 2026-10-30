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
    ComplianceRuleResult,
    EvaluationRecord,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../../services/ComplianceService";
import Synonyms from "../../synonyms";
import { StatusLabel } from "./complianceStateBadge";
import BreakableIdentifier from "./BreakableIdentifier";
import ExecutionFailureReasons from "./ExecutionFailureReasons";
import { useLatestRequest } from "./listingFilters";

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
                setError(typeof result === "string" ? result : "Failed to load the history");
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
                <Table
                    // Columns wrap, so long rule messages stay within the dialog.
                    wrapLines
                    loading={loading}
                    items={evaluations}
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
                        <Box textAlign="center" padding="l">
                            No evaluations recorded for this {Synonyms.asset}.
                        </Box>
                    }
                    columnDefinitions={[
                        {
                            id: "evaluatedAt",
                            header: "Date",
                            cell: (item) =>
                                item.evaluatedAt
                                    ? new Date(item.evaluatedAt).toLocaleString()
                                    : "-",
                        },
                        {
                            id: "schema",
                            header: "Schema",
                            cell: (item) => schemaWithVersion(item),
                        },
                        {
                            id: "verdict",
                            header: "Result",
                            cell: (item) => {
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
                                        {(item.hasRuleErrors ||
                                            erroredRuleNames(item).length > 0) && (
                                            <StatusLabel type="error">Rule errors</StatusLabel>
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
                                if (rules.length === 0) return "-";
                                return (
                                    <SpaceBetween size="xxs">
                                        {rules.map((rule) => (
                                            <RuleResultLine key={rule.ruleName} rule={rule} />
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
