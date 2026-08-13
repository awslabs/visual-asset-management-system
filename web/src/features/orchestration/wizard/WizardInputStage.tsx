/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, ExecuteInputFile, MetadataSourceAsset } from "../types";
import { useDatabases, useAssetSearch } from "../api/queries";
import InputFileSelector from "./InputFileSelector";
import MetadataSourceSelector from "./MetadataSourceSelector";
import InfoTooltip from "../components/InfoTooltip";
import SearchableSelect from "../components/SearchableSelect";
import RestrictionSummary from "./RestrictionSummary";
import { resolveRestrictions } from "./resolveRestrictions";
import { isAllDatabases } from "../api/assets";
import type { PipelineInputConstraints } from "./ExecuteWizard";

interface WizardInputStageProps {
    workflow: Workflow;
    databaseId: string;
    presetAsset?: { databaseId: string; assetId: string };
    inputFiles: ExecuteInputFile[];
    /** Assets named purely as metadata sources (never input files). */
    metadataSourceAssets?: MetadataSourceAsset[];
    /** The ONE database whose own metadata the run reads. */
    metadataSourceDatabaseId?: string;
    outputAssetId?: string;
    outputDatabaseId?: string;
    outputPathPrefix?: string;
    onInputFilesChange: (files: ExecuteInputFile[]) => void;
    onMetadataSourceAssetsChange?: (sources: MetadataSourceAsset[]) => void;
    onMetadataSourceDatabaseIdChange?: (dbId?: string) => void;
    onOutputAssetIdChange: (assetId?: string) => void;
    onOutputDatabaseIdChange: (dbId?: string) => void;
    onOutputPathPrefixChange: (prefix?: string) => void;
    offendingPipelines?: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
    /** Per-step effective config (pipeline systemConfig + the chosen template's overrides), so the
     *  restriction summary reflects the templates actually selected. */
    pipelineConstraints?: PipelineInputConstraints[];
}

/**
 * What the output path prefix does. Held as a constant so the tooltip has one source of wording.
 *
 * Written as an element rather than a string so the examples can be code-formatted — a path fragment
 * or a {{tag}} is unreadable in prose.
 */
const OUTPUT_PATH_PREFIX_HELP = (
    <span className="block space-y-1.5">
        <span className="block">
            Inserted immediately before each output file&apos;s name, so the folders a pipeline
            creates are preserved — <code>/path/file.txt</code> with <code>/run/</code> becomes{" "}
            <code>/path/run/file.txt</code>.
        </span>
        <span className="block">
            A trailing <code>/</code> makes it a folder; without one it joins onto the file name (
            <code>run</code> gives <code>/path/runfile.txt</code>).
        </span>
        <span className="block">
            Supports system and dynamic tags, resolved per execution. The date and execution id are
            the common choices for separating runs — e.g. <code>{"/{{jobStartDate}}/"}</code>,{" "}
            <code>{"/{{executionId}}/"}</code>, or both:{" "}
            <code>{"/{{jobStartDate}}/{{executionId}}/"}</code>. Also useful:{" "}
            <code>{"{{firstAssetFileFileNameNoExt}}"}</code>.
        </span>
        <span className="block">Clear it to add no prefix to the final output paths.</span>
    </span>
);

/**
 * How many input rows still load their version history up front.
 *
 * Each row's list is a separate request, and a selection made in the file manager can carry hundreds
 * of files into this step. Up to this many rows the requests are cheap and the lists are ready before
 * anyone opens one; beyond it each row waits until its own selector is reached.
 */
const EAGER_VERSION_ROW_LIMIT = 5;

