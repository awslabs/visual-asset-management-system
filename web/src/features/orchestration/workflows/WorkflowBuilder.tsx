/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useReducer, useEffect, useCallback, Suspense } from "react";
import { useNavigate } from "react-router-dom";
import { usePipelines, useWorkflow, useWorkflowMutations, useTemplates } from "../api/queries";
import PipelineOrderList from "./PipelineOrderList";
import WorkflowSystemConfigFields from "./WorkflowSystemConfigFields";
import WorkflowValidationPanel from "./WorkflowValidationPanel";
import { validateWorkflow } from "./workflowValidation";
import type { Workflow, SpecifiedPipelineRef, InputFileArity, ConcurrencyRestriction, OutputLocationType, Template } from "../types";

const DagPreview = React.lazy(() => import("./DagPreview"));

interface WorkflowBuilderProps {
    mode: "create" | "edit";
    databaseId: string;
    workflowId?: string;
}

interface WorkflowFormState {
    workflowIdValue: string;
    workflowName: string;
    category: string;
    description: string;
    subDashboardUrl: string;
    enabled: boolean;
    inputFileArity: InputFileArity;
    assetScope: Record<string, boolean>;
    metadataInputs: Record<string, boolean>;
    allowFilters: string;
    excludeFilters: string;
    concurrencyRestriction: ConcurrencyRestriction;
    locationType: OutputLocationType;
    allowOverride: boolean;
    specifiedPipelines: SpecifiedPipelineRef[];
    templatesByPipeline: Record<string, Template[]>;
    validationErrors: string[];
    validationWarnings: string[];
    backendWarnings: string[];
    saving: boolean;
    saveError: string | null;
}

type WorkflowFormAction =
    | { type: "SET_FIELD"; field: keyof WorkflowFormState; value: any }
    | { type: "LOAD_WORKFLOW"; workflow: Workflow }
    | { type: "SET_TEMPLATES"; key: string; templates: Template[] }
    | { type: "SET_VALIDATION"; errors: string[]; warnings: string[] }
    | { type: "SET_SAVING"; saving: boolean }
    | { type: "SET_SAVE_ERROR"; error: string | null }
    | { type: "SET_BACKEND_WARNINGS"; warnings: string[] };

const initialState: WorkflowFormState = {
    workflowIdValue: "",
    workflowName: "",
    category: "",
    description: "",
    subDashboardUrl: "",
    enabled: true,
    inputFileArity: "one",
    assetScope: {},
    metadataInputs: {},
    allowFilters: "",
    excludeFilters: "",
    concurrencyRestriction: "none",
    locationType: "asset",
    allowOverride: false,
    specifiedPipelines: [],
    templatesByPipeline: {},
    validationErrors: [],
    validationWarnings: [],
    backendWarnings: [],
    saving: false,
    saveError: null,
};

function workflowFormReducer(state: WorkflowFormState, action: WorkflowFormAction): WorkflowFormState {
    switch (action.type) {
        case "SET_FIELD":
            return { ...state, [action.field]: action.value };
        case "LOAD_WORKFLOW": {
            const workflow = action.workflow;
            const sc = workflow.systemConfig || {};
            return {
                ...state,
                workflowIdValue: workflow.workflowId || "",
                workflowName: workflow.workflowName || "",
                category: workflow.category || "",
                description: workflow.description || "",
                subDashboardUrl: workflow.subDashboardUrl || "",
                enabled: workflow.enabled ?? true,
                specifiedPipelines: workflow.specifiedPipelines || [],
                inputFileArity: sc.inputFileArity || "one",
                assetScope: sc.assetScope || {},
                metadataInputs: sc.metadataInputs || {},
                allowFilters: (sc.inputFileFilters?.allow || []).join(", "),
                excludeFilters: (sc.inputFileFilters?.exclude || []).join(", "),
                concurrencyRestriction: sc.concurrencyRestriction || "none",
                locationType: sc.outputTarget?.locationType || "asset",
                allowOverride: sc.outputTarget?.allowOverride ?? false,
            };
        }
        case "SET_TEMPLATES":
            return {
                ...state,
                templatesByPipeline: { ...state.templatesByPipeline, [action.key]: action.templates },
            };
        case "SET_VALIDATION":
            return {
                ...state,
                validationErrors: action.errors,
                validationWarnings: action.warnings,
            };
        case "SET_SAVING":
            return { ...state, saving: action.saving };
        case "SET_SAVE_ERROR":
            return { ...state, saveError: action.error };
        case "SET_BACKEND_WARNINGS":
            return { ...state, backendWarnings: action.warnings };
        default:
            return state;
    }
}

