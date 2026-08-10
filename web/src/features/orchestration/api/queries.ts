/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useRef } from "react";
import {
    useQuery,
    useInfiniteQuery,
    useMutation,
    useQueryClient,
    type UseQueryOptions,
    type UseInfiniteQueryOptions,
    type InfiniteData,
} from "@tanstack/react-query";
import * as pipelineService from "./pipelines";
import * as workflowService from "./workflows";
import * as executionService from "./executions";
import * as databaseService from "./databases";
import * as assetService from "./assets";
import type {
    Pipeline,
    PipelineCreateRequest,
    WorkflowCreateRequest,
    Template,
    Workflow,
    WorkflowTrigger,
    Execution,
    ExecutionDetail,
} from "../types";
import type { DatabaseSummary } from "./databases";
import type { DetailMetadataCollection, DetailMetadataPage } from "./executions";
import type {
    AssetSummary,
    AssetFileSummary,
    AssetFileVersionSummary,
    AssetSearchPage,
    AssetFilePage,
} from "./assets";

// Query key factory for stable, structured keys
export const qk = {
    pipelines: (databaseId?: string, filters?: any) =>
        ["pipelines", databaseId ?? null, filters ?? null] as const,
    pipeline: (databaseId: string, pipelineId: string) =>
        ["pipeline", databaseId, pipelineId] as const,
    templates: (databaseId: string, pipelineId: string) =>
        ["templates", databaseId, pipelineId] as const,
    template: (databaseId: string, pipelineId: string, templateId: string) =>
        ["template", databaseId, pipelineId, templateId] as const,
    workflows: (databaseId?: string, filters?: any) =>
        ["workflows", databaseId ?? null, filters ?? null] as const,
    workflow: (databaseId: string, workflowId: string) =>
        ["workflow", databaseId, workflowId] as const,
    triggers: (databaseId: string, workflowId: string) =>
        ["triggers", databaseId, workflowId] as const,
    executions: (scope: ExecutionScope, filters?: any) =>
        ["executions", scope, filters ?? null] as const,
    execution: (executionId: string) => ["execution", executionId] as const,
    executionDetailMetadata: (executionId: string, collection: DetailMetadataCollection) =>
        ["executionDetailMetadata", executionId, collection] as const,
    allowedRoutes: () => ["allowedRoutes"] as const,
    databases: () => ["databases"] as const,
    assets: (databaseId?: string) => ["assets", databaseId ?? null] as const,
    assetSearch: (databaseId: string | undefined, query: string) =>
        ["assetSearch", databaseId ?? null, query] as const,
    assetFiles: (databaseId: string, assetId: string) =>
        ["assetFiles", databaseId, assetId] as const,
    assetFileSearch: (databaseId: string, assetId: string, query: string) =>
        ["assetFileSearch", databaseId, assetId, query] as const,
    fileVersions: (databaseId: string, assetId: string, relativeFileKey: string) =>
        ["fileVersions", databaseId, assetId, relativeFileKey] as const,
};

/** All databases the caller can see — for the create-in-database picker on the global list pages. */
export function useDatabases(enabled = true) {
    return useQuery({
        queryKey: qk.databases(),
        queryFn: () => callService<DatabaseSummary[]>(() => databaseService.listAllDatabases()),
        enabled,
    });
}

/** Assets for the execute-wizard asset selector — scoped to a database, or all when none given. */
export function useAssets(databaseId?: string, enabled = true) {
    return useQuery({
        queryKey: qk.assets(databaseId),
        queryFn: () => callService<AssetSummary[]>(() => assetService.listAssets(databaseId)),
        enabled,
    });
}

/**
 * One SERVER-resolved page of assets matching `query`, for the execute wizard's asset pickers.
 *
 * Unlike `useAssets` (which loads a database's assets for client-side filtering) this re-queries per
 * search term, so a database holding thousands of assets does not have to be pulled into the browser.
 * `keepPreviousData` holds the previous page on screen while the next one loads, so the list does not
 * flash empty on every keystroke.
 */
