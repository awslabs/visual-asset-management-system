/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { usePipelines, useWorkflow, useWorkflowMutations } from "../api/queries";
import PipelineOrderList from "./PipelineOrderList";
import DagPreview from "./DagPreview";
import { validateWorkflow } from "./workflowValidation";
import type { Workflow, SpecifiedPipelineRef, InputFileArity, ConcurrencyRestriction, OutputLocationType } from "../types";

interface WorkflowBuilderProps {
    mode: "create" | "edit";
    databaseId: string;
    workflowId?: string;
}

const WorkflowBuilder: React.FC<WorkflowBuilderProps> = ({ mode, databaseId, workflowId }) => {
    const navigate = useNavigate();
    const { data: pipelines = [] } = usePipelines(databaseId);
    const { data: workflow } = useWorkflow(databaseId, workflowId || "");
    const { createWorkflow, updateWorkflow } = useWorkflowMutations();

    const [workflowIdValue, setWorkflowIdValue] = useState("");
    const [workflowName, setWorkflowName] = useState("");
    const [category, setCategory] = useState("");
    const [description, setDescription] = useState("");
    const [subDashboardUrl, setSubDashboardUrl] = useState("");
    const [enabled, setEnabled] = useState(true);

    const [inputFileArity, setInputFileArity] = useState<InputFileArity>("one");
    const [assetScope, setAssetScope] = useState<Record<string, boolean>>({});
    const [metadataInputs, setMetadataInputs] = useState<Record<string, boolean>>({});
    const [allowFilters, setAllowFilters] = useState("");
    const [excludeFilters, setExcludeFilters] = useState("");
    const [concurrencyRestriction, setConcurrencyRestriction] = useState<ConcurrencyRestriction>("none");
    const [locationType, setLocationType] = useState<OutputLocationType>("asset");
    const [allowOverride, setAllowOverride] = useState(false);

    const [specifiedPipelines, setSpecifiedPipelines] = useState<SpecifiedPipelineRef[]>([]);

    const [validationErrors, setValidationErrors] = useState<string[]>([]);
    const [validationWarnings, setValidationWarnings] = useState<string[]>([]);
    const [backendWarnings, setBackendWarnings] = useState<string[]>([]);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Load workflow data in edit mode
    useEffect(() => {
        if (mode === "edit" && workflow) {
            setWorkflowIdValue(workflow.workflowId || "");
            setWorkflowName(workflow.workflowName || "");
            setCategory(workflow.category || "");
            setDescription(workflow.description || "");
            setSubDashboardUrl(workflow.subDashboardUrl || "");
            setEnabled(workflow.enabled ?? true);
            setSpecifiedPipelines(workflow.specifiedPipelines || []);

            const sc = workflow.systemConfig || {};
            setInputFileArity(sc.inputFileArity || "one");
            setAssetScope(sc.assetScope || {});
            setMetadataInputs(sc.metadataInputs || {});
            setAllowFilters((sc.inputFileFilters?.allow || []).join(", "));
            setExcludeFilters((sc.inputFileFilters?.exclude || []).join(", "));
            setConcurrencyRestriction(sc.concurrencyRestriction || "none");
            setLocationType(sc.outputTarget?.locationType || "asset");
            setAllowOverride(sc.outputTarget?.allowOverride ?? false);
        }
    }, [mode, workflow]);

    // HARD COUPLING: when locationType is "none", force inputFileArity to "none"
    useEffect(() => {
        if (locationType === "none" && inputFileArity !== "none") {
            setInputFileArity("none");
        }
    }, [locationType, inputFileArity]);

    const assembleWorkflow = useCallback((): Workflow => {
        return {
            databaseId,
            workflowId: workflowIdValue,
            workflowName,
            category,
            description,
            subDashboardUrl,
            enabled,
            specifiedPipelines,
            systemConfig: {
                inputFileArity,
                assetScope,
                metadataInputs,
                inputFileFilters: {
                    allow: allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                    exclude: excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
                },
                concurrencyRestriction,
                outputTarget: { locationType, allowOverride },
            },
        };
    }, [
        databaseId,
        workflowIdValue,
        workflowName,
        category,
        description,
        subDashboardUrl,
        enabled,
        specifiedPipelines,
        inputFileArity,
        assetScope,
        metadataInputs,
        allowFilters,
        excludeFilters,
        concurrencyRestriction,
        locationType,
        allowOverride,
    ]);

    // Run validation on change
    useEffect(() => {
        const assembled = assembleWorkflow();

        const pipelinesById = pipelines.reduce((acc: Record<string, any>, p: any) => {
            const key = `${p.databaseId}:${p.pipelineId}`;
            acc[key] = p;
            return acc;
        }, {});

        const result = validateWorkflow(assembled, pipelinesById);
        setValidationErrors(result.errors);
        setValidationWarnings(result.warnings);
    }, [assembleWorkflow, pipelines]);

    const handleSave = async () => {
        if (validationErrors.length > 0) return;

        setSaving(true);
        setSaveError(null);
        try {
            const body = assembleWorkflow();

            if (mode === "create") {
                const result = await createWorkflow.mutateAsync(body);
                if (result?.warnings) {
                    setBackendWarnings(result.warnings);
                }
                navigate(`/databases/${databaseId}/workflows`);
            } else {
                const result = await updateWorkflow.mutateAsync({ databaseId, workflowId: workflowIdValue, body });
                if (result?.warnings) {
                    setBackendWarnings(result.warnings);
                }
                navigate(`/databases/${databaseId}/workflows`);
            }
        } catch (err: any) {
            console.error("Save failed:", err);
            setSaveError(err?.message || "Failed to save workflow");
        } finally {
            setSaving(false);
        }
    };

    const isArityDisabled = locationType === "none";
    const isSaveDisabled = validationErrors.length > 0 || saving;

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
                                value={workflowIdValue}
                                onChange={(e) => setWorkflowIdValue(e.target.value)}
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
                                value={workflowIdValue}
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
                            value={workflowName}
                            onChange={(e) => setWorkflowName(e.target.value)}
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
                            value={category}
                            onChange={(e) => setCategory(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label htmlFor="description" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Description (optional)
                        </label>
                        <textarea
                            id="description"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
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
                            value={subDashboardUrl}
                            onChange={(e) => setSubDashboardUrl(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                    <div>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={enabled}
                                onChange={(e) => setEnabled(e.target.checked)}
                            />
                            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                {enabled ? "Enabled" : "Disabled"}
                            </span>
                        </label>
                    </div>
                </div>
            </div>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">System Configuration</h2>
                <div className="space-y-4">
                    <div>
                        <label htmlFor="inputFileArity" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Input File Arity
                        </label>
                        {isArityDisabled && (
                            <p className="text-sm text-gray-600 dark:text-gray-400 mb-1">
                                Locked to 'none' when output location is 'none' (results-only workflows require no input files)
                            </p>
                        )}
                        <select
                            id="inputFileArity"
                            value={inputFileArity}
                            onChange={(e) => setInputFileArity(e.target.value as InputFileArity)}
                            disabled={isArityDisabled}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 disabled:opacity-50"
                        >
                            <option value="none">none</option>
                            <option value="one">one</option>
                            <option value="multi">multi</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-2 text-gray-900 dark:text-gray-100">
                            Asset Scope
                        </label>
                        <div className="space-y-1">
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={assetScope.asset || false}
                                    onChange={(e) => setAssetScope({ ...assetScope, asset: e.target.checked })}
                                />
                                <span className="text-sm text-gray-900 dark:text-gray-100">Asset</span>
                            </label>
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={assetScope.pipeline || false}
                                    onChange={(e) => setAssetScope({ ...assetScope, pipeline: e.target.checked })}
                                />
                                <span className="text-sm text-gray-900 dark:text-gray-100">Pipeline</span>
                            </label>
                        </div>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-2 text-gray-900 dark:text-gray-100">
                            Metadata Inputs
                        </label>
                        <div className="space-y-1">
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={metadataInputs.asset || false}
                                    onChange={(e) => setMetadataInputs({ ...metadataInputs, asset: e.target.checked })}
                                />
                                <span className="text-sm text-gray-900 dark:text-gray-100">Asset Metadata</span>
                            </label>
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={metadataInputs.file || false}
                                    onChange={(e) => setMetadataInputs({ ...metadataInputs, file: e.target.checked })}
                                />
                                <span className="text-sm text-gray-900 dark:text-gray-100">File Metadata</span>
                            </label>
                        </div>
                    </div>

                    <div>
                        <label htmlFor="allowFilters" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Input File Filters - Allow (comma-separated)
                        </label>
                        <input
                            id="allowFilters"
                            type="text"
                            value={allowFilters}
                            onChange={(e) => setAllowFilters(e.target.value)}
                            placeholder="e.g., *.jpg, *.png"
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>

                    <div>
                        <label htmlFor="excludeFilters" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Input File Filters - Exclude (comma-separated)
                        </label>
                        <input
                            id="excludeFilters"
                            type="text"
                            value={excludeFilters}
                            onChange={(e) => setExcludeFilters(e.target.value)}
                            placeholder="e.g., *.tmp, *.bak"
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                    </div>

                    <div>
                        <label htmlFor="concurrencyRestriction" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Concurrency Restriction
                        </label>
                        <select
                            id="concurrencyRestriction"
                            value={concurrencyRestriction}
                            onChange={(e) => setConcurrencyRestriction(e.target.value as ConcurrencyRestriction)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        >
                            <option value="none">none</option>
                            <option value="perAsset">perAsset</option>
                            <option value="perInputFile">perInputFile</option>
                        </select>
                    </div>

                    <div>
                        <label htmlFor="locationType" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                            Output Target - Location Type
                        </label>
                        <select
                            id="locationType"
                            value={locationType}
                            onChange={(e) => setLocationType(e.target.value as OutputLocationType)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        >
                            <option value="asset">asset</option>
                            <option value="none">none</option>
                        </select>
                    </div>

                    <div>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={allowOverride}
                                onChange={(e) => setAllowOverride(e.target.checked)}
                            />
                            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                {allowOverride ? "Allow Override" : "No Override"}
                            </span>
                        </label>
                    </div>
                </div>
            </div>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Pipeline Order</h2>
                <div className="space-y-4">
                    <PipelineOrderList
                        value={specifiedPipelines}
                        pipelineOptions={pipelines}
                        templatesByPipeline={{}}
                        onChange={setSpecifiedPipelines}
                    />
                    <DagPreview refs={specifiedPipelines} />
                </div>
            </div>

            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Validation</h2>
                <div className="space-y-4">
                    {saveError && (
                        <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded">
                            <strong>Save Error:</strong> {saveError}
                        </div>
                    )}
                    {validationErrors.length > 0 && (
                        <div className="text-red-700 dark:text-red-400">
                            <strong>Errors (blocking save):</strong>
                            <ul className="list-disc list-inside">
                                {validationErrors.map((e, i) => (
                                    <li key={i}>{e}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                    {validationWarnings.length > 0 && (
                        <div className="text-orange-700 dark:text-orange-400">
                            <strong>Warnings:</strong>
                            <ul className="list-disc list-inside">
                                {validationWarnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                    {backendWarnings.length > 0 && (
                        <div className="text-orange-700 dark:text-orange-400">
                            <strong>Backend Warnings:</strong>
                            <ul className="list-disc list-inside">
                                {backendWarnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                    {validationErrors.length === 0 && validationWarnings.length === 0 && backendWarnings.length === 0 && !saveError && (
                        <div className="text-green-700 dark:text-green-400">All validations passed</div>
                    )}
                </div>
            </div>

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
                    {saving ? "Saving..." : "Save"}
                </button>
            </div>
        </div>
    );
};

export default WorkflowBuilder;
