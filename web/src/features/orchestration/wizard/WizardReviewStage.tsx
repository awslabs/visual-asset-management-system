/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, Pipeline, ExecuteInputFile, MetadataSourceAsset } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";

interface WizardReviewStageProps {
    workflow: Workflow;
    /** Database the wizard was launched in; the fallback for a pipeline ref with no own database. */
    databaseId: string;
    pipelines: (Pipeline | undefined)[];
    pipelineData: Record<string, PipelineStageData>;
    inputFiles: ExecuteInputFile[];
    /** Assets named purely as metadata sources (never input files). */
    metadataSourceAssets?: MetadataSourceAsset[];
    /** The ONE database whose own metadata the run reads. */
    metadataSourceDatabaseId?: string;
    outputAssetId?: string;
    outputDatabaseId?: string;
    validationErrors: Record<string, string[]>;
}

const WizardReviewStage: React.FC<WizardReviewStageProps> = ({
    workflow,
    databaseId,
    pipelines,
    pipelineData,
    inputFiles,
    metadataSourceAssets = [],
    metadataSourceDatabaseId,
    outputAssetId,
    outputDatabaseId,
    validationErrors,
}) => {
    // Only complete rows are sent, so only they are summarized — a half-filled picker row would read
    // as a selection the run does not carry.
    const completeSourceAssets = metadataSourceAssets.filter((s) => s.databaseId && s.assetId);
    const hasMetadataSources = completeSourceAssets.length > 0 || !!metadataSourceDatabaseId;
    const hasAnyErrors = Object.values(validationErrors).some((errors) => errors.length > 0);

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Review & Launch</h3>

            {/* Input summary */}
            <div className="p-3 bg-surface-secondary rounded">
                <h4 className="text-md font-semibold text-text-primary mb-2">Inputs</h4>
                {inputFiles.length === 0 ? (
                    <p className="text-sm text-text-secondary">
                        No input files (results-only workflow)
                    </p>
                ) : (
                    <ul className="list-disc list-inside text-sm text-text-primary">
                        {inputFiles.map((file, idx) => (
                            <li key={idx}>
                                {file.databaseId} / {file.assetId} / {file.relativeFileKey}
                                {file.versionId && ` (v${file.versionId})`}
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {/* Metadata sources — entities read for their metadata only, never as input files. */}
            {hasMetadataSources && (
                <div className="p-3 bg-surface-secondary rounded">
                    <h4 className="text-md font-semibold text-text-primary mb-2">
                        Metadata Sources
                    </h4>
                    <p className="text-xs text-text-secondary mb-2">
                        Read for their metadata only — not input files.
                    </p>
                    {metadataSourceDatabaseId && (
                        <p className="text-sm text-text-primary">
                            Database: {metadataSourceDatabaseId}
                        </p>
                    )}
                    {completeSourceAssets.length > 0 && (
                        <ul className="list-disc list-inside text-sm text-text-primary">
                            {completeSourceAssets.map((source, idx) => (
                                <li key={idx}>
                                    {source.databaseId} / {source.assetId}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* Output target */}
            {(outputAssetId || outputDatabaseId) && (
                <div className="p-3 bg-surface-secondary rounded">
                    <h4 className="text-md font-semibold text-text-primary mb-2">Output Target</h4>
                    <p className="text-sm text-text-primary">
                        Asset: {outputDatabaseId || "(default)"} / {outputAssetId || "(default)"}
                    </p>
                </div>
            )}

            {/* Pipeline summaries */}
            <div className="space-y-2">
                <h4 className="text-md font-semibold text-text-primary">Pipelines</h4>
                {workflow.specifiedPipelines.map((ref, idx) => {
                    const pipeline = pipelines[idx];
                    // Per-pipeline stage data and errors are keyed by the composite pipeline key
                    // (same-id pipelines can exist in different databases).
                    const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${
                        ref.pipelineId
                    }`;
                    const data = pipelineData[compositeKey];
                    const errors = validationErrors[compositeKey] || [];

                    return (
                        <div
                            key={compositeKey}
                            className="orch-outline p-3 bg-surface-secondary rounded border border-border-default"
                        >
                            <h5 className="text-sm font-semibold text-text-primary">
                                {pipeline?.pipelineName || ref.pipelineId}
                            </h5>
                            {data?.templateId && (
                                <p className="text-xs text-text-secondary">
                                    Template: {data.templateId}
                                </p>
                            )}
                            {data?.tags && data.tags.length > 0 && (
                                <p className="text-xs text-text-secondary">
                                    Tags: {data.tags.map((t) => `${t.key}=${t.value}`).join(", ")}
                                </p>
                            )}
                            {data?.customTemplateOverride && (
                                <p className="text-xs text-text-secondary">
                                    Custom override enabled. System tag placeholders left in the
                                    configuration are resolved per pipeline task at launch.
                                </p>
                            )}
                            {errors.length > 0 && (
                                <div className="orch-outline mt-2 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
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
                <div className="orch-outline p-4 bg-red-100 dark:bg-red-900/30 border border-red-400 dark:border-red-700 rounded">
                    <p className="text-sm font-semibold text-red-900 dark:text-red-200">
                        Cannot launch: One or more pipelines have validation errors. Please go back
                        and fix the issues.
                    </p>
                </div>
            )}
        </div>
    );
};

export default WizardReviewStage;
