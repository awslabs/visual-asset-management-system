/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo } from "react";
import Dialog from "../components/Dialog";
import Stepper from "../components/Stepper";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import WizardInputStage from "./WizardInputStage";
import WizardPipelineStage from "./WizardPipelineStage";
import WizardReviewStage from "./WizardReviewStage";
import { useWorkflow, useAllPipelines, useExecuteWorkflow } from "../api/queries";
import { resolvePipelineParams } from "./resolveTemplate";
import type {
    Workflow,
    ExecuteInputFile,
    ExecuteRequest,
    PipelineExecutionParameters,
} from "../types";

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
    const { data: workflowData, isLoading: workflowLoading } = useWorkflow(
        workflow.databaseId,
        workflow.workflowId
    );
    const effectiveWorkflow = workflowData || workflow;

    // Fetch all pipelines for this workflow
    const pipelineIds = effectiveWorkflow.specifiedPipelines.map((p) => p.pipelineId);
    const pipelineDbIds = effectiveWorkflow.specifiedPipelines.map(
        (p) => p.pipelineDatabaseId || databaseId
    );
    const { data: allPipelines, isLoading: pipelinesLoading } = useAllPipelines();

    // The workflow definition (and thus its pipeline references) and the pipeline catalog both
    // load asynchronously. Until both resolve we cannot know the pipeline list, so the wizard shows
    // a loading state rather than prematurely rendering "no pipelines"/"pipeline not found".
    const dataLoading = workflowLoading || pipelinesLoading || !allPipelines;

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
    const [outputPathPrefix, setOutputPathPrefix] = useState<string | undefined>(undefined);

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
                (p) =>
                    p?.pipelineId === ref.pipelineId &&
                    p?.databaseId === (ref.pipelineDatabaseId || databaseId)
            );
            if (!pipeline) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: ref.pipelineId,
                    reason: "not found",
                });
            } else if (pipeline.archived) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: pipeline.pipelineName,
                    reason: "archived",
                });
            } else if (!pipeline.enabled) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: pipeline.pipelineName,
                    reason: "disabled",
                });
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

    // When the workflow allows output override and the selected inputs span more than one asset,
    // the output asset cannot be inferred from a single input asset, so it must be chosen explicitly
    // before launch. (Results-only workflows write no asset output, so this never applies.)
    const isResultsOnly = effectiveWorkflow.systemConfig?.outputTarget?.locationType === "none";
    const allowOutputOverride =
        effectiveWorkflow.systemConfig?.outputTarget?.allowOverride || false;
    const distinctInputAssetCount = React.useMemo(
        () =>
            new Set(inputFiles.filter((f) => f.assetId).map((f) => `${f.databaseId}:${f.assetId}`))
                .size,
        [inputFiles]
    );
    const outputAssetMissing =
        !isResultsOnly && allowOutputOverride && distinctInputAssetCount > 1 && !outputAssetId;

    const hasValidationErrors =
        Object.values(validationErrors).some((errs) => errs.length > 0) ||
        offendingPipelines.length > 0 ||
        outputAssetMissing;

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
        if (outputPathPrefix) {
            body.outputFileBaseExecutionPathExtension = outputPathPrefix;
        }

        try {
            const result = await executeWorkflow.mutateAsync({
                workflowDatabaseId: effectiveWorkflow.databaseId,
                workflowId: effectiveWorkflow.workflowId,
                body,
            });

            // Surface warnings if any
            if (
                result &&
                typeof result === "object" &&
                "warnings" in result &&
                result.warnings &&
                result.warnings.length > 0
            ) {
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
                    outputPathPrefix={outputPathPrefix}
                    onInputFilesChange={setInputFiles}
                    onOutputAssetIdChange={setOutputAssetId}
                    onOutputDatabaseIdChange={setOutputDatabaseId}
                    onOutputPathPrefixChange={setOutputPathPrefix}
                    offendingPipelines={offendingPipelines}
                />
            );
        }

        if (currentStageId === "review") {
            return (
                <>
                    {outputAssetMissing && (
                        <div className="mb-4 p-4 bg-yellow-100 dark:bg-yellow-900/20 border border-yellow-400 dark:border-yellow-700 rounded text-yellow-900 dark:text-yellow-200">
                            The selected input files span multiple assets. Go back to the Input step
                            and choose an output asset before launching.
                        </div>
                    )}
                    {offendingPipelines.length > 0 && (
                        <div className="mb-4 p-4 bg-red-100 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded text-red-900 dark:text-red-200">
                            <strong>Cannot Execute:</strong> The following pipelines are disabled or
                            archived:
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
            return <div className="text-vams-error">Pipeline not found</div>;
        }

        const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;

        return (
            // Key by the composite pipeline key so switching between pipeline steps mounts a FRESH
            // stage instance — its local template/tag/override state must never bleed across
            // pipelines (each pipeline's config is independent).
            <WizardPipelineStage
                key={compositeKey}
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

    // While workflow/pipeline data is still loading, the wizard body is a spinner; suppress the
    // navigation footer so the user cannot step through stages that have no data yet.
    const footer = dataLoading ? null : (
        <div className="flex gap-2">
            {currentIndex > 0 && (
                <button onClick={handleBack} className={btnSecondary}>
                    Back
                </button>
            )}
            {currentIndex < steps.length - 1 && (
                <button onClick={handleNext} disabled={!canNavigateNext()} className={btnPrimary}>
                    Next
                </button>
            )}
            {currentIndex === steps.length - 1 && (
                <button
                    onClick={handleLaunch}
                    disabled={executeWorkflow.isPending || hasValidationErrors}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
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
                {dataLoading ? (
                    <div className="flex items-center justify-center min-h-[400px]">
                        <div className="text-center">
                            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400 mb-3" />
                            <p className="text-text-secondary">Loading workflow pipelines…</p>
                        </div>
                    </div>
                ) : (
                    <>
                        <Stepper steps={steps} current={currentStageId} />
                        <div className="min-h-[400px]">{renderStage()}</div>
                    </>
                )}
            </div>
        </Dialog>
    );
};

export default ExecuteWizard;
