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
import { useWorkflow, usePipelines, useExecuteWorkflow } from "../api/queries";
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
    errors: string[];
    params: any;
    mode?: 1 | 2 | 3 | 4 | 5;
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

    // Compute offending (disabled/archived) pipelines
    const offendingPipelines = useMemo(() => {
        const offenders: Array<{ pipelineId: string; pipelineName: string; reason: string }> = [];
        effectiveWorkflow.specifiedPipelines.forEach((ref) => {
            const pipeline = pipelines.find(
                (p) => p?.pipelineId === ref.pipelineId && p?.databaseId === (ref.pipelineDatabaseId || databaseId)
            );
            if (!pipeline) {
                offenders.push({ pipelineId: ref.pipelineId, pipelineName: ref.pipelineId, reason: "not found" });
            } else if (pipeline.archived) {
                offenders.push({ pipelineId: ref.pipelineId, pipelineName: pipeline.pipelineName, reason: "archived" });
            } else if (!pipeline.enabled) {
                offenders.push({ pipelineId: ref.pipelineId, pipelineName: pipeline.pipelineName, reason: "disabled" });
            }
        });
        return offenders;
    }, [effectiveWorkflow.specifiedPipelines, pipelines, databaseId]);

    // Compute validation errors for all pipelines
    const validationErrors = useMemo(() => {
        const errors: Record<string, string[]> = {};

        effectiveWorkflow.specifiedPipelines.forEach((ref) => {
            const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
            const data = pipelineData[compositeKey];
            if (data && data.errors) {
                errors[compositeKey] = data.errors;
            } else {
                errors[compositeKey] = data?.templateId ? [] : ["Template not selected"];
            }
        });

        return errors;
    }, [effectiveWorkflow.specifiedPipelines, pipelineData, databaseId]);

    const hasValidationErrors = Object.values(validationErrors).some((errs) => errs.length > 0) || offendingPipelines.length > 0;

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
            const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
            const data = pipelineData[compositeKey] || {
                pipelineId: ref.pipelineId,
                tags: [],
                errors: [],
                params: {},
            };

            // Use resolved params from the stage (already handles customEditedBody -> customTemplateOverride for mode 5)
            // Backend keys by pipelineId only, so map composite key to pipelineId for API payload
            pipelineExecutionParameters[ref.pipelineId] = data.params;
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
            if (result && typeof result === "object" && "warnings" in result && result.warnings && result.warnings.length > 0) {
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
                    offendingPipelines={offendingPipelines}
                />
            );
        }

        if (currentStageId === "review") {
            return (
                <>
                    {offendingPipelines.length > 0 && (
                        <div className="mb-4 p-4 bg-red-100 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded text-red-900 dark:text-red-200">
                            <strong>Cannot Execute:</strong> The following pipelines are disabled or archived:
                            <ul className="list-disc list-inside mt-2">
                                {offendingPipelines.map((off, idx) => (
                                    <li key={idx}>
                                        <strong>{off.pipelineName}</strong> ({off.reason})
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    <WizardReviewStage
                        workflow={effectiveWorkflow}
                        pipelines={pipelines}
                        pipelineData={pipelineData}
                        inputFiles={inputFiles}
                        outputAssetId={outputAssetId}
                        outputDatabaseId={outputDatabaseId}
                        validationErrors={validationErrors}
                    />
                </>
            );
        }

        // Pipeline stage
        const pipelineIndex = parseInt(currentStageId.replace("pipeline-", ""), 10);
        const pipeline = pipelines[pipelineIndex];
        const ref = effectiveWorkflow.specifiedPipelines[pipelineIndex];

        if (!pipeline) {
            return <div className="text-red-600">Pipeline not found</div>;
        }

        const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;

        return (
            <WizardPipelineStage
                workflow={effectiveWorkflow}
                pipeline={pipeline}
                pipelineRef={ref}
                data={pipelineData[compositeKey]}
                onChange={(data) => {
                    setPipelineData((prev) => ({
                        ...prev,
                        [compositeKey]: data,
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