export function useAssetSearch(query: string, databaseId?: string, enabled = true) {
    return useQuery({
        queryKey: qk.assetSearch(databaseId, query),
        queryFn: () =>
            callService<AssetSearchPage>(() => assetService.searchAssetsPaged(query, databaseId)),
        enabled,
        placeholderData: (previous: any) => previous,
    });
}

/** Non-folder files for an asset — for the wizard file selector. Disabled until an asset is chosen. */
export function useAssetFiles(databaseId?: string, assetId?: string) {
    return useQuery({
        queryKey: qk.assetFiles(databaseId || "", assetId || ""),
        queryFn: () =>
            callService<AssetFileSummary[]>(() =>
                assetService.listAssetFiles(databaseId as string, assetId as string)
            ),
        enabled: !!databaseId && !!assetId,
    });
}

/**
 * One SERVER-resolved page of an asset's files matching `query`, for the wizard's file picker.
 *
 * The file-search counterpart to `useAssetSearch`: an asset can hold thousands of files, so the term
 * is resolved against the search index rather than by filtering a full listing in the browser.
 */
export function useAssetFileSearch(query: string, databaseId?: string, assetId?: string) {
    return useQuery({
        queryKey: qk.assetFileSearch(databaseId || "", assetId || "", query),
        queryFn: () =>
            callService<AssetFilePage>(() =>
                assetService.searchAssetFilesPaged(query, databaseId as string, assetId as string)
            ),
        enabled: !!databaseId && !!assetId,
        placeholderData: (previous: any) => previous,
    });
}

/**
 * The S3 object versions of ONE file — for the per-file version selector.
 *
 * Keyed on the file as well as the asset: an execution's `versionId` is an S3 VersionId for that exact
 * key, so each selected file has its own version list. A whole-asset or folder selection has no single
 * version, so the query stays disabled for a key ending in '/'.
 */
export function useFileVersions(databaseId?: string, assetId?: string, relativeFileKey?: string) {
    const key = relativeFileKey || "";
    return useQuery({
        queryKey: qk.fileVersions(databaseId || "", assetId || "", key),
        queryFn: () =>
            callService<AssetFileVersionSummary[]>(() =>
                assetService.listFileVersions(databaseId as string, assetId as string, key)
            ),
        enabled: !!databaseId && !!assetId && !!key && !key.endsWith("/"),
    });
}

// Execution scope discriminated union
export type ExecutionScope =
    | { kind: "global" }
    | { kind: "workflow"; databaseId: string; workflowId: string }
    | { kind: "asset"; databaseId: string; assetId: string };

// Pure helper: returns 5000 if any row is NEW or RUNNING, else false
export function computeRefetchInterval(rows: any[]): number | false {
    if (!rows || rows.length === 0) return false;
    const hasNonTerminal = rows.some(
        (row) => row.executionStatus === "NEW" || row.executionStatus === "RUNNING"
    );
    return hasNonTerminal ? 5000 : false;
}

/**
 * Poll cadence for a PAGED execution list: the row-driven cadence, spaced by the number of loaded
 * pages.
 *
 * One poll of an infinite query re-reads every page loaded so far in a single pass, and each list
 * request resolves its rows' assets and evaluates them against the caller's constraints. Spacing the
 * ticks by the page count holds the request rate at one page per cadence however deep the reader has
 * paged, so watching a run in progress costs the same whether the board shows 50 rows or 500.
 */
export function computeListRefetchInterval(rows: any[], pageCount: number): number | false {
    const base = computeRefetchInterval(rows);
    if (base === false) return false;
    return base * Math.max(1, pageCount);
}

// Helper to throw on service [false, msg] tuple for query error state
async function callService<T>(serviceFn: () => Promise<[boolean, T | string]>): Promise<T> {
    const [ok, data] = await serviceFn();
    if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
    return data as T;
}

// ============================================================================
// PIPELINE HOOKS
// ============================================================================

type PipelineListPage = { Items: Pipeline[]; NextToken?: string };

