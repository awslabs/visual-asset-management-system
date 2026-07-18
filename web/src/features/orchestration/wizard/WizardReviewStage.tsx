/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, Pipeline, ExecuteInputFile } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";

interface WizardReviewStageProps {
    workflow: Workflow;
    pipelines: (Pipeline | undefined)[];
    pipelineData: Record<string, PipelineStageData>;
    inputFiles: ExecuteInputFile[];
    outputAssetId?: string;
    outputDatabaseId?: string;
    validationErrors: Record<string, string[]>;
}

const WizardReviewStage: React.FC<WizardReviewStageProps> = ({
    workflow,
    pipelines,
    pipelineData,
    inputFiles,
    outputAssetId,
    outputDatabaseId,
    validationErrors,
}) => {
    const hasAnyErrors = Object.values(validationErrors).some((errors) => errors.length > 0);

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Review & Launch</h3>

            {/* Input summary */}
            <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded">
                <h4 className="text-md font-semibold text-gray-900 dark:text-gray-100 mb-2">Inputs</h4>
                {inputFiles.length === 0 ? (
                    <p className="text-sm text-gray-600 dark:text-gray-400">
                        No input files (results-only workflow)
                    </p>
                ) : (
                    <ul className="list-disc list-inside text-sm text-gray-700 dark:text-gray-300">
                        {inputFiles.map((file, idx) => (
                            <li key={idx}>
                                {file.databaseId} / {file.assetId} / {file.relativeFileKey}
                                {file.versionId && ` (v${file.versionId})`}
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {/* Output target */}
            {(outputAssetId || outputDatabaseId) && (
                <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded">
                    <h4 className="text-md font-semibold text-gray-900 dark:text-gray-100 mb-2">
                        Output Target
                    </h4>
                    <p className="text-sm text-gray-700 dark:text-gray-300">
                        Asset: {outputDatabaseId || "(default)"} / {outputAssetId || "(default)"}
                    </p>
                </div>
            )}

            {/* Pipeline summaries */}
            <div className="space-y-2">
                <h4 className="text-md font-semibold text-gray-900 dark:text-gray-100">Pipelines</h4>
                {workflow.specifiedPipelines.map((ref, idx) => {
                    const pipeline = pipelines[idx];
                    const data = pipelineData[ref.pipelineId];
                    const errors = validationErrors[ref.pipelineId] || [];

                    return (
                        <div
                            key={ref.pipelineId}
                            className="p-3 bg-gray-50 dark:bg-gray-800 rounded border border-gray-300 dark:border-gray-600"
                        >
                            <h5 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                                {pipeline?.pipelineName || ref.pipelineId}
                            </h5>
                            {data?.templateId && (
                                <p className="text-xs text-gray-600 dark:text-gray-400">
                                    Template: {data.templateId}
                                </p>
                            )}
                            {data?.tags && data.tags.length > 0 && (
                                <p className="text-xs text-gray-600 dark:text-gray-400">
                                    Tags: {data.tags.map((t) => `${t.key}=${t.value}`).join(", ")}
                                </p>
                            )}
                            {data?.customTemplateOverride && (
                                <p className="text-xs text-gray-600 dark:text-gray-400">
                                    Custom override enabled
                                </p>
                            )}
                            {errors.length > 0 && (
                                <div className="mt-2 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
                                    <p className="text-xs font-semibold text-red-900 dark:text-red-200">
                                        Errors:
                                    </p>
                                    <ul className="list-disc list-inside text-xs text-red-800 dark:text-red-300">
                                        {errors.map((err, errIdx) => (
                                            <li key={errIdx}>{err}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Hard error gate */}
            {hasAnyErrors && (
                <div className="p-4 bg-red-100 dark:bg-red-900/30 border border-red-400 dark:border-red-700 rounded">
                    <p className="text-sm font-semibold text-red-900 dark:text-red-200">
                        Cannot launch: One or more pipelines have validation errors. Please go back and
                        fix the issues.
                    </p>
                </div>
            )}
        </div>
    );
};

export default WizardReviewStage;
