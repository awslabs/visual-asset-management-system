/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo } from "react";
import Dialog from "../components/Dialog";
import Stepper from "../components/Stepper";
import WizardInputStage from "./WizardInputStage";
import WizardPipelineStage from "./WizardPipelineStage";
import WizardReviewStage from "./WizardReviewStage";
import { useWorkflow, usePipelines, useExecuteWorkflow, useTemplates } from "../api/queries";
import { resolvePipelineParams } from "./resolveTemplate";
import type { Workflow, ExecuteInputFile, ExecuteRequest, PipelineExecutionParameters } from "../types";

interface ExecuteWizardProps {
    open: boolean;
    onClose: () => void;
    workflow: Workflow;
    databaseId: string;
    presetAsset?: { databaseId: string; assetId: string };
}

export interface PipelineStageData {
    pipelineId: string;
    templateId?: string;
    tags: { key: string; value: any }[];
    customTemplateOverride?: string;
    customEditedBody?: string;
}

const ExecuteWizard: React.FC<ExecuteWizardProps> = ({
    open,
    onClose,
    workflow,
    databaseId,
    presetAsset,
}) => {
    const { data: workflowData } = useWorkflow(workflow.databaseId, workflow.workflowId);
    const effectiveWorkflow = workflowData || workflow;

    // Fetch all pipelines for this workflow
    const pipelineIds = effectiveWorkflow.specifiedPipelines.map((p) => p.pipelineId);
    const pipelineDbIds = effectiveWorkflow.specifiedPipelines.map(
        (p) => p.pipelineDatabaseId || databaseId
    );
    const { data: allPipelines } = usePipelines();

    const pipelines = useMemo(() => {
        if (!allPipelines) return [];
        return pipelineIds
            .map((id, idx) => {
                const dbId = pipelineDbIds[idx];
                return allPipelines.find((p) => p.pipelineId === id && p.databaseId === dbId);
            })
            .filter(Boolean);
    }, [allPipelines, pipelineIds, pipelineDbIds]);

    // Fetch templates for all pipelines (must call hooks unconditionally)
    const templateQueries = pipelines.map((pipeline) => {
        // eslint-disable-next-line react-hooks/rules-of-hooks
        return useTemplates(pipeline?.databaseId || "", pipeline?.pipelineId || "");
    });

    const executeWorkflow = useExecuteWorkflow();

    // Input stage data
    const [inputFiles, setInputFiles] = useState<ExecuteInputFile[]>([]);
    const [outputAssetId, setOutputAssetId] = useState<string | undefined>(undefined);
    const [outputDatabaseId, setOutputDatabaseId] = useState<string | undefined>(undefined);

    // Pipeline stage data (one entry per pipeline)
    const [pipelineData, setPipelineData] = useState<Record<string, PipelineStageData>>({});

    // Current stage
    const [currentStageId, setCurrentStageId] = useState<string>("input");

    // Build step list: Input -> Pipeline1 -> Pipeline2 -> ... -> Review
    const steps = useMemo(() => {
        const stageSteps = [
            { id: "input", label: "Input" },
            ...effectiveWorkflow.specifiedPipelines.map((p, idx) => ({
                id: `pipeline-${idx}`,
                label: pipelines[idx]?.pipelineName || `Pipeline ${idx + 1}`,
            })),
            { id: "review", label: "Review" },
        ];
        return stageSteps;
    }, [effectiveWorkflow.specifiedPipelines, pipelines]);

    const currentIndex = steps.findIndex((s) => s.id === currentStageId);

    // Compute validation errors for all pipelines
    const validationErrors = useMemo(() => {
        const errors: Record<string, string[]> = {};

        effectiveWorkflow.specifiedPipelines.forEach((ref, idx) => {
            const pipeline = pipelines[idx];
            if (!pipeline) {
                errors[ref.pipelineId] = ["Pipeline not found"];
                return;
            }

            const data = pipelineData[ref.pipelineId] || { pipelineId: ref.pipelineId, tags: [] };
            const templates = templateQueries[idx]?.data;
            const template = templates?.find((t) => t.templateId === data.templateId);

            const result = resolvePipelineParams({
                pipeline,
                template,
                templateId: data.templateId,
                tags: data.tags,
                customTemplateOverride: data.customTemplateOverride,
                customEditedBody: data.customEditedBody,
            });

            errors[ref.pipelineId] = result.errors;
        });

        return errors;
    }, [effectiveWorkflow.specifiedPipelines, pipelines, pipelineData, templateQueries]);

    const hasValidationErrors = Object.values(validationErrors).some((errs) => errs.length > 0);

    const handleNext = () => {
        if (currentIndex < steps.length - 1) {
            setCurrentStageId(steps[currentIndex + 1].id);
        }
    };

    const handleBack = () => {
        if (currentIndex > 0) {
            setCurrentStageId(steps[currentIndex - 1].id);
        }
    };

    const handleLaunch = async () => {
        // Build ExecuteRequest
        const pipelineExecutionParameters: Record<string, PipelineExecutionParameters> = {};

        effectiveWorkflow.specifiedPipelines.forEach((ref, idx) => {
            const data = pipelineData[ref.pipelineId] || {
                pipelineId: ref.pipelineId,
                tags: [],
            };

            const params: PipelineExecutionParameters = {
                templateId: data.templateId,
                templateTags: data.tags,
            };

            if (data.customTemplateOverride) {
                params.customTemplateOverride = data.customTemplateOverride;
            }

            pipelineExecutionParameters[ref.pipelineId] = params;
        });

        const body: ExecuteRequest = {
            inputFiles,
            outputAssetId,
            outputDatabaseId,
            pipelineExecutionParameters,
            triggerType: "manual",
        };

        try {
            const result = await executeWorkflow.mutateAsync({
                workflowDatabaseId: effectiveWorkflow.databaseId,
                workflowId: effectiveWorkflow.workflowId,
                body,
            });

            // Surface warnings if any
            if (result.warnings && result.warnings.length > 0) {
                console.log("Execution warnings:", result.warnings);
            }

            onClose();
        } catch (err) {
            console.error("Execution failed:", err);
        }
    };

    const renderStage = () => {
        if (currentStageId === "input") {
            return (
                <WizardInputStage
                    workflow={effectiveWorkflow}
                    databaseId={databaseId}
                    presetAsset={presetAsset}
                    inputFiles={inputFiles}
                    outputAssetId={outputAssetId}
                    outputDatabaseId={outputDatabaseId}
                    onInputFilesChange={setInputFiles}
                    onOutputAssetIdChange={setOutputAssetId}
                    onOutputDatabaseIdChange={setOutputDatabaseId}
                />
            );
        }

        if (currentStageId === "review") {
            return (
                <WizardReviewStage
                    workflow={effectiveWorkflow}
                    pipelines={pipelines}
                    pipelineData={pipelineData}
                    inputFiles={inputFiles}
                    outputAssetId={outputAssetId}
                    outputDatabaseId={outputDatabaseId}
                    validationErrors={validationErrors}
                />
            );
        }

        // Pipeline stage
        const pipelineIndex = parseInt(currentStageId.replace("pipeline-", ""), 10);
        const pipeline = pipelines[pipelineIndex];
        const ref = effectiveWorkflow.specifiedPipelines[pipelineIndex];

        if (!pipeline) {
            return <div className="text-red-600">Pipeline not found</div>;
        }

        return (
            <WizardPipelineStage
                workflow={effectiveWorkflow}
                pipeline={pipeline}
                pipelineRef={ref}
                data={pipelineData[ref.pipelineId]}
                onChange={(data) => {
                    setPipelineData((prev) => ({
                        ...prev,
                        [ref.pipelineId]: data,
                    }));
                }}
            />
        );
    };

    const canNavigateNext = () => {
        // No validation, just allow navigation
        return true;
    };

    const footer = (
        <div className="flex gap-2">
            {currentIndex > 0 && (
                <button
                    onClick={handleBack}
                    className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                    Back
                </button>
            )}
            {currentIndex < steps.length - 1 && (
                <button
                    onClick={handleNext}
                    disabled={!canNavigateNext()}
                    className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                    Next
                </button>
            )}
            {currentIndex === steps.length - 1 && (
                <button
                    onClick={handleLaunch}
                    disabled={executeWorkflow.isPending || hasValidationErrors}
                    className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                    {executeWorkflow.isPending ? "Launching..." : "Launch"}
                </button>
            )}
        </div>
    );

    return (
        <Dialog
            open={open}
            onOpenChange={onClose}
            title={`Execute ${effectiveWorkflow.workflowName}`}
            footer={footer}
        >
            <div className="space-y-4">
                <Stepper steps={steps} current={currentStageId} />
                <div className="min-h-[400px]">{renderStage()}</div>
            </div>
        </Dialog>
    );
};

export default ExecuteWizard;
