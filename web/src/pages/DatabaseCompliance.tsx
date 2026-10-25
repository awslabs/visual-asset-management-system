/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Link from "@cloudscape-design/components/link";
import FormField from "@cloudscape-design/components/form-field";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import Modal from "@cloudscape-design/components/modal";
import Synonyms from "../synonyms";
import {
    fetchDatabaseComplianceOverview,
    sweepSchema,
    getDatabaseBindings,
    fetchComplianceSchemas,
    bindSchemaToDatabase,
    unbindSchemaFromDatabase,
    DatabaseComplianceOverview,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import {
    COMPLIANCE_STATE_INDICATORS,
    ComplianceStateBadge,
    EvaluationErrorIndicator,
} from "../components/compliance/complianceStateBadge";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";

// The routes behind the database binding control (backend/backend/common/apiRoutes.py): the
// schema list fills the selector, PUT binds or changes the schema, DELETE removes it.
const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
const COMPLIANCE_BIND_DATABASE_API_ROUTE = "/compliance/bind/{databaseId}";

const DatabaseCompliancePage: React.FC = () => {
    const { databaseId } = useParams();
    const [overview, setOverview] = useState<DatabaseComplianceOverview | null>(null);
    const [databaseSchema, setDatabaseSchema] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [sweeping, setSweeping] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    // The database's schema binding. The control is offered to a caller who may list schemas and
    // bind or unbind one; asset-level bindings are set in the asset editor.
    const { can: canCallRoute } = useAllowedRoutes();
    const canListSchemas = canCallRoute("GET", COMPLIANCE_SCHEMAS_API_ROUTE);
    const canBind = canListSchemas && canCallRoute("PUT", COMPLIANCE_BIND_DATABASE_API_ROUTE);
    const canUnbind = canListSchemas && canCallRoute("DELETE", COMPLIANCE_BIND_DATABASE_API_ROUTE);
    const [schemaOptions, setSchemaOptions] = useState<SelectProps.Option[]>([]);
    const [loadingSchemas, setLoadingSchemas] = useState(false);
    const [selectedSchema, setSelectedSchema] = useState<SelectProps.Option | null>(null);
    const [bindingInProgress, setBindingInProgress] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState(false);

    // Server-side token paging of the asset-state table: tokens[i] is the startingToken that
    // fetches page i (tokens[0] is undefined). The summary counts cover the whole database on
    // every page; only `assets` is the page.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);

    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            if (!databaseId) return;
            setLoading(true);
            setError(null);
            try {
                const [overviewSuccess, overviewResult] = await fetchDatabaseComplianceOverview(
                    databaseId,
                    { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken }
                );
                if (overviewSuccess && typeof overviewResult !== "string") {
                    setOverview(overviewResult);
                    setLoadedPages((prev) => Math.max(prev, pageNumber + 1));
                    if (overviewResult.nextToken) {
                        setTokens((prev) => ({
                            ...prev,
                            [pageNumber + 1]: overviewResult.nextToken,
                        }));
                        setHasMore(true);
                    } else {
                        setHasMore(false);
                    }
                } else {
                    setError(
                        typeof overviewResult === "string"
                            ? overviewResult
                            : "Failed to load overview"
                    );
                    setHasMore(false);
                }
            } catch (err: any) {
                setError(err?.message || "Failed to load compliance data");
            } finally {
                setLoading(false);
            }
        },
        [databaseId]
    );

    // A reload restarts the walk from the first page and re-reads the binding.
    const loadData = useCallback(async () => {
        if (!databaseId) return;
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        setHasMore(false);
        await loadPage(0, undefined);

        const [bindingSuccess, bindingResult] = await getDatabaseBindings(databaseId);
        if (bindingSuccess && typeof bindingResult !== "string") {
            const bound = bindingResult.databaseSchema || null;
            setDatabaseSchema(bound);
            setSelectedSchema(bound ? { label: bound, value: bound } : null);
        }
    }, [databaseId, loadPage]);

    useEffect(() => {
        if (databaseId) {
            loadData();
        }
    }, [databaseId, loadData]);

    useEffect(() => {
        if (!canBind) return;
        const loadSchemas = async () => {
            setLoadingSchemas(true);
            const [success, result] = await fetchComplianceSchemas();
            if (success && Array.isArray(result)) {
                setSchemaOptions(
                    result.map((s) => ({
                        label: s.schemaName,
                        value: s.schemaName,
                        description: s.description,
                    }))
                );
            } else {
                setError(typeof result === "string" ? result : "Failed to load compliance schemas");
            }
            setLoadingSchemas(false);
        };
        loadSchemas();
    }, [canBind]);

    // Binds the selected schema, or replaces the bound one, then re-reads the binding and the
    // overview.
    const handleBind = async () => {
        const schemaName = selectedSchema?.value;
        if (!databaseId || !schemaName) return;
        setBindingInProgress(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await bindSchemaToDatabase(databaseId, schemaName);
        if (success) {
            setActionMessage(`Compliance schema ${schemaName} bound to this ${Synonyms.database}.`);
            await loadData();
        } else {
            setError(`The compliance schema binding failed: ${message}`);
        }
        setBindingInProgress(false);
    };

    const handleRemove = async () => {
        setConfirmRemove(false);
        if (!databaseId || !databaseSchema) return;
        const removed = databaseSchema;
        setBindingInProgress(true);
        setActionMessage(null);
        setError(null);
        const [success, message] = await unbindSchemaFromDatabase(databaseId);
        if (success) {
            setActionMessage(
                `Compliance schema ${removed} removed from this ${Synonyms.database}.`
            );
            await loadData();
        } else {
            setError(`Removing the compliance schema binding failed: ${message}`);
        }
        setBindingInProgress(false);
    };

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        const newIndex = detail.currentPageIndex;
        setCurrentPageIndex(newIndex);
        loadPage(newIndex - 1, tokens[newIndex - 1]);
    };

    const handleEvaluateAll = async () => {
        if (!databaseSchema) {
            setError(
                `No compliance schema bound to this ${Synonyms.database}. Bind a schema first.`
            );
            return;
        }
        setSweeping(true);
        setActionMessage(null);
        setError(null);

        const [success, result] = await sweepSchema(databaseSchema);
        if (success) {
            const msg =
                typeof result === "object" && result?.message
                    ? result.message
                    : "Evaluation sweep triggered";
            setActionMessage(msg);
            await loadData();
        } else {
            setError(typeof result === "string" ? result : "Sweep failed");
        }
        setSweeping(false);
    };

    const summary = overview?.summary;
    const totalTracked = overview?.totalAssets || 0;
    const compliantCount = summary?.compliant || 0;
    const complianceRate = totalTracked > 0 ? Math.round((compliantCount / totalTracked) * 100) : 0;
    const pageAssets = overview?.assets || [];
    const isPaging = hasMore || currentPageIndex > 1;

    return (
        <SpaceBetween size="l">
            <BreadcrumbGroup
                items={[
                    { text: Synonyms.Databases, href: "#/databases" },
                    { text: databaseId || "", href: `#/databases/${databaseId}/assets/` },
                    { text: "Compliance", href: "" },
                ]}
            />

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

            {(canBind || (canUnbind && databaseSchema)) && (
                <Container
                    header={
                        <Header
                            variant="h2"
                            description={`The compliance schema the ${Synonyms.assets} of this ${Synonyms.database} are evaluated against. Schemas bound to individual ${Synonyms.assets} in the ${Synonyms.asset} editor take precedence.`}
                        >
                            Schema Binding
                        </Header>
                    }
                >
                    <SpaceBetween direction="horizontal" size="xs" alignItems="end">
                        {canBind && (
                            <FormField label="Bound schema">
                                <Select
                                    selectedOption={selectedSchema}
                                    onChange={({ detail }) =>
                                        setSelectedSchema(detail.selectedOption)
                                    }
                                    options={schemaOptions}
                                    placeholder="Select a compliance schema"
                                    loadingText="Loading schemas"
                                    statusType={loadingSchemas ? "loading" : "finished"}
                                    filteringType="auto"
                                    disabled={bindingInProgress}
                                    data-testid="database-compliance-bound-schema"
                                />
                            </FormField>
                        )}
                        {canBind && (
                            <Button
                                onClick={handleBind}
                                loading={bindingInProgress}
                                disabled={
                                    !selectedSchema?.value ||
                                    selectedSchema.value === databaseSchema
                                }
                            >
                                {databaseSchema ? "Change" : "Bind"}
                            </Button>
                        )}
                        {canUnbind && databaseSchema && (
                            <Button
                                onClick={() => setConfirmRemove(true)}
                                disabled={bindingInProgress}
                            >
                                Remove
                            </Button>
                        )}
                    </SpaceBetween>
                </Container>
            )}

            <Modal
                visible={confirmRemove}
                onDismiss={() => setConfirmRemove(false)}
                header="Remove the compliance schema binding"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button variant="link" onClick={() => setConfirmRemove(false)}>
                                Cancel
                            </Button>
                            <Button variant="primary" onClick={handleRemove}>
                                Remove binding
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                Remove {databaseSchema} from the {Synonyms.database} {databaseId}? The compliance
                state of every {Synonyms.asset} that inherits it is deleted; {Synonyms.assets} that
                bind their own schema keep theirs.
            </Modal>

            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button
                                variant="primary"
                                loading={sweeping}
                                onClick={handleEvaluateAll}
                                disabled={!databaseSchema}
                            >
                                Evaluate All {Synonyms.Assets}
                            </Button>
                        }
                        description={
                            databaseSchema
                                ? `Schema: ${databaseSchema}`
                                : "No compliance schema bound"
                        }
                    >
                        {Synonyms.Database} Compliance Overview
                    </Header>
                }
            >
                <ColumnLayout columns={8} minColumnWidth={130} variant="text-grid">
                    <div>
                        <Box variant="awsui-key-label">Tracked {Synonyms.Assets}</Box>
                        <Box variant="awsui-value-large">{totalTracked}</Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Compliant</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator type={COMPLIANCE_STATE_INDICATORS.compliant.type}>
                                {compliantCount}
                            </StatusIndicator>
                        </Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Non-Compliant</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator type={COMPLIANCE_STATE_INDICATORS.non_compliant.type}>
                                {summary?.non_compliant || 0}
                            </StatusIndicator>
                        </Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Quarantined</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator type={COMPLIANCE_STATE_INDICATORS.quarantined.type}>
                                {summary?.quarantined || 0}
                            </StatusIndicator>
                        </Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Exception</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator type={COMPLIANCE_STATE_INDICATORS.exception.type}>
                                {summary?.exception || 0}
                            </StatusIndicator>
                        </Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Pending</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator
                                type={COMPLIANCE_STATE_INDICATORS.pending_evaluation.type}
                            >
                                {summary?.pending_evaluation || 0}
                            </StatusIndicator>
                        </Box>
                    </div>
                    {/* An overlay on the state buckets: these assets keep the state shown in their
                        row, and their last evaluation produced no verdict. */}
                    <div>
                        <Box variant="awsui-key-label">Evaluation errors</Box>
                        <Box variant="awsui-value-large">
                            <StatusIndicator type="error">{summary?.error || 0}</StatusIndicator>
                        </Box>
                    </div>
                    <div>
                        <Box variant="awsui-key-label">Compliance Rate</Box>
                        <Box variant="awsui-value-large">{complianceRate}%</Box>
                    </div>
                </ColumnLayout>
            </Container>

            <Container
                header={
                    <Header
                        variant="h3"
                        counter={`(${pageAssets.length} of ${totalTracked})`}
                        description={
                            isPaging
                                ? `Showing ${pageAssets.length} of ${totalTracked} tracked ${Synonyms.assets}; the summary above covers all of them.`
                                : undefined
                        }
                    >
                        {Synonyms.Asset} Compliance States
                    </Header>
                }
            >
                <Table
                    loading={loading}
                    items={pageAssets}
                    pagination={
                        <Pagination
                            currentPageIndex={currentPageIndex}
                            pagesCount={hasMore ? loadedPages + 1 : loadedPages}
                            openEnd={hasMore}
                            onChange={handlePageChange}
                            disabled={loading}
                            ariaLabels={{
                                nextPageLabel: `Next page of ${Synonyms.assets}`,
                                previousPageLabel: `Previous page of ${Synonyms.assets}`,
                                pageLabel: (pageNumber) =>
                                    `Page ${pageNumber} of ${Synonyms.assets}`,
                            }}
                        />
                    }
                    empty={
                        <Box textAlign="center" padding="l">
                            No {Synonyms.assets} are being tracked for compliance in this{" "}
                            {Synonyms.database}.
                        </Box>
                    }
                    columnDefinitions={[
                        {
                            id: "assetName",
                            header: `${Synonyms.Asset} Name`,
                            cell: (item: any) => (
                                <Link href={`#/databases/${databaseId}/assets/${item.assetId}`}>
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
                            id: "complianceState",
                            header: "State",
                            cell: (item: any) => (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <ComplianceStateBadge state={item.complianceState} />
                                    <EvaluationErrorIndicator
                                        lastEvaluationStatus={item.lastEvaluationStatus}
                                    />
                                </SpaceBetween>
                            ),
                            sortingField: "complianceState",
                        },
                        {
                            id: "schemaName",
                            header: "Bound schema",
                            cell: (item: any) => item.schemaName || "-",
                            sortingField: "schemaName",
                        },
                        {
                            id: "schemaSource",
                            header: "Source",
                            cell: (item: any) => item.schemaSource || "-",
                            sortingField: "schemaSource",
                        },
                        {
                            id: "updatedAt",
                            header: "Last Updated",
                            cell: (item: any) =>
                                item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "-",
                            sortingField: "updatedAt",
                        },
                    ]}
                    sortingDisabled={false}
                />
            </Container>
        </SpaceBetween>
    );
};

export default DatabaseCompliancePage;