/**
 * Paginated pipeline list for the list view — consumes the backend's server-side pagination
 * (pageSize/startingToken -> NextToken) via useInfiniteQuery + "Load more". Flatten
 * data.pages for rendering. For the COMPLETE set (ref resolution, pickers) use useAllPipelines.
 */
export function usePipelines(databaseId?: string, includeArchived?: boolean, pageSize = 50) {
    return useInfiniteQuery({
        queryKey: qk.pipelines(databaseId, { includeArchived, pageSize }),
        queryFn: async ({ pageParam }: { pageParam?: string }) => {
            const params: Record<string, string> = { pageSize: String(pageSize) };
            if (includeArchived) params.includeArchived = "true";
            if (pageParam) params.startingToken = pageParam;
            return callService<PipelineListPage>(() =>
                pipelineService.listPipelines(databaseId, params)
            );
        },
        getNextPageParam: (lastPage: PipelineListPage) => lastPage.NextToken,
        initialPageParam: undefined as string | undefined,
    });
}

/** Complete pipeline set (drains all server pages). For ref resolution / pickers, not the list view. */
export function useAllPipelines(databaseId?: string, includeArchived?: boolean, enabled = true) {
    return useQuery({
        queryKey: [...qk.pipelines(databaseId, { includeArchived }), "all"],
        queryFn: () =>
            callService<Pipeline[]>(() =>
                pipelineService.listAllPipelines(databaseId, includeArchived)
            ),
        enabled,
    });
}

export function usePipeline(databaseId: string, pipelineId: string) {
    return useQuery({
        queryKey: qk.pipeline(databaseId, pipelineId),
        queryFn: () =>
            callService<Pipeline>(() => pipelineService.getPipeline(databaseId, pipelineId)),
        enabled: !!databaseId && !!pipelineId,
    });
}

export function useCreatePipeline() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (body: PipelineCreateRequest) =>
            callService(() => pipelineService.createPipeline(body)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["pipelines"] });
        },
    });
}

export function useUpdatePipeline() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({
            databaseId,
            pipelineId,
            body,
        }: {
            databaseId: string;
            pipelineId: string;
            body: Partial<Pipeline>;
        }) => callService(() => pipelineService.updatePipeline(databaseId, pipelineId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: ["pipelines"] });
            queryClient.invalidateQueries({
                queryKey: qk.pipeline(vars.databaseId, vars.pipelineId),
            });
        },
    });
}

export function useArchivePipeline() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ databaseId, pipelineId }: { databaseId: string; pipelineId: string }) =>
            callService(() => pipelineService.archivePipeline(databaseId, pipelineId)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["pipelines"] });
        },
    });
}

// ============================================================================
// TEMPLATE HOOKS
// ============================================================================

export function useTemplates(databaseId: string, pipelineId: string) {
    return useQuery({
        queryKey: qk.templates(databaseId, pipelineId),
        queryFn: () =>
            callService<Template[]>(() => pipelineService.listTemplates(databaseId, pipelineId)),
        enabled: !!databaseId && !!pipelineId,
    });
}

/**
 * Single template, including the tagSchema and the fully rehydrated configBody/webFormJson.
 * The templates list omits the tag schema and blanks S3-offloaded bodies, so any caller that
 * writes a template back must load it through here.
 */
export function useTemplate(databaseId: string, pipelineId: string, templateId: string) {
    return useQuery({
        queryKey: qk.template(databaseId, pipelineId, templateId),
        queryFn: () =>
            callService<Template>(() =>
                pipelineService.getTemplate(databaseId, pipelineId, templateId)
            ),
        enabled: !!databaseId && !!pipelineId && !!templateId,
    });
}

/**
 * Freshness window for a wizard session's prefetched templates.
 *
 * Long enough that stepping around inside one wizard session never re-fetches, while the unmount
 * cleanup — not expiry — is what prevents a later opening from reusing this session's data.
 */
const WIZARD_SESSION_STALE_TIME = 5 * 60 * 1000;