const WizardInputStage: React.FC<WizardInputStageProps> = ({
    workflow,
    databaseId,
    presetAsset,
    inputFiles,
    metadataSourceAssets = [],
    metadataSourceDatabaseId,
    outputAssetId,
    outputDatabaseId,
    outputPathPrefix,
    onInputFilesChange,
    onMetadataSourceAssetsChange,
    onMetadataSourceDatabaseIdChange,
    onOutputAssetIdChange,
    onOutputDatabaseIdChange,
    onOutputPathPrefixChange,
    offendingPipelines = [],
    pipelineConstraints = [],
}) => {
    const inputFileArity = workflow.systemConfig?.inputFileArity || "one";
    const allowOutputOverride = workflow.systemConfig?.outputTarget?.allowOverride || false;

    // Databases for the input/output selectors. On a database-scoped launch the database is fixed;
    // on the global page the user picks from the databases they can see.
    const { data: databases } = useDatabases();
    const databaseOptions = React.useMemo(
        () => (databases || []).map((d: any) => ({ databaseId: d.databaseId })),
        [databases]
    );
    // Assets for the optional output-target asset selector (scoped to the chosen output database),
    // resolved server-side per search term so the picker scales past a page of assets.
    const outputDbForAssets = outputDatabaseId || databaseId;
    const [outputAssetQuery, setOutputAssetQuery] = React.useState("");
    const { data: outputAssetPage, isFetching: outputAssetsLoading } = useAssetSearch(
        outputAssetQuery,
        outputDbForAssets,
        !!outputDbForAssets
    );
    const outputAssets = outputAssetPage?.items || [];
    const outputAssetFooter =
        (outputAssetPage?.total ?? 0) > outputAssets.length
            ? `Showing ${outputAssets.length} of ${outputAssetPage?.total} — refine the search`
            : undefined;

    // Distinct assets across the selected input files (only entries that name an asset).
    const distinctInputAssets = React.useMemo(() => {
        const seen = new Map<string, { databaseId: string; assetId: string }>();
        (inputFiles || []).forEach((f) => {
            if (f.assetId)
                seen.set(`${f.databaseId}:${f.assetId}`, {
                    databaseId: f.databaseId,
                    assetId: f.assetId,
                });
        });
        return Array.from(seen.values());
    }, [inputFiles]);

    // Output-asset auto-fill (only when the workflow allows output override). The output asset is
    // very likely one of the input assets, so when the inputs resolve to exactly ONE asset, default
    // the output target to it; when they resolve to >1 (or 0) asset, clear the auto-filled value so
    // the user must choose explicitly (an ambiguous/absent input asset can't be inferred). Skipped
    // for results-only workflows (no asset output).
    const isResultsOnly = workflow.systemConfig?.outputTarget?.locationType === "none";
    React.useEffect(() => {
        if (!allowOutputOverride || isResultsOnly) return;
        if (distinctInputAssets.length === 1) {
            const only = distinctInputAssets[0];
            if (outputAssetId !== only.assetId) onOutputAssetIdChange(only.assetId);
            if (outputDatabaseId !== only.databaseId) onOutputDatabaseIdChange(only.databaseId);
        } else if (distinctInputAssets.length > 1) {
            // Ambiguous — force an explicit choice.
            if (outputAssetId) onOutputAssetIdChange(undefined);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [distinctInputAssets, allowOutputOverride, isResultsOnly]);

    // Resolved down the workflow -> pipeline -> template chain. Computed here rather than read from
    // the backend's aggregate, because that aggregate deliberately excludes template overrides and a
    // template may already be chosen by this point.
    //
    // Declared above the banner early-returns below: a hook after them runs on some renders and not
    // others, which breaks React's hook ordering.
    const restrictions = React.useMemo(
        () =>
            resolveRestrictions(
                workflow.systemConfig,
                pipelineConstraints.map((c) => ({
                    systemConfig: c.systemConfig,
                    templateOverrides: c.templateOverrides,
                }))
            ),
        [workflow.systemConfig, pipelineConstraints]
    );

    // Whether a whole-asset ('/') input is offered, resolved across the workflow, every step, and each
    // step's chosen template — not from the workflow alone. A workflow may permit a whole-asset
    // selection while one of its pipelines does not, and the run is checked against every step, so
    // offering it on the workflow's word alone let the user pick something that failed at validation
    // for a reason the picker already knew. An omitted scope key is not a grant (matching the backend's
    // _scope_errors), so a workflow that says nothing offers neither.
    const allowWholeAsset = restrictions.wholeAssetAllowed;
    // A folder input is gated the same way, and the backend accepts a trailing-slash key wherever the
    // resolved scope grants it.
    const allowFolder = restrictions.folderAllowed;
    // The filters the file pickers offer against are the RESOLVED ones — the workflow's, its
    // pipelines' and the chosen templates'. Filtering on the workflow's alone offered files the chain
    // rejects, which the validation panel then contradicted the picker about.
    const fileFilters = React.useMemo(
        () => ({ allow: restrictions.allow, exclude: restrictions.exclude }),
        [restrictions.allow, restrictions.exclude]
    );
    // The seeded database for a new row. GLOBAL is the shared pipeline/workflow catalog rather than an
    // asset database, so it is not a value the row's Database picker can show or an asset endpoint can
    // take: an empty value renders as the picker's blank prompt and keeps the Asset picker disabled
    // until a real database is chosen.
    const seedDatabaseId = isAllDatabases(databaseId) ? "" : databaseId;
    const deferRowVersions = (inputFiles || []).length > EAGER_VERSION_ROW_LIMIT;

    // Metadata-source pickers, offered only for a run with no input files: with input files the
    // sources are the files' own assets and databases, so there is nothing to name.
    const wantsAssetMetadata = restrictions.metadataInputKeys.includes("assetMetadata");
    const wantsDatabaseMetadata = restrictions.metadataInputKeys.includes("databaseMetadata");
    const showMetadataSources =
        inputFileArity === "none" && (wantsAssetMetadata || wantsDatabaseMetadata);
    // Several source assets are only offerable when the workflow admits a cross-asset span — the same
    // gate the backend applies to the source selection. Metadata sources are exempt from the STEPS'
    // scope (they carry no file key and take no part in a step's input selection), so this reads the
    // workflow's own span rather than the resolved chain.
    const workflowScope = workflow.systemConfig?.assetScope || {};
    const allowMultipleSourceAssets =
        !!workflowScope.crossAssetAllowed && !workflowScope.singleAssetOnly;
    // GLOBAL is dropped: databaseMetadata reads ONE concrete database's own metadata, and GLOBAL is
    // the unscoped/all-databases keyword rather than an asset database, so the backend rejects it.
    const metadataSourceDatabaseOptions = React.useMemo(
        () => databaseOptions.filter((d) => !isAllDatabases(d.databaseId)),
        [databaseOptions]
    );

    // Requirements banner
    if (!workflow.enabled || workflow.archived) {
        return (
            <div className="p-4 bg-yellow-100 dark:bg-yellow-900/30 border border-yellow-400 dark:border-yellow-700 rounded text-yellow-900 dark:text-yellow-200">
                <strong>Cannot Execute:</strong>{" "}
                {!workflow.enabled ? "This workflow is disabled." : "This workflow is archived."}
            </div>
        );
    }

    // Offending pipelines banner
    if (offendingPipelines.length > 0) {
        return (
            <div className="p-4 bg-red-100 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded text-red-900 dark:text-red-200">
                <strong>Cannot Execute:</strong> The following pipelines are disabled or archived:
                <ul className="list-disc list-inside mt-2">
                    {offendingPipelines.map((off, idx) => (
                        <li key={idx}>
                            <strong>{off.pipelineName}</strong> ({off.reason})
                        </li>
                    ))}
                </ul>
            </div>
        );
    }

    const handleAddInputFile = () => {
        // Seed a new row with the preset asset when launched from one (so the common case is one
        // click to add another file from the same asset), else an empty row for cross-asset search.
        onInputFilesChange([
            ...inputFiles,
            {
                databaseId: presetAsset?.databaseId || seedDatabaseId,
                assetId: presetAsset?.assetId || "",
                relativeFileKey: allowWholeAsset ? "/" : "",
            },
        ]);
    };

    const handleRemoveInputFile = (index: number) => {
        const updated = inputFiles.filter((_, i) => i !== index);
        onInputFilesChange(updated);
    };

    const handleAddMetadataSourceAsset = () => {
        onMetadataSourceAssetsChange?.([
            ...metadataSourceAssets,
            { databaseId: metadataSourceDatabaseId || "", assetId: "" },
        ]);
    };

    const handleRemoveMetadataSourceAsset = (index: number) => {
        onMetadataSourceAssetsChange?.(metadataSourceAssets.filter((_, i) => i !== index));
    };

    const handleMetadataSourceAsset = (index: number, source: MetadataSourceAsset) => {
        const next = [...metadataSourceAssets];
        next[index] = source;
        onMetadataSourceAssetsChange?.(next);
    };

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Input Files</h3>

            <RestrictionSummary restrictions={restrictions} />

            {inputFileArity === "none" && (
                <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded text-blue-900 dark:text-blue-200 text-sm">
                    This workflow does not require input files (results-only execution).
                </div>
            )}

            {/* Metadata sources. A run with no input files has no assets or databases to derive the
                metadata from, so the entities are named here — as METADATA sources, not as inputs:
                they carry no file key and travel in their own request fields. */}
            {showMetadataSources && (
                <div className="space-y-3">
                    <h4 className="text-md font-semibold text-text-primary">Metadata Sources</h4>

                    {/* The wording the section exists for: a source is never required, and never an
                        input file. Named as a status region so it is announced when the section
                        appears, rather than only being found by someone reading down the step. */}
                    <div
                        role="status"
                        aria-label="Metadata source selection is optional"
                        className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded text-blue-900 dark:text-blue-200 text-sm"
                    >
                        The{" "}
                        {wantsDatabaseMetadata && wantsAssetMetadata
                            ? "database and asset(s) you select here are"
                            : wantsDatabaseMetadata
                            ? "database you select here is"
                            : "asset(s) you select here are"}{" "}
                        optional and only for metadata input. They are not input files, and this
                        workflow runs whether or not you select any.
                    </div>

                    {wantsDatabaseMetadata && (
                        <label className="block">
                            <span className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
                                Metadata source database (optional)
                                <InfoTooltip
                                    label="Metadata source database help"
                                    text="The one database whose own metadata is read and passed to the steps. Only a concrete database can be named — there is no metadata to read for an all-databases selection."
                                />
                            </span>
                            <select
                                aria-label="Metadata source database"
                                value={metadataSourceDatabaseId || ""}
                                onChange={(e) =>
                                    onMetadataSourceDatabaseIdChange?.(e.target.value || undefined)
                                }
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            >
                                <option value="">No database metadata</option>
                                {metadataSourceDatabaseOptions.map((d) => (
                                    <option key={d.databaseId} value={d.databaseId}>
                                        {d.databaseId}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}

                    {wantsAssetMetadata && (
                        <div>
                            <span className="flex items-center gap-1.5 text-sm font-medium text-text-primary mb-2">
                                {allowMultipleSourceAssets
                                    ? "Metadata source assets (optional)"
                                    : "Metadata source asset (optional)"}
                                <InfoTooltip
                                    label="Metadata source asset help"
                                    text="Each asset named here contributes its asset-level metadata to the run. A source is an entity, not a file, so no file selection is involved."
                                />
                            </span>
                            {metadataSourceAssets.length === 0 && (
                                <p className="text-sm text-text-secondary mb-2">
                                    No metadata source assets selected.
                                </p>
                            )}
                            {metadataSourceAssets.map((source, index) => (
                                <div
                                    key={index}
                                    className="mb-2 p-3 border border-border-default rounded"
                                >
                                    <MetadataSourceSelector
                                        databaseOptions={databaseOptions}
                                        value={source}
                                        onChange={(updated) =>
                                            handleMetadataSourceAsset(index, updated)
                                        }
                                    />
                                    <button
                                        onClick={() => handleRemoveMetadataSourceAsset(index)}
                                        className="mt-2 text-sm text-red-600 dark:text-red-400 hover:underline"
                                    >
                                        Remove Metadata Source
                                    </button>
                                </div>
                            ))}
                            {/* A single-asset workflow offers the control only while nothing is
                                selected, so a second source can never be added. */}
                            {(allowMultipleSourceAssets || metadataSourceAssets.length === 0) && (
                                <button
                                    onClick={handleAddMetadataSourceAsset}
                                    className="mt-2 px-3 py-2 text-sm text-blue-600 dark:text-blue-400 border border-blue-600 dark:border-blue-400 rounded hover:bg-blue-50 dark:hover:bg-blue-900/20"
                                >
                                    Add Metadata Source Asset
                                </button>
                            )}
                        </div>
                    )}
                </div>
            )}

            {inputFileArity === "one" && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Input File
                    </label>
                    {presetAsset && (
                        <p className="text-xs text-text-secondary mb-2">
                            Launched from {presetAsset.databaseId} / {presetAsset.assetId}. The
                            asset is pre-filled — choose the file to run
                            {allowWholeAsset ? " (or the whole asset)" : ""}. You can also pick a
                            different database/asset.
                        </p>
                    )}
                    <div className="p-3 border border-border-default rounded">
                        <InputFileSelector
                            databaseOptions={databaseOptions}
                            allowWholeAsset={allowWholeAsset}
                            allowFolder={allowFolder}
                            inputFileFilters={fileFilters}
                            value={
                                inputFiles[0] || {
                                    databaseId: presetAsset?.databaseId || seedDatabaseId,
                                    assetId: presetAsset?.assetId || "",
                                    relativeFileKey: allowWholeAsset ? "/" : "",
                                }
                            }
                            onChange={(file) => onInputFilesChange([file])}
                        />
                    </div>
                </div>
            )}

            {inputFileArity === "multi" && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Input Files
                    </label>
                    {presetAsset && (
                        <p className="text-xs text-text-secondary mb-2">
                            Launched from {presetAsset.databaseId} / {presetAsset.assetId}. Add one
                            or more files; each row can search a different database/asset, so you
                            can combine files from multiple assets.
                        </p>
                    )}
                    {inputFiles.length === 0 && (
                        <p className="text-sm text-text-secondary mb-2">
                            No input files added yet.
                        </p>
                    )}
                    {inputFiles.map((file, index) => (
                        <div key={index} className="mb-2 p-3 border border-border-default rounded">
                            <InputFileSelector
                                databaseOptions={databaseOptions}
                                allowWholeAsset={allowWholeAsset}
                                allowFolder={allowFolder}
                                inputFileFilters={fileFilters}
                                deferVersions={deferRowVersions}
                                value={file}
                                onChange={(updated) => {
                                    const next = [...inputFiles];
                                    next[index] = updated;
                                    onInputFilesChange(next);
                                }}
                            />
                            <button
                                onClick={() => handleRemoveInputFile(index)}
                                className="mt-2 text-sm text-red-600 dark:text-red-400 hover:underline"
                            >
                                Remove
                            </button>
                        </div>
                    ))}
                    <button
                        onClick={handleAddInputFile}
                        className="mt-2 px-3 py-2 text-sm text-blue-600 dark:text-blue-400 border border-blue-600 dark:border-blue-400 rounded hover:bg-blue-50 dark:hover:bg-blue-900/20"
                    >
                        Add Input File
                    </button>
                </div>
            )}

            {/* Output target — only for asset-output workflows (results-only writes no asset). */}
            {!isResultsOnly && (
                <div className="mt-4 space-y-2">
                    <h4 className="text-md font-semibold text-text-primary">Output Target</h4>

                    {allowOutputOverride ? (
                        <>
                            {/* When inputs span multiple assets, the output asset cannot be inferred
                                and MUST be chosen explicitly. */}
                            {distinctInputAssets.length > 1 && !outputAssetId && (
                                <div className="p-2 text-xs rounded bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 text-yellow-800 dark:text-yellow-300">
                                    The selected input files span multiple assets — choose an output
                                    asset below.
                                </div>
                            )}
                            <label className="block">
                                <span className="block text-xs text-text-secondary mb-1">
                                    Output Database
                                </span>
                                <select
                                    aria-label="Output Database"
                                    value={outputDatabaseId || ""}
                                    onChange={(e) => {
                                        onOutputDatabaseIdChange(e.target.value || undefined);
                                        // Changing the database invalidates the chosen asset.
                                        onOutputAssetIdChange(undefined);
                                    }}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                >
                                    <option value="">Use workflow default</option>
                                    {databaseOptions.map((d) => (
                                        <option key={d.databaseId} value={d.databaseId}>
                                            {d.databaseId}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label className="block">
                                <span className="block text-xs text-text-secondary mb-1">
                                    Output Asset
                                </span>
                                <SearchableSelect
                                    ariaLabel="Output Asset"
                                    value={outputAssetId || ""}
                                    disabled={!outputDbForAssets}
                                    loading={outputAssetsLoading}
                                    onQueryChange={setOutputAssetQuery}
                                    footerNote={outputAssetFooter}
                                    placeholder={
                                        outputDbForAssets
                                            ? "Search output assets…"
                                            : "Select a database first"
                                    }
                                    onChange={(v) => onOutputAssetIdChange(v || undefined)}
                                    leadingOption={{ value: "", label: "Use workflow default" }}
                                    options={(outputAssets || []).map((a: any) => ({
                                        value: a.assetId,
                                        label: a.assetName || a.assetId,
                                        detail: a.assetName ? a.assetId : undefined,
                                    }))}
                                />
                            </label>
                        </>
                    ) : (
                        <p className="text-xs text-text-secondary">
                            Output is written to the input asset (this workflow does not allow
                            choosing a different output asset).
                        </p>
                    )}

                    {/* Output path prefix applies to any asset output, override or not. */}
                    <label className="block">
                        <span className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
                            Output path prefix (optional)
                            {/* The full explanation is a tooltip rather than a paragraph: it is
                                reference material for a single optional field, and inline it dominated
                                the Output section. */}
                            <InfoTooltip
                                label="Output path prefix help"
                                text={OUTPUT_PATH_PREFIX_HELP}
                            />
                        </span>
                        <input
                            type="text"
                            aria-label="Output path prefix"
                            placeholder="No prefix"
                            value={outputPathPrefix || ""}
                            // Pass "" through rather than collapsing it to undefined: clearing
                            // the field means "no prefix", whereas undefined means "untouched" and
                            // lets the workflow default apply.
                            onChange={(e) => onOutputPathPrefixChange(e.target.value)}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                    </label>
                </div>
            )}
        </div>
    );
};

export default WizardInputStage;
