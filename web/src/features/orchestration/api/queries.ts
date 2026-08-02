/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

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
export function useAllWorkflows(databaseId?: string, includeArchived?: boolean) {
    return useQuery({
        queryKey: [...qk.workflows(databaseId, { includeArchived }), "all"],
        queryFn: () =>
            callService<Workflow[]>(() =>
                workflowService.listAllWorkflows(databaseId, includeArchived)
            ),
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
        refetchInterval: (query: any) => {
            // Compute refetch interval from all pages
            const allRows =
                query.state.data?.pages.flatMap((p: ExecutionListResponse) => p.Items) ?? [];
            return computeRefetchInterval(allRows);
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
