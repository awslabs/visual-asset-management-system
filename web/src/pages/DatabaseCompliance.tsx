/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback, useRef } from "react";
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
import FormField from "@cloudscape-design/components/form-field";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import Modal from "@cloudscape-design/components/modal";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import Synonyms from "../synonyms";
import {
    fetchDatabaseComplianceOverview,
    evaluateDatabaseCompliance,
    getDatabaseBindings,
    fetchComplianceSchemas,
    bindableSchemas,
    bindSchemaToDatabase,
    bindingResultText,
    bindingApplyOutcomeText,
    formatCount,
    bindingApplyPollDelay,
    bindingApplyProgressText,
    bindingEvaluationsProgressText,
    unbindSchemaFromDatabase,
    revokeException,
    ComplianceBindingApply,
    ComplianceBindingResult,
    DatabaseComplianceOverview,
    EvaluateDatabaseResponse,
    BINDING_APPLY_APPLIED,
    BINDING_APPLY_APPLYING,
    BINDING_APPLY_FAILED,
    BINDING_APPLY_STALLED,
    BINDING_EVALUATIONS_QUEUEING,
    BINDING_EVALUATIONS_STALLED,
    COMPLIANCE_LISTING_PAGE_SIZE,
} from "../services/ComplianceService";
import {
    COMPLIANCE_STATE_INDICATORS,
    ComplianceStateBadge,
    EvaluationErrorIndicator,
    complianceStateIndicator,
} from "../components/compliance/complianceStateBadge";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";
import EvaluationHistoryModal from "../components/compliance/EvaluationHistoryModal";
import { schemaSourceLabel } from "../components/compliance/complianceStateBadge";
import RevokeExceptionModal from "../components/compliance/RevokeExceptionModal";
import {
    ROW_ACTIONS_STICKY_COLUMNS,
    SUMMARY_TILE_MIN_WIDTH,
    VAMS_PAGE_PADDING,
} from "../components/compliance/rowActions";
import { useAssetPageAccess } from "../hooks/useAssetPageAccess";
import {
    TextFilterInput,
    useLatestRequest,
    useUrlFilters,
} from "../components/compliance/listingFilters";
import {
    ListingLoadFailed,
    listedBefore,
    listingEmptyText,
    loadFailureMessage,
} from "../components/compliance/listingStates";
import ComplianceAssetCell from "../components/compliance/ComplianceAssetCell";
import {
    REMOVE_BINDING_CONFIRM_LABEL,
    REMOVE_BINDING_HEADER,
    bindingChangeConfirmLabel,
    bindingChangeConfirmation,
    bindingChangeHeader,
    removeBindingConfirmation,
} from "../components/compliance/bindingConfirmations";
import ListToolbar from "../components/list/ListToolbar";
import { PAGE_SORTING_NOTE, byDate, byText, useSortedItems } from "../components/list/tableSorting";

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

/**
 * Shown when the name sort was asked while the service is still building its name index (for a
 * few minutes after an upgrade, longer on a large table); the rows are then in ID order.
 */
export const NAME_SORT_UNAVAILABLE_NOTICE = `Sort by name is available once indexing finishes. The ${Synonyms.assets} are shown in ${Synonyms.asset} ID order until then.`;

const optionFor = (options: SelectProps.Option[], value: string): SelectProps.Option =>
    options.find((option) => option.value === value) || options[0];

// The routes behind the database binding control (backend/backend/common/apiRoutes.py): the
// schema list fills the selector, PUT binds or changes the schema, DELETE removes it.
/**
 * What a failed binding read shows: the header's status label and the disabled schema select's
 * placeholder. Short, so neither runs past its container at 390 px (D160); the reason follows the
 * label as text.
 */
export const BINDING_NOT_LOADED_LABEL = "Not loaded";

/**
 * The status labels of a binding change applied in the background (D161), short like
 * BINDING_NOT_LOADED_LABEL so a status indicator never wraps at 390 px; the sentence and the counts
 * follow as text and in the progress bar.
 */
export const bindingApplyLabel = (apply: ComplianceBindingApply): string => {
    if (apply.status === BINDING_APPLY_STALLED) return "Stopped";
    if (apply.status === BINDING_APPLY_FAILED) return "Not finished";
    if (apply.operation === "unbind") return "Removing the binding";
    if (apply.operation === "evaluatePending") return "Queueing evaluations";
    return "Applying the binding";
};