const TemplatesFetcher: React.FC<{
    pipelineDatabaseId: string;
    pipelineId: string;
    onTemplatesLoaded: (key: string, templates: Template[]) => void;
}> = ({ pipelineDatabaseId, pipelineId, onTemplatesLoaded }) => {
    const { data: templates } = useTemplates(pipelineDatabaseId, pipelineId);
    const key = `${pipelineDatabaseId}:${pipelineId}`;

    useEffect(() => {
        if (templates) {
            onTemplatesLoaded(key, templates);
        }
    }, [templates, key, onTemplatesLoaded]);

    return null;
};

const WorkflowBuilder: React.FC<WorkflowBuilderProps> = ({ mode, databaseId, workflowId }) => {
    const navigate = useNavigate();
    const { data: pipelines = [] } = usePipelines(databaseId);
    const { data: workflow } = useWorkflow(databaseId, workflowId || "");
    const { createWorkflow, updateWorkflow } = useWorkflowMutations();

    const [state, dispatch] = useReducer(workflowFormReducer, initialState);

    const handleTemplatesLoaded = useCallback((key: string, templates: Template[]) => {
        dispatch({ type: "SET_TEMPLATES", key, templates });
    }, []);

    useEffect(() => {
        if (mode === "edit" && workflow) {
            dispatch({ type: "LOAD_WORKFLOW", workflow });
        }
    }, [mode, workflow]);

    // HARD COUPLING: when locationType is "none", force inputFileArity to "none"
    useEffect(() => {
        if (state.locationType === "none" && state.inputFileArity !== "none") {
            dispatch({ type: "SET_FIELD", field: "inputFileArity", value: "none" });
        }
    }, [state.locationType, state.inputFileArity]);

    const assembleWorkflow = useCallback((): Workflow => {
        return {
            databaseId,
            workflowId: state.workflowIdValue,
            workflowName: state.workflowName,
            category: state.category,
            description: state.description,
            subDashboardUrl: state.subDashboardUrl,
            enabled: state.enabled,
            specifiedPipelines: state.specifiedPipelines,
            systemConfig: {
                inputFileArity: state.inputFileArity,
                assetScope: state.assetScope,
                metadataInputs: state.metadataInputs,
                inputFileFilters: {
                    allow: state.allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                    exclude: state.excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
                },
                concurrencyRestriction: state.concurrencyRestriction,
                outputTarget: { locationType: state.locationType, allowOverride: state.allowOverride },
            },
        };
    }, [
        databaseId,
        state.workflowIdValue,
        state.workflowName,
        state.category,
        state.description,
        state.subDashboardUrl,
        state.enabled,
        state.specifiedPipelines,
        state.inputFileArity,
        state.assetScope,
        state.metadataInputs,
        state.allowFilters,
        state.excludeFilters,
        state.concurrencyRestriction,
        state.locationType,
        state.allowOverride,
    ]);

    useEffect(() => {
        const assembled = assembleWorkflow();

        const pipelinesById = pipelines.reduce((acc: Record<string, any>, p: any) => {
            const key = `${p.databaseId}:${p.pipelineId}`;
            acc[key] = p;
            return acc;
        }, {});

        const result = validateWorkflow(assembled, pipelinesById);
        dispatch({ type: "SET_VALIDATION", errors: result.errors, warnings: result.warnings });
    }, [assembleWorkflow, pipelines]);

    const handleSave = async () => {
        if (state.validationErrors.length > 0) return;

        dispatch({ type: "SET_SAVING", saving: true });
        dispatch({ type: "SET_SAVE_ERROR", error: null });
        try {
            const body = assembleWorkflow();

            if (mode === "create") {
                const result = await createWorkflow.mutateAsync(body);
                if (result?.warnings) {
                    dispatch({ type: "SET_BACKEND_WARNINGS", warnings: result.warnings });
                }
                navigate(`/databases/${databaseId}/workflows`);
            } else {
                const result = await updateWorkflow.mutateAsync({ databaseId, workflowId: state.workflowIdValue, body });
                if (result?.warnings) {
                    dispatch({ type: "SET_BACKEND_WARNINGS", warnings: result.warnings });
                }
                navigate(`/databases/${databaseId}/workflows`);
            }
        } catch (err: any) {
            console.error("Save failed:", err);
            dispatch({ type: "SET_SAVE_ERROR", error: err?.message || "Failed to save workflow" });
        } finally {
            dispatch({ type: "SET_SAVING", saving: false });
        }
    };

    const isArityDisabled = state.locationType === "none";
    const isSaveDisabled = state.validationErrors.length > 0 || state.saving;

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
                {mode === "create" ? "Create Workflow" : "Edit Workflow"}
            </h1>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Basic Information</h2>
                <div className="space-y-4">
                    {mode === "create" && (
                        <div>
                            <label htmlFor="workflowId" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                                Workflow ID
                            </label>
                            <p className="text-sm text-gray-600 dark:text-gray-400 mb-1">Unique identifier (3-63 chars, letters, numbers, hyphens, underscores)</p>
                            <input
                                id="workflowId"
                                type="text"
                                value={state.workflowIdValue}
                                onChange={(e) => dispatch({ type: "SET_FIELD", field: "workflowIdValue", value: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                    )}
                    {mode === "edit" && (
                        <div>
                            <label htmlFor="workflowId" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                                Workflow ID
                            </label>
                            <input
                                id="workflowId"
                                type="text"
                                value={state.workflowIdValue}
                                disabled
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-gray-100 dark:bg-gray-700 text-gray-900 dark:text-gray-100 opacity-50"
                            />
                        </div>
                    )}
                    <div>
                        <label htmlFor="workflowName" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Workflow Name
                        </label>
                        <input
                            id="workflowName"
                            type="text"
                            value={state.workflowName}
                            onChange={(e) => dispatch({ type: "SET_FIELD", field: "workflowName", value: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label htmlFor="category" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Category (optional)
                        </label>
                        <input
                            id="category"
                            type="text"
                            value={state.category}
                            onChange={(e) => dispatch({ type: "SET_FIELD", field: "category", value: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label htmlFor="description" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Description (optional)
                        </label>
                        <textarea
                            id="description"
                            value={state.description}
                            onChange={(e) => dispatch({ type: "SET_FIELD", field: "description", value: e.target.value })}
                            rows={3}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label htmlFor="subDashboardUrl" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Sub-Dashboard URL (optional)
                        </label>
                        <input
                            id="subDashboardUrl"
                            type="text"
                            value={state.subDashboardUrl}
                            onChange={(e) => dispatch({ type: "SET_FIELD", field: "subDashboardUrl", value: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={state.enabled}
                                onChange={(e) => dispatch({ type: "SET_FIELD", field: "enabled", value: e.target.checked })}
                            />
                            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                {state.enabled ? "Enabled" : "Disabled"}
                            </span>
                        </label>
                    </div>
                </div>
            </div>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">System Configuration</h2>
                <WorkflowSystemConfigFields
                    inputFileArity={state.inputFileArity}
                    assetScope={state.assetScope}
                    metadataInputs={state.metadataInputs}
                    allowFilters={state.allowFilters}
                    excludeFilters={state.excludeFilters}
                    concurrencyRestriction={state.concurrencyRestriction}
                    locationType={state.locationType}
                    allowOverride={state.allowOverride}
                    isArityDisabled={isArityDisabled}
                    onInputFileArityChange={(value) => dispatch({ type: "SET_FIELD", field: "inputFileArity", value })}
                    onAssetScopeChange={(value) => dispatch({ type: "SET_FIELD", field: "assetScope", value })}
                    onMetadataInputsChange={(value) => dispatch({ type: "SET_FIELD", field: "metadataInputs", value })}
                    onAllowFiltersChange={(value) => dispatch({ type: "SET_FIELD", field: "allowFilters", value })}
                    onExcludeFiltersChange={(value) => dispatch({ type: "SET_FIELD", field: "excludeFilters", value })}
                    onConcurrencyRestrictionChange={(value) => dispatch({ type: "SET_FIELD", field: "concurrencyRestriction", value })}
                    onLocationTypeChange={(value) => dispatch({ type: "SET_FIELD", field: "locationType", value })}
                    onAllowOverrideChange={(value) => dispatch({ type: "SET_FIELD", field: "allowOverride", value })}
                />
            </div>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Pipeline Order</h2>
                <div className="space-y-4">
                    {state.specifiedPipelines.map((ref, idx) => {
                        if (!ref.pipelineId || !ref.pipelineDatabaseId) return null;
                        return (
                            <TemplatesFetcher
                                key={`${ref.pipelineDatabaseId}:${ref.pipelineId}-${idx}`}
                                pipelineDatabaseId={ref.pipelineDatabaseId}
                                pipelineId={ref.pipelineId}
                                onTemplatesLoaded={handleTemplatesLoaded}
                            />
                        );
                    })}
                    <PipelineOrderList
                        value={state.specifiedPipelines}
                        pipelineOptions={pipelines}
                        templatesByPipeline={state.templatesByPipeline}
                        onChange={(value) => dispatch({ type: "SET_FIELD", field: "specifiedPipelines", value })}
                    />
                    {state.specifiedPipelines.length > 0 && (
                        <Suspense fallback={<div className="text-sm text-gray-500 dark:text-gray-400">Loading preview...</div>}>
                            <DagPreview refs={state.specifiedPipelines} />
                        </Suspense>
                    )}
                </div>
            </div>

            <WorkflowValidationPanel
                validationErrors={state.validationErrors}
                validationWarnings={state.validationWarnings}
                backendWarnings={state.backendWarnings}
                saveError={state.saveError}
            />

            <div className="flex justify-end gap-2">
                <button
                    onClick={() => navigate(-1)}
                    className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded hover:bg-gray-300 dark:hover:bg-gray-600"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSave}
                    disabled={isSaveDisabled}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                >
                    {state.saving ? "Saving..." : "Save"}
                </button>
            </div>
        </div>
    );
};

export default WorkflowBuilder;
