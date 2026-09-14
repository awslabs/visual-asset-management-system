/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Callout from "../components/Callout";
import VirtualList from "../components/VirtualList";
import { collectReviewBlockers } from "./reviewBlockers";
import type { WorkflowBlockedReason } from "./reviewBlockers";
import { describeInputFiles, pluralize, summarizeInputFiles } from "./selectedInputFiles";
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
    /** The prefix every output file lands under. `undefined` = untouched, so the workflow's own
     *  default applies; `""` is a deliberate "write at the asset root". */
    outputPathPrefix?: string;
    validationErrors: Record<string, string[]>;
    /** Workflow + per-pipeline checks on the input selection (blocks Launch). */
    inputSelectionErrors?: string[];
    /** Inputs span several assets and no output asset was chosen (blocks Launch). */
    outputAssetMissing?: boolean;
    offendingPipelines?: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
    /** The workflow itself is disabled or archived (blocks Launch). */
    workflowBlockedReason?: WorkflowBlockedReason;
    /** Jump to a step: "input" or "pipeline-<index>". Without it no Edit link renders. */
    onEdit?: (stepId: string) => void;
}

/**
 * The output path prefix as the review screen states it. An empty value or a bare "/" both mean the
 * asset root, and an untouched field means the workflow's stored default is what the run will use —
 * three cases a blank line cannot distinguish. Mirrors the execution detail view's wording.
 */
const outputPathPrefixText = (prefix?: string): string =>
    prefix === undefined
        ? "(workflow default)"
        : !prefix || prefix === "/"
        ? "None (asset root)"
        : prefix;

/** Input rows listed in full before the rest fold behind "Show all". */
export const REVIEW_INLINE_INPUT_ROWS = 10;
/** Per-asset count lines shown before the rest fold into "+N more assets". */
const REVIEW_ASSET_LINES = 8;
const REVIEW_ROW_HEIGHT = 28;

/** One input entry as the review states it: database / asset / key, and the pinned version. */
const InputLine: React.FC<{ file: ExecuteInputFile }> = ({ file }) => (
    <>
        {file.databaseId} / {file.assetId} / {file.relativeFileKey}
        {file.versionId && ` (v${file.versionId})`}
    </>
);

/**
 * The input selection, sized for hundreds of entries: the count and its spread over assets first,
 * then the rows — inline while few, otherwise the first page with the rest behind a windowed list.
 */
const InputsSummary: React.FC<{ inputFiles: ExecuteInputFile[] }> = ({ inputFiles }) => {
    const [showAll, setShowAll] = React.useState(false);
    const summary = React.useMemo(() => summarizeInputFiles(inputFiles), [inputFiles]);
    const folded = inputFiles.length > REVIEW_INLINE_INPUT_ROWS;
    const inline = folded && !showAll ? inputFiles.slice(0, REVIEW_INLINE_INPUT_ROWS) : inputFiles;
    const hiddenAssets = summary.perAsset.length - REVIEW_ASSET_LINES;

    return (
        <>
            <p className="text-sm text-text-primary" data-testid="review-input-count">
                {describeInputFiles(summary)}
            </p>
            {summary.assetCount > 1 && (
                <ul className="text-xs text-text-secondary">
                    {summary.perAsset.slice(0, REVIEW_ASSET_LINES).map((asset) => (
                        <li key={`${asset.databaseId}:${asset.assetId}`}>
                            {asset.databaseId} / {asset.assetId} — {pluralize(asset.count, "file")}
                        </li>
                    ))}
                    {hiddenAssets > 0 && <li>+{pluralize(hiddenAssets, "more asset")}</li>}
                </ul>
            )}
            {folded && showAll ? (
                <VirtualList
                    items={inputFiles}
                    rowHeight={REVIEW_ROW_HEIGHT}
                    height={REVIEW_ROW_HEIGHT * 10}
                    rowKey={(_, index) => index}
                    renderRow={(file) => (
                        <div className="flex h-full items-center truncate px-1 text-sm text-text-primary">
                            <InputLine file={file} />
                        </div>
                    )}
                    aria-label="Input files"
                    testId="review-input-list"
                    className="orch-outline rounded border border-border-default"
                />
            ) : (
                <ul className="list-disc list-inside text-sm text-text-primary">
                    {inline.map((file, idx) => (
                        <li key={idx}>
                            <InputLine file={file} />
                        </li>
                    ))}
                </ul>
            )}
            {folded && (
                <button
                    type="button"
                    onClick={() => setShowAll((v) => !v)}
                    className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                >
                    {showAll
                        ? `Show first ${REVIEW_INLINE_INPUT_ROWS}`
                        : `Show all ${inputFiles.length}`}
                </button>
            )}
        </>
    );
};