/**
 * Warm the template caches for a set of pipelines before their wizard step is reached, for the
 * lifetime of ONE wizard session.
 *
 * A wizard's per-step queries only mount when that step renders, so each step paid its own network
 * latency at the moment the user arrived — the pipeline step sat empty for seconds while its template
 * list loaded, with no indication anything was happening. The pipeline list is known as soon as the
 * workflow resolves, so the lists are fetched while the user is still on the Input step.
 *
 * Deliberately NOT a durable cache. On unmount every entry this hook created is removed, so a later
 * opening of the wizard re-reads the templates rather than showing a snapshot from an earlier session.
 * Templates are editable, and a stale body silently becomes the config a run is launched with — worth
 * one fetch per open. Only keys this hook actually created are removed, so a template being viewed
 * elsewhere in the app is left alone.
 *
 * `prefetchQuery` (not `fetchQuery`) so an in-flight or freshly-written entry is not duplicated, and
 * it never throws into the caller: a prefetch is an optimization, and a failure must degrade to the
 * step's own query reporting the error rather than breaking the wizard.
 *
 * Writes the SAME query keys `useTemplates` / `useTemplate` read, so the step finds the data already
 * there instead of issuing a second request.
 *
 * `defaultTemplateId` additionally warms the single-template detail (tagSchema + rehydrated
 * configBody), which is what the step renders its form from. That matters most in an edit or re-run
 * flow, where the template is already chosen and would otherwise be a second serial fetch after the
 * list arrives.
 */
export function usePrefetchPipelineTemplates(
    targets: Array<{ databaseId: string; pipelineId: string; defaultTemplateId?: string }>
) {
    const queryClient = useQueryClient();
    // Serialized so the effect re-runs when the actual set changes, not on every render — the array
    // is rebuilt by the caller's useMemo and would otherwise be a new reference each time.
    const signature = JSON.stringify(
        targets.map((t) => [t.databaseId, t.pipelineId, t.defaultTemplateId || ""])
    );
    // Every key this hook created, so unmount removes exactly those and nothing else. A ref rather
    // than state: it must survive re-renders without causing one, and the cleanup reads the
    // accumulated set from every effect run, not just the last.
    const createdKeys = useRef<Array<readonly unknown[]>>([]);

    useEffect(() => {
        const list: typeof targets = JSON.parse(signature).map(
            ([databaseId, pipelineId, defaultTemplateId]: string[]) => ({
                databaseId,
                pipelineId,
                defaultTemplateId,
            })
        );
        for (const { databaseId, pipelineId, defaultTemplateId } of list) {
            if (!databaseId || !pipelineId) continue;
            const listKey = qk.templates(databaseId, pipelineId);
            createdKeys.current.push(listKey);
            queryClient
                .prefetchQuery({
                    queryKey: listKey,
                    queryFn: () =>
                        callService<Template[]>(() =>
                            pipelineService.listTemplates(databaseId, pipelineId)
                        ),
                    // Fresh for this session: without it, prefetchQuery treats the entry it just
                    // wrote as stale and re-requests on every wizard render, so stepping back and
                    // forth would re-fetch every pipeline's templates each time.
                    staleTime: WIZARD_SESSION_STALE_TIME,
                })
                .catch(() => undefined);
            if (defaultTemplateId) {
                const detailKey = qk.template(databaseId, pipelineId, defaultTemplateId);
                createdKeys.current.push(detailKey);
                queryClient
                    .prefetchQuery({
                        queryKey: detailKey,
                        queryFn: () =>
                            callService<Template>(() =>
                                pipelineService.getTemplate(
                                    databaseId,
                                    pipelineId,
                                    defaultTemplateId
                                )
                            ),
                        staleTime: WIZARD_SESSION_STALE_TIME,
                    })
                    .catch(() => undefined);
            }
        }
    }, [signature, queryClient]);

    // Drop this session's entries when the wizard closes. Without this the next opening would render
    // from the previous session's snapshot, which for an edited template means launching a run with a
    // config body the user is no longer looking at.
    useEffect(() => {
        const keys = createdKeys.current;
        return () => {
            for (const queryKey of keys) {
                queryClient.removeQueries({ queryKey, exact: true });
            }
            keys.length = 0;
        };
    }, [queryClient]);
}

