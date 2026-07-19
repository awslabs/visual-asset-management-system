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
import type { Pipeline, Template, Workflow, WorkflowTrigger, Execution, ExecutionDetail } from "../types";

// Query key factory for stable, structured keys
export const qk = {
    pipelines: (databaseId?: string, filters?: any) => ["pipelines", databaseId ?? null, filters ?? null] as const,
    pipeline: (databaseId: string, pipelineId: string) => ["pipeline", databaseId, pipelineId] as const,
    templates: (databaseId: string, pipelineId: string) => ["templates", databaseId, pipelineId] as const,
    workflows: (databaseId?: string, filters?: any) => ["workflows", databaseId ?? null, filters ?? null] as const,
    workflow: (databaseId: string, workflowId: string) => ["workflow", databaseId, workflowId] as const,
    triggers: (databaseId: string, workflowId: string) => ["triggers", databaseId, workflowId] as const,
    executions: (scope: ExecutionScope, filters?: any) => ["executions", scope, filters ?? null] as const,
    execution: (executionId: string) => ["execution", executionId] as const,
    allowedRoutes: () => ["allowedRoutes"] as const,
};

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

export function usePipelines(databaseId?: string, includeArchived?: boolean) {
    return useQuery({
        queryKey: qk.pipelines(databaseId, { includeArchived }),
        queryFn: () => callService<Pipeline[]>(() => pipelineService.listPipelines(databaseId, includeArchived)),
    });
}

export function usePipeline(databaseId: string, pipelineId: string) {
    return useQuery({
        queryKey: qk.pipeline(databaseId, pipelineId),
        queryFn: () => callService<Pipeline>(() => pipelineService.getPipeline(databaseId, pipelineId)),
        enabled: !!databaseId && !!pipelineId,
    });
}

export function useCreatePipeline() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (body: Pipeline) => callService(() => pipelineService.createPipeline(body)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["pipelines"] });
        },
    });
}

export function useUpdatePipeline() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ databaseId, pipelineId, body }: { databaseId: string; pipelineId: string; body: Partial<Pipeline> }) =>
            callService(() => pipelineService.updatePipeline(databaseId, pipelineId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: ["pipelines"] });
            queryClient.invalidateQueries({ queryKey: qk.pipeline(vars.databaseId, vars.pipelineId) });
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
        queryFn: () => callService<Template[]>(() => pipelineService.listTemplates(databaseId, pipelineId)),
        enabled: !!databaseId && !!pipelineId,
    });
}

export function useTemplateMutations() {
    const queryClient = useQueryClient();

    const createTemplate = useMutation({
        mutationFn: ({ databaseId, pipelineId, body }: { databaseId: string; pipelineId: string; body: Template }) =>
            callService(() => pipelineService.createTemplate(databaseId, pipelineId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: qk.templates(vars.databaseId, vars.pipelineId) });
        },
    });

    const updateTemplate = useMutation({
        mutationFn: ({ databaseId, pipelineId, templateId, body }: { databaseId: string; pipelineId: string; templateId: string; body: Partial<Template> }) =>
            callService(() => pipelineService.updateTemplate(databaseId, pipelineId, templateId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: qk.templates(vars.databaseId, vars.pipelineId) });
        },
    });

    const archiveTemplate = useMutation({
        mutationFn: ({ databaseId, pipelineId, templateId }: { databaseId: string; pipelineId: string; templateId: string }) =>
            callService(() => pipelineService.archiveTemplate(databaseId, pipelineId, templateId)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: qk.templates(vars.databaseId, vars.pipelineId) });
        },
    });

    return { createTemplate, updateTemplate, archiveTemplate };
}

// ============================================================================
// WORKFLOW HOOKS
// ============================================================================

export function useWorkflows(databaseId?: string, includeArchived?: boolean) {
    return useQuery({
        queryKey: qk.workflows(databaseId, { includeArchived }),
        queryFn: () => callService<Workflow[]>(() => workflowService.listWorkflows(databaseId, includeArchived)),
    });
}

