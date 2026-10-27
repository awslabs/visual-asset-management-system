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
    evaluateDatabaseCompliance,
    getDatabaseBindings,
    fetchComplianceSchemas,
    bindSchemaToDatabase,
    unbindSchemaFromDatabase,
    revokeException,
    DatabaseComplianceOverview,
    EvaluateDatabaseResponse,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import {
    COMPLIANCE_STATE_INDICATORS,
    ComplianceStateBadge,
    EvaluationErrorIndicator,
} from "../components/compliance/complianceStateBadge";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";
import EvaluationHistoryModal from "../components/compliance/EvaluationHistoryModal";
import { schemaSourceLabel } from "../components/compliance/complianceStateBadge";
import RevokeExceptionModal from "../components/compliance/RevokeExceptionModal";
import { ROW_ACTIONS_STICKY_COLUMNS, VAMS_PAGE_PADDING } from "../components/compliance/rowActions";
import { useAssetPageAccess } from "../hooks/useAssetPageAccess";
import { TextFilterInput, useUrlFilters } from "../components/compliance/listingFilters";

/** The overview's server-side filters and sort, kept in the URL. */
export const OVERVIEW_FILTER_KEYS = [
    "state",
    "schemaName",
    "schemaSource",
    "evaluationError",
    "text",
    "sort",
] as const;

export const OVERVIEW_STATE_OPTIONS: SelectProps.Option[] = [
    { value: "", label: "All states" },
    { value: "compliant", label: "Compliant" },
    { value: "non_compliant", label: "Non-Compliant" },
    { value: "quarantined", label: "Quarantined" },
    { value: "exception", label: "Exception" },
    { value: "pending_evaluation", label: "Pending Evaluation" },
    { value: "unknown", label: "Unknown" },
];

export const OVERVIEW_SOURCE_OPTIONS: SelectProps.Option[] = [
    { value: "", label: "Any binding" },
    { value: "database", label: `${Synonyms.Database} binding` },
    { value: "asset", label: `${Synonyms.Asset} override` },
];

export const OVERVIEW_ERROR_OPTIONS: SelectProps.Option[] = [
    { value: "", label: "Any last evaluation" },
    { value: "true", label: "Last evaluation errored" },
    { value: "false", label: "Last evaluation without error" },
];

export const OVERVIEW_SORT_OPTIONS: SelectProps.Option[] = [
    { value: "", label: `Sort by ${Synonyms.asset} ID` },
    { value: "name", label: `Sort by ${Synonyms.asset} name` },
];

const optionFor = (options: SelectProps.Option[], value: string): SelectProps.Option =>
    options.find((option) => option.value === value) || options[0];

// The routes behind the database binding control (backend/backend/common/apiRoutes.py): the
// schema list fills the selector, PUT binds or changes the schema, DELETE removes it.
const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
const COMPLIANCE_BIND_DATABASE_API_ROUTE = "/compliance/bind/{databaseId}";
// Evaluate All (the database's tracked assets); offered only to a caller who may call it.
export const COMPLIANCE_EVALUATE_DATABASE_API_ROUTE = "/compliance/evaluate/{databaseId}";
// Per-asset history and the revocation of an asset's exception, offered on the asset rows so a
// role that cannot open asset pages (both compliance templates) still reaches them.
export const COMPLIANCE_EVALUATIONS_API_ROUTE = "/compliance/evaluations/{databaseId}/{assetId}";
export const COMPLIANCE_EXCEPTION_API_ROUTE =
    "/compliance/quarantine/{databaseId}/{assetId}/exception";

/**
 * The success message of Evaluate All: the API's count, plus how many tracked assets were not
 * reached (the request is bounded like a sweep) and how many were registered under the binding.
 */
export const evaluateAllMessage = (result: EvaluateDatabaseResponse): string => {
    const parts = [result.message || `Evaluation triggered for ${result.assetsTriggered.length}`];
    if (result.registered) {
        parts.push(`${result.registered} newly registered under the ${Synonyms.database} schema`);
    }
    if (result.assetsRemaining) {
        parts.push(`${result.assetsRemaining} not reached yet; choose Evaluate All again`);
    }
    return parts.join("; ") + ".";
};

/**
 * Width (px) of the asset rows' Actions column: the History and Revoke exception inline links side
 * by side (about 180 px of labels and gap) with the cell padding. See ROW_ACTIONS_STICKY_COLUMNS.
 */