export function useTemplateMutations() {
    const queryClient = useQueryClient();

    // The pipeline detail and list responses embed templates/templateCount, so a template write
    // invalidates them alongside the template caches.
    const invalidateTemplateScopes = (databaseId: string, pipelineId: string) => {
        queryClient.invalidateQueries({ queryKey: qk.templates(databaseId, pipelineId) });
        queryClient.invalidateQueries({ queryKey: qk.pipeline(databaseId, pipelineId) });
        queryClient.invalidateQueries({ queryKey: ["pipelines"] });
    };

    const createTemplate = useMutation({
        mutationFn: ({
            databaseId,
            pipelineId,
            body,
        }: {
            databaseId: string;
            pipelineId: string;
            body: Template;
        }) => callService(() => pipelineService.createTemplate(databaseId, pipelineId, body)),
        onSuccess: (_, vars) => {
            invalidateTemplateScopes(vars.databaseId, vars.pipelineId);
        },
    });

    const updateTemplate = useMutation({
        mutationFn: ({
            databaseId,
            pipelineId,
            templateId,
            body,
        }: {
            databaseId: string;
            pipelineId: string;
            templateId: string;
            body: Partial<Template>;
        }) =>
            callService(() =>
                pipelineService.updateTemplate(databaseId, pipelineId, templateId, body)
            ),
        onSuccess: (_, vars) => {
            invalidateTemplateScopes(vars.databaseId, vars.pipelineId);
            queryClient.invalidateQueries({
                queryKey: qk.template(vars.databaseId, vars.pipelineId, vars.templateId),
            });
        },
    });

    const deleteTemplate = useMutation({
        mutationFn: ({
            databaseId,
            pipelineId,
            templateId,
        }: {
            databaseId: string;
            pipelineId: string;
            templateId: string;
        }) => callService(() => pipelineService.deleteTemplate(databaseId, pipelineId, templateId)),
        onSuccess: (_, vars) => {
            invalidateTemplateScopes(vars.databaseId, vars.pipelineId);
            queryClient.invalidateQueries({
                queryKey: qk.template(vars.databaseId, vars.pipelineId, vars.templateId),
            });
        },
    });

    return { createTemplate, updateTemplate, deleteTemplate };
}

// ============================================================================
// WORKFLOW HOOKS
// ============================================================================

type WorkflowListPage = { Items: Workflow[]; NextToken?: string };

/**
 * Paginated workflow list for the list view — consumes the backend's server-side pagination
 * via useInfiniteQuery + "Load more". Flatten data.pages for rendering.
 */
export function useWorkflows(databaseId?: string, includeArchived?: boolean, pageSize = 50) {
    return useInfiniteQuery({
        queryKey: qk.workflows(databaseId, { includeArchived, pageSize }),
        queryFn: async ({ pageParam }: { pageParam?: string }) => {
            const params: Record<string, string> = { pageSize: String(pageSize) };
            if (includeArchived) params.includeArchived = "true";
            if (pageParam) params.startingToken = pageParam;
            return callService<WorkflowListPage>(() =>
                workflowService.listWorkflows(databaseId, params)
            );
        },
        getNextPageParam: (lastPage: WorkflowListPage) => lastPage.NextToken,
        initialPageParam: undefined as string | undefined,
    });
}

/** Complete workflow set (drains all server pages). For pickers / ref resolution, not the list view. */
export function useAllWorkflows(databaseId?: string, includeArchived?: boolean, enabled = true) {
    return useQuery({
        queryKey: [...qk.workflows(databaseId, { includeArchived }), "all"],
        queryFn: () =>
            callService<Workflow[]>(() =>
                workflowService.listAllWorkflows(databaseId, includeArchived)
            ),
        // `enabled` lets a caller skip a redundant scope — e.g. the GLOBAL catalog when the unscoped
        // list already includes it.
        enabled,
    });
}

export function useWorkflow(databaseId: string, workflowId: string) {
    return useQuery({
        queryKey: qk.workflow(databaseId, workflowId),
        queryFn: () =>
            callService<Workflow>(() => workflowService.getWorkflow(databaseId, workflowId)),
        enabled: !!databaseId && !!workflowId,
    });
}