export function useWorkflow(databaseId: string, workflowId: string) {
    return useQuery({
        queryKey: qk.workflow(databaseId, workflowId),
        queryFn: () => callService<Workflow>(() => workflowService.getWorkflow(databaseId, workflowId)),
        enabled: !!databaseId && !!workflowId,
    });
}

export function useWorkflowMutations() {
    const queryClient = useQueryClient();

    const createWorkflow = useMutation({
        mutationFn: (body: Workflow) => callService(() => workflowService.createWorkflow(body)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["workflows"] });
        },
    });

    const updateWorkflow = useMutation({
        mutationFn: ({ databaseId, workflowId, body }: { databaseId: string; workflowId: string; body: Partial<Workflow> }) =>
            callService(() => workflowService.updateWorkflow(databaseId, workflowId, body)),
        onSuccess: (_, vars) => {
            queryClient.invalidateQueries({ queryKey: ["workflows"] });
            queryClient.invalidateQueries({ queryKey: qk.workflow(vars.databaseId, vars.workflowId) });
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
        queryFn: () => callService<WorkflowTrigger[]>(() => workflowService.listTriggers(databaseId, workflowId)),
        enabled: !!databaseId && !!workflowId,
    });
}

// ============================================================================
// EXECUTION HOOKS
// ============================================================================

type ExecutionListResponse = { Items: Execution[]; NextToken?: string };

export function useExecutions(
    scope: ExecutionScope,
    filters?: Record<string, string>,
    opts?: any
) {
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
                const workflowParams = { ...params, workflowId: scope.workflowId, databaseId: scope.databaseId };
                const [ok, data] = await executionService.listExecutionsGlobal(workflowParams);
                if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
                return data as ExecutionListResponse;
            } else {
                // Asset scope
                const [ok, data] = await executionService.listExecutionsForAsset(scope.databaseId, scope.assetId, params);
                if (!ok) throw new Error(typeof data === "string" ? data : "Service call failed");
                return data as ExecutionListResponse;
            }
        },
        getNextPageParam: (lastPage: ExecutionListResponse) => lastPage.NextToken,
        initialPageParam: undefined as string | undefined,
        refetchInterval: (query: any) => {
            // Compute refetch interval from all pages
            const allRows = query.state.data?.pages.flatMap((p: ExecutionListResponse) => p.Items) ?? [];
            return computeRefetchInterval(allRows);
        },
        ...opts,
    });
}

export function useExecutionDetails(executionId: string) {
    return useQuery({
        queryKey: qk.execution(executionId),
        queryFn: () => callService<ExecutionDetail>(() => executionService.getExecutionDetails(executionId)),
        enabled: !!executionId,
    });
}

export function useExecuteWorkflow() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ workflowDatabaseId, workflowId, body }: { workflowDatabaseId: string; workflowId: string; body: any }) =>
            callService(() => executionService.executeWorkflow(workflowDatabaseId, workflowId, body)),
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
        mutationFn: ({ executionId, executionGroupId }: { executionId: string; executionGroupId?: string }) =>
            callService(() => executionService.rerunExecution(executionId, executionGroupId)),
        onSuccess: (_, vars) => {
            // Re-run creates a NEW execution, so invalidate lists + the original execution detail
            queryClient.invalidateQueries({ queryKey: ["executions"] });
            queryClient.invalidateQueries({ queryKey: qk.execution(vars.executionId) });
        },
    });

    const permanentDeleteExecution = useMutation({
        mutationFn: (executionId: string) => callService(() => executionService.permanentDeleteExecution(executionId)),
        onSuccess: (_, executionId) => {
            // Permanent delete removes the execution, so invalidate lists + its detail view
            queryClient.invalidateQueries({ queryKey: ["executions"] });
            queryClient.invalidateQueries({ queryKey: qk.execution(executionId) });
        },
    });

    return { abortExecution, rerunExecution, permanentDeleteExecution };
}