/** The evaluations phase of a binding change: queueing, and stopped (short, as above). */
export const BINDING_EVALUATIONS_LABEL = "Queueing evaluations";
export const BINDING_EVALUATIONS_STOPPED_LABEL = "Evaluations stopped";

/** Whether a binding change of the database is under way (running or stalled): the API answers
 * 409 to another change and to Evaluate All until it ends, so the page does not offer them. */
export const bindingApplyBusy = (apply: ComplianceBindingApply | null | undefined): boolean =>
    !!apply && (apply.status === BINDING_APPLY_APPLYING || apply.status === BINDING_APPLY_STALLED);

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
 * reached (the request is bounded like a sweep), how many were registered under the binding, and
 * how many were skipped as archived or as assets the caller may not read.
 */
export const evaluateAllMessage = (result: EvaluateDatabaseResponse): string => {
    const parts = [
        result.message || `Evaluation triggered for ${formatCount(result.assetsTriggered.length)}`,
    ];
    if (result.registered) {
        parts.push(
            `${formatCount(result.registered)} newly registered under the ${
                Synonyms.database
            } schema`
        );
    }
    if (result.skippedArchived) {
        parts.push(`${formatCount(result.skippedArchived)} archived, not evaluated`);
    }
    if (result.skippedNotAuthorized) {
        parts.push(`${formatCount(result.skippedNotAuthorized)} skipped (you may not read them)`);
    }
    if (result.assetsRemaining) {
        parts.push(
            `${formatCount(result.assetsRemaining)} not reached yet; choose Evaluate All again`
        );
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

/**
 * The database a database compliance overview URL (`#/databases/<id>/compliance`) names, or null
 * for any other URL. Moving to another database's overview changes the URL first: the filters
 * follow it at once, the route's `databaseId` a render later.
 */
export const overviewRouteDatabaseId = (): string | null => {
    const match = /^#\/databases\/([^/?]+)\/compliance\/?(\?|$)/.exec(window.location.hash || "");
    return match ? decodeURIComponent(match[1]) : null;
};

const DatabaseCompliancePage: React.FC = () => {
    const { databaseId } = useParams();
    // Each load is dropped when a later one has begun (another database, other filters, another
    // page), so the rows and counts shown are always the latest request's.
    const beginOverviewLoad = useLatestRequest();
    const beginBindingLoad = useLatestRequest();
    const beginSchemasLoad = useLatestRequest();
    const [overview, setOverview] = useState<DatabaseComplianceOverview | null>(null);
    const [databaseSchema, setDatabaseSchema] = useState<string | null>(null);
    // Whether the binding read has answered: until it has, the page says the binding is loading
    // rather than that no schema is bound, and a failed read shows its error (D154).
    const [bindingStatus, setBindingStatus] = useState<"loading" | "loaded" | "failed">("loading");
    const [bindingError, setBindingError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [sweeping, setSweeping] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Whether the last overview read failed: the asset table then says nothing was loaded rather
    // than that the database tracks no assets.
    const [loadFailed, setLoadFailed] = useState(false);
    const [actionMessage, setActionMessage] = useState<string | null>(null);
    // Why the API refused the last action (bind, change, removal, Evaluate All, revoke). Kept apart
    // from `error`, which every overview load clears, so the refusal stays on the page while the
    // binding and the overview are read again after it.
    const [actionError, setActionError] = useState<string | null>(null);
    // The NextToken of the last Evaluate All while assets remained: the next Evaluate All
    // continues that evaluation with the assets not evaluated yet.
    const [evaluateAllToken, setEvaluateAllToken] = useState<string | undefined>(undefined);

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
    // Bind and Change reach every asset of the database, so each is confirmed first, like Remove.
    const [confirmBind, setConfirmBind] = useState(false);
    // The database's last binding change applied in the background, as the binding read reports it
    // (D161). While it runs, the binding is read again with backoff (`applyPolls` counts the reads)
    // until it ends; `followedApply` is the change this page saw running, whose end it reports.
    const [bindingApply, setBindingApply] = useState<ComplianceBindingApply | null>(null);
    const [applyPolls, setApplyPolls] = useState(0);
    const followedApply = useRef<string | null>(null);
    const [filters, setFilters] = useUrlFilters(OVERVIEW_FILTER_KEYS);
    const filtersActive = OVERVIEW_FILTER_KEYS.some((key) => key !== "sort" && !!filters[key]);

    // Server-side token paging of the asset-state table: tokens[i] is the startingToken that
    // fetches page i (tokens[0] is undefined). The summary counts cover the whole database on
    // every page; only `assets` is the page.
    const [tokens, setTokens] = useState<Record<number, string | undefined>>({});
    const [hasMore, setHasMore] = useState(false);
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [loadedPages, setLoadedPages] = useState(1);
    // Which loaded pages (0-based) listed an asset (listingStates.listedBefore).
    const [listedPages, setListedPages] = useState<Record<number, boolean>>({});

    const loadPage = useCallback(
        async (pageNumber: number, startingToken: string | undefined) => {
            if (!databaseId) return;
            const isCurrent = beginOverviewLoad();
            // The URL already names another database: the filters followed it before the route's
            // `databaseId` did, and the load for that database follows. Nothing is requested for
            // this one.
            const routeDatabaseId = overviewRouteDatabaseId();
            if (routeDatabaseId !== null && routeDatabaseId !== databaseId) return;
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
                if (!isCurrent()) return;
                if (overviewSuccess && typeof overviewResult !== "string") {
                    setOverview(overviewResult);
                    setListedPages((prev) => ({
                        ...prev,
                        [pageNumber]: (overviewResult.assets || []).length > 0,
                    }));
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
                        loadFailureMessage(
                            `the ${Synonyms.database} compliance overview`,
                            overviewResult
                        )
                    );
                    setHasMore(false);
                }
            } catch (err: any) {
                if (!isCurrent()) return;
                setLoadFailed(true);
                setError(
                    loadFailureMessage(`the ${Synonyms.database} compliance overview`, err?.message)
                );
            } finally {
                if (isCurrent()) {
                    setLoading(false);
                }
            }
        },
        [
            beginOverviewLoad,
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
        setListedPages({});
        setHasMore(false);
        await loadPage(0, undefined);
    }, [databaseId, loadPage]);

    // Reads the binding. `quiet` is a read that follows a change being applied: the page keeps
    // showing what it shows (no loading state), and a failed read is left to the next one.
    const loadBinding = useCallback(
        async (options?: { quiet?: boolean }) => {
            if (!databaseId) return;
            const quiet = !!options?.quiet;
            const isCurrent = beginBindingLoad();
            const routeDatabaseId = overviewRouteDatabaseId();
            if (routeDatabaseId !== null && routeDatabaseId !== databaseId) return;
            if (!quiet) {
                setBindingStatus("loading");
                setBindingError(null);
            }
            const [bindingSuccess, bindingResult] = await getDatabaseBindings(databaseId);
            if (!isCurrent()) return;
            if (bindingSuccess && typeof bindingResult !== "string") {
                const bound = bindingResult.databaseSchema || null;
                setDatabaseSchema(bound);
                setSelectedSchema(bound ? { label: bound, value: bound } : null);
                setBindingStatus("loaded");
                const apply = bindingResult.apply || null;
                setBindingApply(apply);
                if (apply?.status === BINDING_APPLY_APPLYING) {
                    followedApply.current = apply.applyId;
                } else if (apply && followedApply.current === apply.applyId) {
                    // The change this page saw running ended (or stopped): its outcome, then the
                    // overview as the records now are.
                    followedApply.current = null;
                    if (apply.status === BINDING_APPLY_APPLIED) {
                        setActionMessage(bindingApplyOutcomeText(apply));
                    } else if (apply.status === BINDING_APPLY_FAILED) {
                        setActionError(bindingApplyOutcomeText(apply));
                    }
                    await restartWalk();
                }
            } else if (!quiet) {
                setBindingStatus("failed");
                setBindingError(
                    typeof bindingResult === "string" && bindingResult
                        ? bindingResult
                        : "Failed to load the schema binding"
                );
            }
        },
        [beginBindingLoad, databaseId, restartWalk]
    );

    // While a binding change is applied, or its evaluations are queued, the binding is read again
    // with backoff until it ends.
    const applyRunning =
        bindingApply?.status === BINDING_APPLY_APPLYING ||
        bindingApply?.evaluationStatus === BINDING_EVALUATIONS_QUEUEING;
    const runningApplyId = applyRunning ? bindingApply?.applyId : undefined;
    useEffect(() => {
        setApplyPolls(0);
    }, [runningApplyId]);
    useEffect(() => {
        if (!runningApplyId) return;
        const timer = setTimeout(async () => {
            await loadBinding({ quiet: true });
            setApplyPolls((count) => count + 1);
        }, bindingApplyPollDelay(applyPolls));
        return () => clearTimeout(timer);
    }, [runningApplyId, applyPolls, loadBinding]);

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

    // The schemas this database may bind, re-read for each database the page shows.
    useEffect(() => {
        if (!canBind || !databaseId) return;
        const loadSchemas = async () => {
            const isCurrent = beginSchemasLoad();
            setLoadingSchemas(true);
            const [success, result] = await fetchComplianceSchemas(databaseId);
            if (!isCurrent()) return;
            if (success && Array.isArray(result)) {
                setSchemaOptions(
                    bindableSchemas(result).map((s) => ({
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
    }, [beginSchemasLoad, canBind, databaseId]);

    // Clears the last action's outcome before the next action is sent.
    const beginAction = () => {
        setActionMessage(null);
        setActionError(null);
    };

    // Reports an action's outcome, then reads the binding and the overview again whether the
    // action succeeded or was refused (D162). A refusal can follow a change made elsewhere (another
    // tab or user bound or removed the schema, released an asset or revoked its exception), and
    // the binding, the counts and the rows then follow the records as the API now holds them; the
    // refusal stays shown.
    const finishAction = async (outcome: { message: string | null } | { refusal: string }) => {
        if ("refusal" in outcome) {
            setActionError(outcome.refusal);
        } else {
            setActionMessage(outcome.message);
        }
        await loadData();
    };

    // Binds the selected schema, or replaces the bound one, then re-reads the binding and the
    // overview. Sent from the confirmation dialog.
    const handleBind = async () => {
        setConfirmBind(false);
        const schemaName = selectedSchema?.value;
        if (!databaseId || !schemaName) return;
        setBindingInProgress(true);
        beginAction();
        const [success, message, result] = await bindSchemaToDatabase(databaseId, schemaName);
        if (success && result?.apply) {
            // Saved, and applied to the asset records in the background (D161): the binding read
            // that follows shows its progress, and its end reports the counts.
            followedApply.current = result.apply.applyId;
            await finishAction({
                message: `Compliance schema ${schemaName} bound to this ${
                    Synonyms.database
                }. ${bindingApplyProgressText(result.apply)}.`,
            });
        } else if (success) {
            const detail = bindingResultText(result);
            await finishAction({
                message:
                    result?.changed === false
                        ? detail
                        : [
                              `Compliance schema ${schemaName} bound to this ${Synonyms.database}.`,
                              detail,
                          ]
                              .filter(Boolean)
                              .join(" "),
            });
        } else {
            await finishAction({ refusal: `The compliance schema binding failed: ${message}` });
        }
        setBindingInProgress(false);
    };

    const handleRemove = async () => {
        setConfirmRemove(false);
        if (!databaseId || !databaseSchema) return;
        const removed = databaseSchema;
        setBindingInProgress(true);
        beginAction();
        const [success, message, result] = await unbindSchemaFromDatabase(databaseId);
        if (success && result?.apply) {
            followedApply.current = result.apply.applyId;
        }
        await finishAction(
            success && result?.apply
                ? {
                      message: `Compliance schema ${removed} removed from this ${
                          Synonyms.database
                      }. ${bindingApplyProgressText(result.apply)}.`,
                  }
                : success
                ? {
                      message: [
                          `Compliance schema ${removed} removed from this ${Synonyms.database}.`,
                          bindingResultText(result),
                      ]
                          .filter(Boolean)
                          .join(" "),
                  }
                : { refusal: `Removing the compliance schema binding failed: ${message}` }
        );
        setBindingInProgress(false);
    };

    // Repeats a stalled change, which resumes it where it stopped (the API decides; another change
    // answers 409 while it is under way).
    const handleResume = async () => {
        if (!databaseId || !bindingApply) return;
        const stalled = bindingApply;
        setBindingInProgress(true);
        beginAction();
        const [success, message, result]: [boolean, string, ComplianceBindingResult?] =
            stalled.operation === "unbind"
                ? await unbindSchemaFromDatabase(databaseId)
                : await bindSchemaToDatabase(databaseId, stalled.schemaName || "");
        if (success && result?.apply) {
            followedApply.current = result.apply.applyId;
        }
        await finishAction(
            success
                ? {
                      message: result?.apply
                          ? `${bindingApplyProgressText(result.apply)}.`
                          : message,
                  }
                : { refusal: `The binding change did not resume: ${message}` }
        );
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
        beginAction();

        const [success, result] = await evaluateDatabaseCompliance(databaseId, evaluateAllToken);
        if (success && typeof result !== "string") {
            setEvaluateAllToken(result.NextToken || undefined);
            await finishAction({ message: evaluateAllMessage(result) });
        } else {
            await finishAction({
                refusal: typeof result === "string" ? result : "Evaluation failed",
            });
        }
        setSweeping(false);
    };

    const handleRevokeException = async () => {
        if (!databaseId || !revokeTarget) return;
        const target = revokeTarget;
        const label = target.assetName || target.assetId;
        setRevoking(true);
        beginAction();
        const [success, result] = await revokeException(databaseId, target.assetId);
        setRevoking(false);
        setRevokeTarget(null);
        // The refusal names the asset: after the re-read its row may show another state.
        await finishAction(
            success && typeof result !== "string"
                ? { message: `Exception revoked for ${label}.` }
                : {
                      refusal: `The exception for ${label} was not revoked: ${
                          typeof result === "string" ? result : "Failed to revoke exception"
                      }`,
                  }
        );
    };

    const summary = overview?.summary;
    const totalTracked = overview?.totalAssets || 0;
    const compliantCount = summary?.compliant || 0;
    // No rate without tracked assets the reader can view: 0% would read as none compliant.
    const complianceRate =
        totalTracked > 0 ? Math.round((compliantCount / totalTracked) * 100) : null;
    // A failed load shows no rows: those of an earlier load belong to another page or filter.
    const pageAssets = loadFailed ? [] : overview?.assets || [];
    const isPaging = hasMore || currentPageIndex > 1;
    // The rows of the page shown, in the order of the column header chosen; the Sort by filter
    // orders the whole database on the server.
    const { items: sortedAssets, sortingProps } = useSortedItems<any>(pageAssets);
    // The binding as the last read answered it; nothing is said about it before that.
    const bindingKnown = bindingStatus === "loaded";
    // While a change is under way the page offers no other change and no Evaluate All (the API
    // answers 409 to them); a stalled change offers Resume to a caller who may make it.
    const applyBusy = bindingApplyBusy(bindingApply);
    const canResume =
        (bindingApply?.status === BINDING_APPLY_STALLED ||
            bindingApply?.evaluationStatus === BINDING_EVALUATIONS_STALLED) &&
        (bindingApply.operation === "unbind" ? canUnbind : canBind);
    const applyDescription =
        !bindingKnown || !bindingApply ? null : bindingApply.status === BINDING_APPLY_APPLYING ? (
            <SpaceBetween size="xxs">
                <StatusIndicator type="in-progress">
                    {bindingApplyLabel(bindingApply)}
                </StatusIndicator>
                <ProgressBar
                    value={
                        bindingApply.assetCount > 0
                            ? Math.min(
                                  100,
                                  Math.round(
                                      (bindingApply.assetsProcessed / bindingApply.assetCount) * 100
                                  )
                              )
                            : 0
                    }
                    description={bindingApplyProgressText(bindingApply)}
                    additionalInfo={`${formatCount(bindingApply.assetsProcessed)} of ${formatCount(
                        bindingApply.assetCount
                    )} done`}
                    ariaLabel="Binding change progress"
                />
            </SpaceBetween>
        ) : bindingApply.status === BINDING_APPLY_STALLED ||
          bindingApply.status === BINDING_APPLY_FAILED ? (
            <SpaceBetween size="xxs">
                <StatusIndicator
                    type={bindingApply.status === BINDING_APPLY_STALLED ? "warning" : "error"}
                >
                    {bindingApplyLabel(bindingApply)}
                </StatusIndicator>
                <Box variant="span" color="inherit">
                    {bindingApplyOutcomeText(bindingApply)}
                </Box>
                {canResume && (
                    <Button onClick={handleResume} loading={bindingInProgress}>
                        Resume
                    </Button>
                )}
            </SpaceBetween>
        ) : bindingApply.evaluationStatus === BINDING_EVALUATIONS_QUEUEING ? (
            // Every record carries the binding; the evaluations are queued a share at a time and
            // nothing waits on them.
            <SpaceBetween size="xxs">
                <StatusIndicator type="in-progress">{BINDING_EVALUATIONS_LABEL}</StatusIndicator>
                <Box variant="span" color="inherit">
                    {bindingEvaluationsProgressText(bindingApply)}
                </Box>
            </SpaceBetween>
        ) : bindingApply.evaluationStatus === BINDING_EVALUATIONS_STALLED ? (
            <SpaceBetween size="xxs">
                <StatusIndicator type="warning">
                    {BINDING_EVALUATIONS_STOPPED_LABEL}
                </StatusIndicator>
                <Box variant="span" color="inherit">
                    {`Queueing the evaluations stopped: ${
                        bindingApply.evaluationReason || "repeat the same change to resume it"
                    }.`}
                </Box>
                {canResume && (
                    <Button onClick={handleResume} loading={bindingInProgress}>
                        Resume
                    </Button>
                )}
            </SpaceBetween>
        ) : null;
    const bindingDescription =
        bindingStatus === "loading" ? (
            <StatusIndicator type="loading">Loading the schema binding</StatusIndicator>
        ) : bindingStatus === "failed" ? (
            // A status indicator breaks words wherever it wraps, so it carries a short label and the
            // reason follows as text that wraps between words (D160). Retry only reads the binding
            // again, so every reader who sees the error gets it, binding control or not.
            <SpaceBetween size="xxs">
                <StatusIndicator type="error">{BINDING_NOT_LOADED_LABEL}</StatusIndicator>
                <Box variant="span" color="inherit">
                    {`The schema binding could not be loaded: ${bindingError}`}
                </Box>
                <Button onClick={() => loadBinding()}>Retry</Button>
            </SpaceBetween>
        ) : (
            <SpaceBetween size="xxs">
                <span>
                    {databaseSchema ? `Schema: ${databaseSchema}` : "No compliance schema bound"}
                </span>
                {applyDescription}
            </SpaceBetween>
        );

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

                {actionError && (
                    <Alert type="error" dismissible onDismiss={() => setActionError(null)}>
                        {actionError}
                    </Alert>
                )}
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

                {(canBind || (canUnbind && bindingKnown && databaseSchema)) && (
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
                                        placeholder={
                                            bindingStatus === "loading"
                                                ? "Loading the bound schema"
                                                : bindingStatus === "failed"
                                                ? BINDING_NOT_LOADED_LABEL
                                                : "Select a compliance schema"
                                        }
                                        loadingText="Loading schemas"
                                        statusType={loadingSchemas ? "loading" : "finished"}
                                        filteringType="auto"
                                        disabled={bindingInProgress || !bindingKnown || applyBusy}
                                        data-testid="database-compliance-bound-schema"
                                    />
                                </FormField>
                            )}
                            {canBind && (
                                <Button
                                    onClick={() => setConfirmBind(true)}
                                    loading={bindingInProgress}
                                    disabled={
                                        !bindingKnown ||
                                        applyBusy ||
                                        !selectedSchema?.value ||
                                        selectedSchema.value === databaseSchema
                                    }
                                >
                                    {databaseSchema ? "Change" : "Bind"}
                                </Button>
                            )}
                            {canUnbind && bindingKnown && databaseSchema && (
                                <Button
                                    onClick={() => setConfirmRemove(true)}
                                    disabled={bindingInProgress || applyBusy}
                                >
                                    Remove
                                </Button>
                            )}
                        </SpaceBetween>
                    </Container>
                )}

                {/* Each confirmation is mounted while it is open, so the page holds one at a time. */}
                {confirmBind && (
                    <Modal
                        visible={confirmBind}
                        onDismiss={() => setConfirmBind(false)}
                        header={bindingChangeHeader(databaseSchema)}
                        footer={
                            <Box float="right">
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button variant="link" onClick={() => setConfirmBind(false)}>
                                        Cancel
                                    </Button>
                                    <Button variant="primary" onClick={handleBind}>
                                        {bindingChangeConfirmLabel(databaseSchema)}
                                    </Button>
                                </SpaceBetween>
                            </Box>
                        }
                    >
                        {bindingChangeConfirmation(
                            databaseId || "",
                            databaseSchema,
                            selectedSchema?.value || ""
                        )}
                    </Modal>
                )}

                {confirmRemove && (
                    <Modal
                        visible={confirmRemove}
                        onDismiss={() => setConfirmRemove(false)}
                        header={REMOVE_BINDING_HEADER}
                        footer={
                            <Box float="right">
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button variant="link" onClick={() => setConfirmRemove(false)}>
                                        Cancel
                                    </Button>
                                    <Button variant="primary" onClick={handleRemove}>
                                        {REMOVE_BINDING_CONFIRM_LABEL}
                                    </Button>
                                </SpaceBetween>
                            </Box>
                        }
                    >
                        {removeBindingConfirmation(databaseSchema || "", databaseId || "")}
                    </Modal>
                )}

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
                                        disabled={
                                            applyBusy || (!databaseSchema && totalTracked === 0)
                                        }
                                    >
                                        Evaluate All {Synonyms.Assets}
                                    </Button>
                                ) : undefined
                            }
                            description={bindingDescription}
                        >
                            {Synonyms.Database} Compliance Overview
                        </Header>
                    }
                >
                    <ColumnLayout
                        columns={9}
                        minColumnWidth={SUMMARY_TILE_MIN_WIDTH}
                        variant="text-grid"
                    >
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
                                    {formatCount(totalTracked)}
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
                                            {formatCount((summary as any)?.[tile.state])}
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
                                        {formatCount(summary?.error)}
                                        {filters.evaluationError === "true" ? " (shown)" : ""}
                                    </StatusIndicator>
                                </Button>
                            </Box>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Compliance Rate</Box>
                            <Box variant="awsui-value-large">
                                {complianceRate === null ? "-" : `${complianceRate}%`}
                            </Box>
                        </div>
                    </ColumnLayout>
                </Container>

                <Container
                    header={
                        <Header
                            variant="h3"
                            counter={
                                loadFailed
                                    ? undefined
                                    : filtersActive
                                    ? `(${formatCount(pageAssets.length)}${hasMore ? "+" : ""})`
                                    : `(${formatCount(pageAssets.length)} of ${formatCount(
                                          totalTracked
                                      )})`
                            }
                            description={
                                loadFailed
                                    ? undefined
                                    : [
                                          filtersActive
                                              ? `${
                                                    Synonyms.Assets
                                                } matching the filters; the summary above covers all ${formatCount(
                                                    totalTracked
                                                )} tracked ${Synonyms.assets}.`
                                              : isPaging
                                              ? `Showing ${formatCount(
                                                    pageAssets.length
                                                )} of ${formatCount(totalTracked)} tracked ${
                                                    Synonyms.assets
                                                }; the summary above covers all of them.`
                                              : null,
                                          isPaging
                                              ? `${PAGE_SORTING_NOTE} Sort by orders every ${Synonyms.asset} of the ${Synonyms.database}.`
                                              : null,
                                      ]
                                          .filter(Boolean)
                                          .join(" ") || undefined
                            }
                        >
                            {Synonyms.Asset} Compliance States
                        </Header>
                    }
                >
                    {filters.sort === "name" && overview?.nameSortUnavailable && (
                        <Box margin={{ bottom: "s" }}>
                            <Alert type="info" data-testid="name-sort-unavailable">
                                {NAME_SORT_UNAVAILABLE_NOTICE}
                            </Alert>
                        </Box>
                    )}
                    <Table
                        wrapLines
                        stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
                        filter={
                            <ListToolbar
                                search={
                                    <TextFilterInput
                                        value={filters.text}
                                        onCommit={(text) => setFilters({ text })}
                                        placeholder={`Search ${Synonyms.asset} name or ID`}
                                        ariaLabel={`Search ${Synonyms.assets}`}
                                    />
                                }
                                onRefresh={loadData}
                                refreshing={loading}
                                refreshLabel={`Refresh the ${Synonyms.database} compliance overview`}
                                filters={
                                    <>
                                        <Select
                                            selectedOption={optionFor(
                                                OVERVIEW_STATE_OPTIONS,
                                                filters.state
                                            )}
                                            options={OVERVIEW_STATE_OPTIONS}
                                            ariaLabel="Filter by compliance state"
                                            onChange={({ detail }) =>
                                                setFilters({
                                                    state: detail.selectedOption.value || "",
                                                })
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
                                                            ...schemaOptions.map(
                                                                (o) => o.value || ""
                                                            ),
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
                                                    evaluationError:
                                                        detail.selectedOption.value || "",
                                                })
                                            }
                                        />
                                        <Select
                                            selectedOption={optionFor(
                                                OVERVIEW_SORT_OPTIONS,
                                                filters.sort
                                            )}
                                            options={OVERVIEW_SORT_OPTIONS}
                                            ariaLabel={`Sort ${Synonyms.assets}`}
                                            onChange={({ detail }) =>
                                                setFilters({
                                                    sort: detail.selectedOption.value || "",
                                                })
                                            }
                                        />
                                    </>
                                }
                            />
                        }
                        loading={loading}
                        items={sortedAssets}
                        {...sortingProps}
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
                            loadFailed ? (
                                <ListingLoadFailed
                                    what={`The ${Synonyms.asset} compliance states`}
                                    onRetry={() =>
                                        loadPage(currentPageIndex - 1, tokens[currentPageIndex - 1])
                                    }
                                />
                            ) : (
                                <Box textAlign="center" padding="l">
                                    {listingEmptyText(
                                        {
                                            hasMore,
                                            pageIndex: currentPageIndex,
                                            listedEarlier: listedBefore(
                                                listedPages,
                                                currentPageIndex
                                            ),
                                            filtered: filtersActive,
                                        },
                                        Synonyms.assets,
                                        `No ${Synonyms.assets} you can view are being tracked for compliance in this ${Synonyms.database}.`
                                    )}
                                </Box>
                            )
                        }
                        columnDefinitions={[
                            {
                                id: "asset",
                                header: Synonyms.Asset,
                                cell: (item: any) => (
                                    <ComplianceAssetCell
                                        databaseId={databaseId || ""}
                                        assetId={item.assetId}
                                        assetName={item.assetName}
                                        linked={canOpenAsset}
                                    />
                                ),
                                sortingField: "assetName",
                                sortingComparator: byText(
                                    (item: any) => item.assetName || item.assetId
                                ),
                            },
                            {
                                id: "complianceState",
                                header: "State",
                                // The labels are one line each (StatusLabel): the column is as
                                // wide as the longest one on the page.
                                cell: (item: any) => (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <ComplianceStateBadge state={item.complianceState} />
                                        <EvaluationErrorIndicator
                                            lastEvaluationStatus={item.lastEvaluationStatus}
                                        />
                                    </SpaceBetween>
                                ),
                                sortingField: "complianceState",
                                sortingComparator: byText(
                                    (item: any) =>
                                        complianceStateIndicator(item.complianceState).label
                                ),
                            },
                            {
                                // The bound schema with where its binding comes from beneath (the
                                // database's binding or the asset's own), in one column so the
                                // table fits the page at 1280 px.
                                id: "schemaName",
                                header: "Bound schema",
                                cell: (item: any) => (
                                    <SpaceBetween size="xxxs">
                                        <span>{item.schemaName || "-"}</span>
                                        {item.schemaSource && (
                                            <Box variant="small" color="text-body-secondary">
                                                {schemaSourceLabel(item.schemaSource)}
                                            </Box>
                                        )}
                                    </SpaceBetween>
                                ),
                                sortingField: "schemaName",
                                sortingComparator: byText((item: any) => item.schemaName),
                            },
                            {
                                id: "updatedAt",
                                header: "Last Updated",
                                cell: (item: any) =>
                                    item.updatedAt
                                        ? new Date(item.updatedAt).toLocaleString()
                                        : "-",
                                sortingField: "updatedAt",
                                sortingComparator: byDate((item: any) => item.updatedAt),
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