export function useWorkflowMutations() {
    const queryClient = useQueryClient();

    const createWorkflow = useMutation({
        mutationFn: (body: WorkflowCreateRequest) =>
            callService(() => workflowService.createWorkflow(body)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["workflows"] });
        },
    });

    const updateWorkflow = useMutation({
        mutationFn: ({
            databaseId,
            workflowId,
            body,
        }: {
            databaseId: string;
            workflowId: string;
            body: Partial<Workflow>;
        }) => callService(() => workflowService.updateWorkflow(databaseId, workflowId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: ["workflows"] });
            queryClient.invalidateQueries({
                queryKey: qk.workflow(vars.databaseId, vars.workflowId),
            });
        },
    });

    const archiveWorkflow = useMutation({
        mutationFn: ({ databaseId, workflowId }: { databaseId: string; workflowId: string }) =>
            callService(() => workflowService.archiveWorkflow(databaseId, workflowId)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["workflows"] });
        },
    });

    return { createWorkflow, updateWorkflow, archiveWorkflow };
}

// ============================================================================
// TRIGGER HOOKS
// ============================================================================

export function useTriggers(databaseId: string, workflowId: string) {
    return useQuery({
        queryKey: qk.triggers(databaseId, workflowId),
        queryFn: () =>
            callService<WorkflowTrigger[]>(() =>
                workflowService.listTriggers(databaseId, workflowId)
            ),
        enabled: !!databaseId && !!workflowId,
    });
}

// ============================================================================
// EXECUTION HOOKS
// ============================================================================

type ExecutionListResponse = { Items: Execution[]; NextToken?: string };

export function useExecutions(scope: ExecutionScope, filters?: Record<string, string>, opts?: any) {
    return useInfiniteQuery({
        queryKey: qk.executions(scope, filters),
        queryFn: async ({ pageParam }: { pageParam?: string }) => {
            const params: Record<string, string> = { ...filters, pageSize: "50" };
            if (pageParam) {
                params.startingToken = pageParam;
            }

            if (scope.kind === "global") {
                const [ok, data] = await executionService.listExecutionsGlobal(params);
                if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
                return data as ExecutionListResponse;
            } else if (scope.kind === "workflow") {
                // The global-list endpoint filters a workflow by its composite key: workflowId plus
                // workflowDatabaseId (workflow ids are unique only within a database).
                const workflowParams = {
                    ...params,
                    workflowId: scope.workflowId,
                    workflowDatabaseId: scope.databaseId,
                };
                const [ok, data] = await executionService.listExecutionsGlobal(workflowParams);
                if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
                return data as ExecutionListResponse;
            } else {
                // Asset scope
                const [ok, data] = await executionService.listExecutionsForAsset(
                    scope.databaseId,
                    scope.assetId,
                    params
                );
                if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
                return data as ExecutionListResponse;
            }
        },
        getNextPageParam: (lastPage: ExecutionListResponse) => lastPage.NextToken,
        initialPageParam: undefined as string | undefined,
        // A tick re-reads every loaded page in one pass, so the cadence is spaced by the page count to
        // keep the request rate at one page per cadence however far the reader has paged. Loaded pages
        // are kept: bounding the fetch to the first page instead would discard the rest on every tick.
        // A hidden tab skips the tick entirely (refetchIntervalInBackground defaults off), so a board
        // left open in a background tab issues nothing until it is looked at again.
        refetchInterval: (query: any) => {
            const pages: ExecutionListResponse[] = query.state.data?.pages ?? [];
            const allRows = pages.flatMap((p: ExecutionListResponse) => p.Items);
            return computeListRefetchInterval(allRows, pages.length);
        },
        ...opts,
    });
}