export const ASSET_ROW_ACTIONS_COLUMN_WIDTH = 240;

/** The state tiles of the summary, in display order; each one filters the table to its state. */
const SUMMARY_TILES: {
    state: keyof typeof COMPLIANCE_STATE_INDICATORS;
    label: string;
}[] = [
    { state: "compliant", label: "Compliant" },
    { state: "non_compliant", label: "Non-Compliant" },
    { state: "quarantined", label: "Quarantined" },
    { state: "exception", label: "Exception" },
    { state: "pending_evaluation", label: "Pending Evaluation" },
    { state: "unknown", label: "Unknown" },
];

const DatabaseCompliancePage: React.FC = () => {
    const { databaseId } = useParams();
    const [overview, setOverview] = useState<DatabaseComplianceOverview | null>(null);
    const [databaseSchema, setDatabaseSchema] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [sweeping, setSweeping] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Whether the last overview read failed: the asset table then says nothing was loaded rather
    // than that the database tracks no assets.
    const [loadFailed, setLoadFailed] = useState(false);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    // The database's schema binding. The control is offered to a caller who may list schemas and
    // bind or unbind one; asset-level bindings are set in the asset editor.
    const { can: canCallRoute } = useAllowedRoutes();
    const canListSchemas = canCallRoute("GET", COMPLIANCE_SCHEMAS_API_ROUTE);
    const canBind = canListSchemas && canCallRoute("PUT", COMPLIANCE_BIND_DATABASE_API_ROUTE);
    const canUnbind = canListSchemas && canCallRoute("DELETE", COMPLIANCE_BIND_DATABASE_API_ROUTE);
    const canEvaluateAll = canCallRoute("POST", COMPLIANCE_EVALUATE_DATABASE_API_ROUTE);
    const canViewHistory = canCallRoute("GET", COMPLIANCE_EVALUATIONS_API_ROUTE);
    const canRevokeException = canCallRoute("DELETE", COMPLIANCE_EXCEPTION_API_ROUTE);
    const [historyTarget, setHistoryTarget] = useState<any | null>(null);
    const [revokeTarget, setRevokeTarget] = useState<any | null>(null);
    const [revoking, setRevoking] = useState(false);
    // The breadcrumb and asset names link to the assets and asset pages only when the caller may
    // open them.
    const { canOpenDatabaseAssets, canOpenAsset } = useAssetPageAccess();
    const [schemaOptions, setSchemaOptions] = useState<SelectProps.Option[]>([]);
    const [loadingSchemas, setLoadingSchemas] = useState(false);
    const [selectedSchema, setSelectedSchema] = useState<SelectProps.Option | null>(null);
    const [bindingInProgress, setBindingInProgress] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [filters, setFilters] = useUrlFilters(OVERVIEW_FILTER_KEYS);
    const filtersActive = OVERVIEW_FILTER_KEYS.some((key) => key !== "sort" && !!filters[key]);

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
            setLoadFailed(false);
            try {
                const [overviewSuccess, overviewResult] = await fetchDatabaseComplianceOverview(
                    databaseId,
                    { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken },
                    {
                        state: filters.state,
                        schemaName: filters.schemaName,
                        schemaSource: filters.schemaSource,
                        evaluationError: filters.evaluationError,
                        text: filters.text,
                        sort: filters.sort,
                    }
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
                    setLoadFailed(true);
                    setError(
                        typeof overviewResult === "string"
                            ? overviewResult
                            : "Failed to load overview"
                    );
                    setHasMore(false);
                }
            } catch (err: any) {
                setLoadFailed(true);
                setError(err?.message || "Failed to load compliance data");
            } finally {
                setLoading(false);
            }
        },
        [
            databaseId,
            filters.state,
            filters.schemaName,
            filters.schemaSource,
            filters.evaluationError,
            filters.text,
            filters.sort,
        ]
    );

    // A filter change restarts the walk from the first page: a token is issued for its filters.
    const restartWalk = useCallback(async () => {
        if (!databaseId) return;
        setTokens({});
        setCurrentPageIndex(1);
        setLoadedPages(1);
        setHasMore(false);
        await loadPage(0, undefined);
    }, [databaseId, loadPage]);

    const loadBinding = useCallback(async () => {
        if (!databaseId) return;
        const [bindingSuccess, bindingResult] = await getDatabaseBindings(databaseId);
        if (bindingSuccess && typeof bindingResult !== "string") {
            const bound = bindingResult.databaseSchema || null;
            setDatabaseSchema(bound);
            setSelectedSchema(bound ? { label: bound, value: bound } : null);
        }
    }, [databaseId]);

    // A reload restarts the walk from the first page and re-reads the binding.
    const loadData = useCallback(async () => {
        await restartWalk();
        await loadBinding();
    }, [restartWalk, loadBinding]);

    useEffect(() => {
        if (databaseId) {
            restartWalk();
        }
    }, [databaseId, restartWalk]);

    useEffect(() => {
        if (databaseId) {
            loadBinding();
        }
    }, [databaseId, loadBinding]);

    useEffect(() => {
        if (!canBind) return;
        const loadSchemas = async () => {
            setLoadingSchemas(true);
            const [success, result] = await fetchComplianceSchemas(databaseId);
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

    // Evaluates the tracked assets of this database only, each against its own bound schema (the
    // database binding or the asset's override); a schema-wide sweep would also evaluate the other
    // databases bound to the same schema and skip this database's overrides.
    const handleEvaluateAll = async () => {
        if (!databaseId) return;
        setSweeping(true);
        setActionMessage(null);
        setError(null);

        const [success, result] = await evaluateDatabaseCompliance(databaseId);
        if (success && typeof result !== "string") {
            setActionMessage(evaluateAllMessage(result));
            await loadData();
        } else {
            setError(typeof result === "string" ? result : "Evaluation failed");
        }
        setSweeping(false);
    };

    const handleRevokeException = async () => {
        if (!databaseId || !revokeTarget) return;
        setRevoking(true);
        setActionMessage(null);
        setError(null);
        const [success, result] = await revokeException(databaseId, revokeTarget.assetId);
        setRevoking(false);
        setRevokeTarget(null);
        if (success && typeof result !== "string") {
            setActionMessage(
                `Exception revoked for ${revokeTarget.assetName || revokeTarget.assetId}.`
            );
            await loadData();
        } else {
            setError(typeof result === "string" ? result : "Failed to revoke exception");
        }
    };

    const summary = overview?.summary;
    const totalTracked = overview?.totalAssets || 0;
    const compliantCount = summary?.compliant || 0;
    const complianceRate = totalTracked > 0 ? Math.round((compliantCount / totalTracked) * 100) : 0;
    const pageAssets = overview?.assets || [];
    const isPaging = hasMore || currentPageIndex > 1;

    return (
        // The page margins every VAMS list page uses (VAMS_PAGE_PADDING).
        <Box padding={VAMS_PAGE_PADDING}>
            <SpaceBetween size="l">
                <BreadcrumbGroup
                    items={
                        canOpenDatabaseAssets
                            ? [
                                  { text: Synonyms.Databases, href: "#/databases" },
                                  {
                                      text: databaseId || "",
                                      href: `#/databases/${databaseId}/assets/`,
                                  },
                                  { text: "Compliance", href: "" },
                              ]
                            : [
                                  { text: Synonyms.Databases, href: "#/databases" },
                                  { text: `${databaseId || ""} Compliance`, href: "" },
                              ]
                    }
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
                                description={`The compliance schema the ${Synonyms.assets} of this ${Synonyms.database} are evaluated against. Schemas bound to individual ${Synonyms.assets} in the ${Synonyms.asset} editor take precedence. Offered: the GLOBAL schemas and those available to this ${Synonyms.database} only.`}
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
                    Remove {databaseSchema} from the {Synonyms.database} {databaseId}? The
                    compliance state of every {Synonyms.asset} that inherits it is deleted;{" "}
                    {Synonyms.assets} that bind their own schema keep theirs.
                </Modal>

                <Container
                    header={
                        <Header
                            variant="h2"
                            actions={
                                canEvaluateAll ? (
                                    <Button
                                        variant="primary"
                                        loading={sweeping}
                                        onClick={handleEvaluateAll}
                                        disabled={!databaseSchema && totalTracked === 0}
                                    >
                                        Evaluate All {Synonyms.Assets}
                                    </Button>
                                ) : undefined
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
                    <ColumnLayout columns={9} minColumnWidth={130} variant="text-grid">
                        {/* Each tile lists its assets: a state tile sets the state filter, the
                            errors tile the evaluation-error filter, Tracked clears both. */}
                        <div>
                            <Box variant="awsui-key-label">Tracked {Synonyms.Assets}</Box>
                            <Box variant="awsui-value-large">
                                <Button
                                    variant="inline-link"
                                    ariaLabel={`Show every tracked ${Synonyms.asset}`}
                                    onClick={() => setFilters({ state: "", evaluationError: "" })}
                                >
                                    {totalTracked}
                                </Button>
                            </Box>
                        </div>
                        {SUMMARY_TILES.map((tile) => (
                            <div key={tile.state}>
                                <Box variant="awsui-key-label">{tile.label}</Box>
                                <Box variant="awsui-value-large">
                                    <Button
                                        variant="inline-link"
                                        ariaLabel={`Show only ${tile.label.toLowerCase()} ${
                                            Synonyms.assets
                                        }`}
                                        onClick={() =>
                                            setFilters({
                                                state:
                                                    filters.state === tile.state ? "" : tile.state,
                                            })
                                        }
                                    >
                                        <StatusIndicator
                                            type={COMPLIANCE_STATE_INDICATORS[tile.state].type}
                                        >
                                            {(summary as any)?.[tile.state] || 0}
                                            {filters.state === tile.state ? " (shown)" : ""}
                                        </StatusIndicator>
                                    </Button>
                                </Box>
                            </div>
                        ))}
                        {/* An overlay on the state buckets: these assets keep the state shown in their
                            row, and their last evaluation produced no verdict. */}
                        <div>
                            <Box variant="awsui-key-label">Evaluation errors</Box>
                            <Box variant="awsui-value-large">
                                <Button
                                    variant="inline-link"
                                    ariaLabel={`Show only ${Synonyms.assets} whose last evaluation errored`}
                                    onClick={() =>
                                        setFilters({
                                            evaluationError:
                                                filters.evaluationError === "true" ? "" : "true",
                                        })
                                    }
                                >
                                    <StatusIndicator type="error">
                                        {summary?.error || 0}
                                        {filters.evaluationError === "true" ? " (shown)" : ""}
                                    </StatusIndicator>
                                </Button>
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
                            counter={
                                filtersActive
                                    ? `(${pageAssets.length}${hasMore ? "+" : ""})`
                                    : `(${pageAssets.length} of ${totalTracked})`
                            }
                            description={
                                filtersActive
                                    ? `${Synonyms.Assets} matching the filters; the summary above covers all ${totalTracked} tracked ${Synonyms.assets}.`
                                    : isPaging
                                    ? `Showing ${pageAssets.length} of ${totalTracked} tracked ${Synonyms.assets}; the summary above covers all of them.`
                                    : undefined
                            }
                        >
                            {Synonyms.Asset} Compliance States
                        </Header>
                    }
                >
                    <Table
                        wrapLines
                        stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
                        filter={
                            <SpaceBetween direction="horizontal" size="xs">
                                <TextFilterInput
                                    value={filters.text}
                                    onCommit={(text) => setFilters({ text })}
                                    placeholder={`Search ${Synonyms.asset} name or ID`}
                                    ariaLabel={`Search ${Synonyms.assets}`}
                                />
                                <Select
                                    selectedOption={optionFor(
                                        OVERVIEW_STATE_OPTIONS,
                                        filters.state
                                    )}
                                    options={OVERVIEW_STATE_OPTIONS}
                                    ariaLabel="Filter by compliance state"
                                    onChange={({ detail }) =>
                                        setFilters({ state: detail.selectedOption.value || "" })
                                    }
                                />
                                <Select
                                    selectedOption={{
                                        value: filters.schemaName,
                                        label: filters.schemaName || "Any schema",
                                    }}
                                    options={[
                                        { value: "", label: "Any schema" },
                                        ...Array.from(
                                            new Set(
                                                [
                                                    ...schemaOptions.map((o) => o.value || ""),
                                                    databaseSchema || "",
                                                    filters.schemaName,
                                                ].filter(Boolean)
                                            )
                                        )
                                            .sort()
                                            .map((name) => ({ value: name, label: name })),
                                    ]}
                                    ariaLabel="Filter by bound schema"
                                    onChange={({ detail }) =>
                                        setFilters({
                                            schemaName: detail.selectedOption.value || "",
                                        })
                                    }
                                />
                                <Select
                                    selectedOption={optionFor(
                                        OVERVIEW_SOURCE_OPTIONS,
                                        filters.schemaSource
                                    )}
                                    options={OVERVIEW_SOURCE_OPTIONS}
                                    ariaLabel="Filter by binding source"
                                    onChange={({ detail }) =>
                                        setFilters({
                                            schemaSource: detail.selectedOption.value || "",
                                        })
                                    }
                                />
                                <Select
                                    selectedOption={optionFor(
                                        OVERVIEW_ERROR_OPTIONS,
                                        filters.evaluationError
                                    )}
                                    options={OVERVIEW_ERROR_OPTIONS}
                                    ariaLabel="Filter by last evaluation"
                                    onChange={({ detail }) =>
                                        setFilters({
                                            evaluationError: detail.selectedOption.value || "",
                                        })
                                    }
                                />
                                <Select
                                    selectedOption={optionFor(OVERVIEW_SORT_OPTIONS, filters.sort)}
                                    options={OVERVIEW_SORT_OPTIONS}
                                    ariaLabel={`Sort ${Synonyms.assets}`}
                                    onChange={({ detail }) =>
                                        setFilters({ sort: detail.selectedOption.value || "" })
                                    }
                                />
                            </SpaceBetween>
                        }
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
                                {loadFailed
                                    ? `The ${Synonyms.asset} compliance states could not be loaded.`
                                    : filtersActive
                                    ? `No ${Synonyms.assets} match the filters.`
                                    : `No ${Synonyms.assets} are being tracked for compliance in this ${Synonyms.database}.`}
                            </Box>
                        }
                        columnDefinitions={[
                            {
                                id: "assetName",
                                header: `${Synonyms.Asset} Name`,
                                cell: (item: any) =>
                                    canOpenAsset ? (
                                        <Link
                                            href={`#/databases/${databaseId}/assets/${item.assetId}`}
                                        >
                                            {item.assetName || item.assetId}
                                        </Link>
                                    ) : (
                                        item.assetName || item.assetId
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
                                cell: (item: any) => schemaSourceLabel(item.schemaSource),
                                sortingField: "schemaSource",
                            },
                            {
                                id: "updatedAt",
                                header: "Last Updated",
                                cell: (item: any) =>
                                    item.updatedAt
                                        ? new Date(item.updatedAt).toLocaleString()
                                        : "-",
                                sortingField: "updatedAt",
                            },
                            ...(canViewHistory || canRevokeException
                                ? [
                                      {
                                          id: "actions",
                                          header: "Actions",
                                          width: ASSET_ROW_ACTIONS_COLUMN_WIDTH,
                                          minWidth: ASSET_ROW_ACTIONS_COLUMN_WIDTH,
                                          cell: (item: any) => (
                                              <SpaceBetween direction="horizontal" size="s">
                                                  {canViewHistory && (
                                                      <Button
                                                          variant="inline-link"
                                                          ariaLabel={`Evaluation history of ${
                                                              item.assetName || item.assetId
                                                          }`}
                                                          onClick={() => setHistoryTarget(item)}
                                                      >
                                                          History
                                                      </Button>
                                                  )}
                                                  {canRevokeException &&
                                                      item.complianceState === "exception" && (
                                                          <Button
                                                              variant="inline-link"
                                                              ariaLabel={`Revoke the exception of ${
                                                                  item.assetName || item.assetId
                                                              }`}
                                                              onClick={() => setRevokeTarget(item)}
                                                          >
                                                              Revoke exception
                                                          </Button>
                                                      )}
                                              </SpaceBetween>
                                          ),
                                      },
                                  ]
                                : []),
                        ]}
                        sortingDisabled={false}
                    />
                </Container>
                {historyTarget && databaseId && (
                    <EvaluationHistoryModal
                        visible={!!historyTarget}
                        databaseId={databaseId}
                        assetId={historyTarget.assetId}
                        assetLabel={historyTarget.assetName || historyTarget.assetId}
                        onDismiss={() => setHistoryTarget(null)}
                    />
                )}

                {revokeTarget && (
                    <RevokeExceptionModal
                        visible={!!revokeTarget}
                        revoking={revoking}
                        onConfirm={handleRevokeException}
                        onDismiss={() => setRevokeTarget(null)}
                    />
                )}
            </SpaceBetween>
        </Box>
    );
};

export default DatabaseCompliancePage;