/** A titled summary card with an optional link back to the step that owns its contents. */
const ReviewCard: React.FC<{
    title: string;
    onEdit?: () => void;
    children: React.ReactNode;
}> = ({ title, onEdit, children }) => (
    <div className="orch-outline rounded-lg border border-border-default bg-surface-secondary p-3 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
            <h4 className="text-md font-semibold text-text-primary">{title}</h4>
            {onEdit && (
                <button
                    type="button"
                    aria-label={`Edit ${title}`}
                    onClick={onEdit}
                    className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                >
                    Edit
                </button>
            )}
        </div>
        {children}
    </div>
);

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
    outputPathPrefix,
    validationErrors,
    inputSelectionErrors = [],
    outputAssetMissing = false,
    offendingPipelines = [],
    workflowBlockedReason,
    onEdit,
}) => {
    // Results-only runs write no asset output, so there is no destination to confirm.
    const isResultsOnly = workflow.systemConfig?.outputTarget?.locationType === "none";
    // Only complete rows are sent, so only they are summarized — a half-filled picker row would read
    // as a selection the run does not carry.
    const completeSourceAssets = metadataSourceAssets.filter((s) => s.databaseId && s.assetId);
    const hasMetadataSources = completeSourceAssets.length > 0 || !!metadataSourceDatabaseId;

    const blockers = collectReviewBlockers({
        specifiedPipelines: workflow.specifiedPipelines,
        pipelines,
        databaseId,
        validationErrors,
        inputSelectionErrors,
        outputAssetMissing,
        offendingPipelines,
        workflowBlockedReason,
    });

    const edit = (stepId: string) => (onEdit ? () => onEdit(stepId) : undefined);

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Review & Launch</h3>

            {/* Every reason Launch is disabled, once, with the step that clears it. A live region
                rather than an alert: the launch error below is the step's one alert. */}
            {blockers.length > 0 && (
                <Callout tone="warning" aria-live="polite" title="Blockers">
                    <ul className="mt-1 space-y-1">
                        {blockers.map((b) => (
                            <li
                                key={`${b.stepId}|${b.text}`}
                                className="flex flex-wrap items-baseline gap-x-2"
                            >
                                <span className="font-medium">{b.stepLabel}</span>
                                <span>{b.text}</span>
                                {onEdit && (
                                    <button
                                        type="button"
                                        aria-label={`Edit ${b.stepLabel}`}
                                        onClick={() => onEdit(b.stepId)}
                                        className="text-xs font-medium underline"
                                    >
                                        Edit
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                </Callout>
            )}

            {/* Input summary */}
            <ReviewCard title="Inputs" onEdit={edit("input")}>
                {inputFiles.length === 0 ? (
                    <p className="text-sm text-text-secondary">
                        No input files (results-only workflow)
                    </p>
                ) : (
                    <InputsSummary inputFiles={inputFiles} />
                )}
            </ReviewCard>

            {/* Metadata sources — entities read for their metadata only, never as input files. */}
            {hasMetadataSources && (
                <ReviewCard title="Metadata Sources" onEdit={edit("input")}>
                    <p className="text-xs text-text-secondary">
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
                </ReviewCard>
            )}

            {/* Output target. Shown for every asset-output run, not only one that overrode the ids:
                the path prefix decides where each file lands and is the hardest input to undo after
                the fact, so it has to be confirmable before launch even when the ids are defaults. */}
            {(!isResultsOnly || outputAssetId || outputDatabaseId) && (
                <ReviewCard title="Output Target" onEdit={edit("input")}>
                    <p className="text-sm text-text-primary">
                        Asset: {outputDatabaseId || "(default)"} / {outputAssetId || "(default)"}
                    </p>
                    <p className="text-sm text-text-primary">
                        Path prefix: {outputPathPrefixText(outputPathPrefix)}
                    </p>
                </ReviewCard>
            )}

            {/* One card per pipeline step. */}
            {workflow.specifiedPipelines.map((ref, idx) => {
                const pipeline = pipelines[idx];
                // Stage data is keyed by the composite pipeline key (same-id pipelines can exist in
                // different databases).
                const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
                const data = pipelineData[compositeKey];
                const title = pipeline?.pipelineName || ref.pipelineId;
                // A step with a blocker of any kind does not run at all, so nothing is said about
                // how it would. Mode alone cannot decide this: a require-template step with no
                // template resolves to the same "no template, no override" mode as a step that
                // genuinely runs on its defaults.
                const stepBlocked = blockers.some((b) => b.stepId === `pipeline-${idx}`);

                return (
                    <ReviewCard key={compositeKey} title={title} onEdit={edit(`pipeline-${idx}`)}>
                        {data?.templateId && (
                            <p className="text-sm text-text-primary">
                                Template: {data.templateName || data.templateId}
                                {data.templateName && (
                                    <span className="ml-2 font-mono text-xs text-text-secondary">
                                        {data.templateId}
                                    </span>
                                )}
                            </p>
                        )}
                        {data?.tags && data.tags.length > 0 && (
                            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                                {data.tags.map((t) => (
                                    <React.Fragment key={t.key}>
                                        <dt className="font-mono text-text-secondary">{t.key}</dt>
                                        <dd className="break-words text-text-primary">
                                            {String(t.value)}
                                        </dd>
                                    </React.Fragment>
                                ))}
                            </dl>
                        )}
                        {data?.customTemplateOverride && (
                            <span className="inline-block rounded-full bg-yellow-100 px-2 py-0.5 text-[11px] text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300">
                                Overridden configuration
                            </span>
                        )}
                        {!stepBlocked && !data?.templateId && !data?.customTemplateOverride && (
                            <p className="text-xs text-text-secondary">
                                Runs with its built-in settings.
                            </p>
                        )}
                    </ReviewCard>
                );
            })}
        </div>
    );
};

export default WizardReviewStage;