export function useExecutionDetails(executionId: string) {
    return useQuery({
        queryKey: qk.execution(executionId),
        queryFn: () =>
            callService<ExecutionDetail>(() => executionService.getExecutionDetails(executionId)),
        enabled: !!executionId,
        // Poll while the run is still going, on the same 5s cadence as the lists. The list views
        // auto-advanced but this page did not, so opening a RUNNING execution to watch it finish
        // showed a frozen status until the user reloaded — the one place someone is most likely to be
        // waiting. Stops on its own once the status is terminal, so a finished execution is not polled
        // forever.
        refetchInterval: (query: any) =>
            computeRefetchInterval(query.state.data ? [query.state.data] : []),
    });
}

/** Rows per request against the paged detail-metadata route. The backend clamps at 500. */
export const DETAIL_METADATA_PAGE_SIZE = 200;

/**
 * One of an execution's metadata collections, read through the paged route instead of the bounded
 * copy the details view embeds.
 *
 * `useInfiniteQuery` so each server page is kept and the loaded rows accumulate — the detail page
 * renders the union of `data.pages` and pages over it locally, fetching the next server page only when
 * the reader reaches the end of what is loaded. `NextToken` is absent on the last page, so
 * `getNextPageParam` returning undefined is what marks the collection fully retrieved.
 *
 * Disabled until a caller opts in: the collections are only re-read when the details response reported
 * them truncated, so an execution whose metadata fits inline costs no extra request.
 */
export function useExecutionDetailMetadata(
    executionId: string,
    collection: DetailMetadataCollection,
    enabled: boolean
) {
    return useInfiniteQuery({
        queryKey: qk.executionDetailMetadata(executionId, collection),
        queryFn: async ({ pageParam }: { pageParam?: string }) => {
            const params: Record<string, string> = {
                collection,
                pageSize: String(DETAIL_METADATA_PAGE_SIZE),
            };
            if (pageParam) params.startingToken = pageParam;
            return callService<DetailMetadataPage>(() =>
                executionService.getExecutionDetailsMetadata(executionId, params)
            );
        },
        getNextPageParam: (lastPage: DetailMetadataPage) => lastPage.NextToken,
        initialPageParam: undefined as string | undefined,
        enabled: enabled && !!executionId,
    });
}

export function useExecuteWorkflow() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({
            workflowDatabaseId,
            workflowId,
            body,
        }: {
            workflowDatabaseId: string;
            workflowId: string;
            body: any;
        }) =>
            callService(() =>
                executionService.executeWorkflow(workflowDatabaseId, workflowId, body)
            ),
        onSuccess: (_, vars) => {
            // Invalidate all execution list scopes (a new execution could appear in any scope)
            queryClient.invalidateQueries({ queryKey: ["executions"] });
        },
    });
}

export function useExecutionActions() {
    const queryClient = useQueryClient();

    const abortExecution = useMutation({
        mutationFn: ({ executionId, groupId }: { executionId: string; groupId?: string }) =>
            callService(() => executionService.abortExecution(executionId, groupId)),
        onSuccess: (_, vars) => {
            // Narrow invalidation: only the execution list scope and the specific execution detail
            queryClient.invalidateQueries({ queryKey: ["executions"] });
            queryClient.invalidateQueries({ queryKey: qk.execution(vars.executionId) });
        },
    });

    const rerunExecution = useMutation({
        mutationFn: ({
            executionId,
            executionGroupId,
        }: {
            executionId: string;
            executionGroupId?: string;
        }) => callService(() => executionService.rerunExecution(executionId, executionGroupId)),
        onSuccess: (_, vars) => {
            // Re-run creates a NEW execution, so invalidate lists + the original execution detail
            queryClient.invalidateQueries({ queryKey: ["executions"] });
            queryClient.invalidateQueries({ queryKey: qk.execution(vars.executionId) });
        },
    });

    const permanentDeleteExecution = useMutation({
        mutationFn: (executionId: string) =>
            callService(() => executionService.permanentDeleteExecution(executionId)),
        onSuccess: (_, executionId) => {
            // Permanent delete removes the execution, so invalidate lists + its detail view
            queryClient.invalidateQueries({ queryKey: ["executions"] });
            queryClient.invalidateQueries({ queryKey: qk.execution(executionId) });
        },
    });

    return { abortExecution, rerunExecution, permanentDeleteExecution };
}
