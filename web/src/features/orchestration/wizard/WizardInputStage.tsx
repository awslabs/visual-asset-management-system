/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, ExecuteInputFile, MetadataSourceAsset } from "../types";
import { useDatabases, useAssetSearch } from "../api/queries";
import InputFileSelector from "./InputFileSelector";
import MetadataSourceSelector from "./MetadataSourceSelector";
import SelectedInputFilesList from "./SelectedInputFilesList";
import BulkFilePicker from "./BulkFilePicker";
import InfoTooltip from "../components/InfoTooltip";
import SearchableSelect from "../components/SearchableSelect";
import Callout from "../components/Callout";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { resolveRestrictions } from "./resolveRestrictions";
import { isAllDatabases } from "../api/assets";
import {
    appendInputFiles,
    inputFileKey,
    isCompleteInputFile,
    pluralize,
} from "./selectedInputFiles";
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
    /** Show the "Launched from db / asset" hint — a preset asset with no preset files. */
    showPresetHint?: boolean;
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

/** One titled group of the step. `orch-outline` opts the border into painting (preflight is off). */
const Card: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
    <div className="orch-outline rounded-lg border border-border-default bg-surface-container p-4 space-y-3">
        <h3 className="text-base font-semibold text-text-primary">{title}</h3>
        {children}
    </div>
);

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
    showPresetHint = false,
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
                // `templateKnown` is carried through, not dropped: without it every step reads as
                // resolved and the summary presents an indicative resolution — one a template still
                // to be chosen can narrow — as the final answer on the very step where files are
                // picked.
                pipelineConstraints.map((c) => ({
                    systemConfig: c.systemConfig,
                    templateOverrides: c.templateOverrides,
                    templateKnown: c.templateKnown,
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

    // Multi-file selection. Complete entries sit in the compact list; an incomplete entry (a row
    // still being picked) and at most one complete entry opened with Edit render as picker rows. The
    // picker dialog and the last bulk result are step-local too.
    const [editingIndex, setEditingIndex] = React.useState<number | null>(null);
    const [pickerOpen, setPickerOpen] = React.useState(false);
    const [bulkNote, setBulkNote] = React.useState("");
    const editing =
        editingIndex !== null && editingIndex < (inputFiles || []).length ? editingIndex : null;
    const pickerRowIndexes = React.useMemo(() => {
        const set = new Set<number>();
        (inputFiles || []).forEach((file, index) => {
            if (!isCompleteInputFile(file) || index === editing) set.add(index);
        });
        return set;
    }, [inputFiles, editing]);
    const selectedKeys = React.useMemo(
        () => new Set((inputFiles || []).map(inputFileKey)),
        [inputFiles]
    );
    const hasListedFiles = (inputFiles || []).some((_, index) => !pickerRowIndexes.has(index));

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
            <Callout tone="warning">
                <strong>Cannot Execute:</strong>{" "}
                {!workflow.enabled ? "This workflow is disabled." : "This workflow is archived."}
            </Callout>
        );
    }

    // Offending pipelines banner
    if (offendingPipelines.length > 0) {
        return (
            <Callout tone="error">
                <strong>Cannot Execute:</strong> The following pipelines are disabled or archived:
                <ul className="list-disc list-inside mt-2">
                    {offendingPipelines.map((off, idx) => (
                        <li key={idx}>
                            <strong>{off.pipelineName}</strong> ({off.reason})
                        </li>
                    ))}
                </ul>
            </Callout>
        );
    }

    const handleAddInputFile = () => {
        // Seed a new row with the preset asset when launched from one (so the common case is one
        // click to add another file from the same asset), else an empty row for cross-asset search.
        // The file starts empty so the row asks for an explicit pick; the whole asset stays one
        // option of the File picker rather than the default.
        setEditingIndex(null);
        setBulkNote("");
        onInputFilesChange([
            ...inputFiles,
            {
                databaseId: presetAsset?.databaseId || seedDatabaseId,
                assetId: presetAsset?.assetId || "",
                relativeFileKey: "",
            },
        ]);
    };

    const handleRemoveInputFile = (index: number) => {
        const updated = inputFiles.filter((_, i) => i !== index);
        if (editing !== null) {
            if (editing === index) setEditingIndex(null);
            else if (editing > index) setEditingIndex(editing - 1);
        }
        onInputFilesChange(updated);
    };

    const handleRowChange = (index: number, updated: ExecuteInputFile) => {
        const next = [...inputFiles];
        next[index] = updated;
        // A row that just became complete stays open, so its version can be pinned before it folds
        // into the list.
        if (isCompleteInputFile(updated)) setEditingIndex(index);
        onInputFilesChange(next);
    };

    const handleBulkAdd = (incoming: ExecuteInputFile[]) => {
        const result = appendInputFiles(inputFiles, incoming);
        onInputFilesChange(result.files);
        setBulkNote(
            result.skipped > 0
                ? `Added ${pluralize(result.added, "file")}; ${result.skipped} already selected.`
                : `Added ${pluralize(result.added, "file")}.`
        );
    };

    const handleClearAll = () => {
        setEditingIndex(null);
        setBulkNote("");
        onInputFilesChange([]);
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

    // Output target only for asset-output workflows (results-only writes no asset); metadata sources
    // only for a run with no input files.
    const showOutput = !isResultsOnly;
    const showMetadata = showMetadataSources;

    return (
        // Stacked full-width sections: the files first, where the work is, then where the output
        // lands, then the metadata sources.
        <div className="space-y-4">
            <Card title={inputFileArity === "one" ? "Input File" : "Input Files"}>
                {inputFileArity === "none" && (
                    <p className="text-sm text-text-secondary">
                        This workflow takes no input files (results-only execution).
                    </p>
                )}

                {inputFileArity === "one" && (
                    <>
                        {showPresetHint && presetAsset && (
                            <p className="text-xs text-text-secondary">
                                Launched from {presetAsset.databaseId} / {presetAsset.assetId}. The
                                asset is pre-filled — choose the file to run
                                {allowWholeAsset ? " (or the whole asset)" : ""}. You can also pick
                                a different database/asset.
                            </p>
                        )}
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
                    </>
                )}

                {inputFileArity === "multi" && (
                    <>
                        {showPresetHint && presetAsset && (
                            <p className="text-xs text-text-secondary">
                                Launched from {presetAsset.databaseId} / {presetAsset.assetId}. Add
                                one or more files; a selection can combine files from several
                                databases and assets.
                            </p>
                        )}
                        {inputFiles.length === 0 && (
                            <p className="text-sm text-text-secondary">No input files added yet.</p>
                        )}

                        {/* Every complete entry, windowed, whatever the count. */}
                        {hasListedFiles && (
                            <SelectedInputFilesList
                                files={inputFiles}
                                restrictions={restrictions}
                                skipIndexes={pickerRowIndexes}
                                onRemove={handleRemoveInputFile}
                                onEdit={setEditingIndex}
                                onClearAll={handleClearAll}
                            />
                        )}
                        {bulkNote && (
                            <p className="text-xs text-text-secondary" aria-live="polite">
                                {bulkNote}
                            </p>
                        )}

                        {/* Rows still being picked, and the one opened with Edit. */}
                        {inputFiles.map((file, index) =>
                            pickerRowIndexes.has(index) ? (
                                <div
                                    key={index}
                                    className="orch-outline rounded border border-border-default p-3"
                                >
                                    <InputFileSelector
                                        databaseOptions={databaseOptions}
                                        allowWholeAsset={allowWholeAsset}
                                        allowFolder={allowFolder}
                                        inputFileFilters={fileFilters}
                                        deferVersions={deferRowVersions}
                                        value={file}
                                        onChange={(updated) => handleRowChange(index, updated)}
                                    />
                                    <div className="mt-2 flex gap-4 text-sm">
                                        {isCompleteInputFile(file) && (
                                            <button
                                                type="button"
                                                onClick={() => setEditingIndex(null)}
                                                className="text-blue-600 dark:text-blue-400 hover:underline"
                                            >
                                                Done
                                            </button>
                                        )}
                                        <button
                                            type="button"
                                            onClick={() => handleRemoveInputFile(index)}
                                            className="text-red-600 dark:text-red-400 hover:underline"
                                        >
                                            Remove
                                        </button>
                                    </div>
                                </div>
                            ) : null
                        )}

                        <div className="flex flex-wrap gap-2">
                            {/* Many files at once from one asset through the bulk picker; one file
                                through the row selector, which also offers the whole asset and
                                folders. */}
                            <button
                                type="button"
                                onClick={() => setPickerOpen(true)}
                                className={btnPrimary}
                            >
                                Add files…
                            </button>
                            <button
                                type="button"
                                onClick={handleAddInputFile}
                                className={btnSecondary}
                            >
                                Add Input File
                            </button>
                        </div>
                        {pickerOpen && (
                            <BulkFilePicker
                                onClose={() => setPickerOpen(false)}
                                databaseOptions={databaseOptions}
                                initialDatabaseId={presetAsset?.databaseId || seedDatabaseId}
                                initialAssetId={presetAsset?.assetId || ""}
                                restrictions={restrictions}
                                selectedKeys={selectedKeys}
                                existingCount={inputFiles.length}
                                onAdd={handleBulkAdd}
                            />
                        )}
                    </>
                )}
            </Card>

            {showOutput && (
                <Card title="Output Target">
                    {/* When inputs span multiple assets, the output asset cannot be inferred and MUST
                        be chosen explicitly. */}
                    {allowOutputOverride && distinctInputAssets.length > 1 && !outputAssetId && (
                        <Callout tone="warning" className="text-xs">
                            The selected input files span multiple assets — choose an output asset
                            below.
                        </Callout>
                    )}
                    {/* One row: database, asset, path prefix. Without an override the note takes the
                        first two columns so the prefix keeps its place. */}
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        {allowOutputOverride ? (
                            <>
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
                                        leadingOption={{
                                            value: "",
                                            label: "Use workflow default",
                                        }}
                                        options={(outputAssets || []).map((a: any) => ({
                                            value: a.assetId,
                                            label: a.assetName || a.assetId,
                                            detail: a.assetName ? a.assetId : undefined,
                                        }))}
                                    />
                                </label>
                            </>
                        ) : (
                            <p className="self-end text-xs text-text-secondary md:col-span-2">
                                Output is written to the input asset (this workflow does not allow
                                choosing a different output asset).
                            </p>
                        )}

                        {/* Output path prefix applies to any asset output, override or not. */}
                        <label className="block">
                            <span className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
                                Output path prefix (optional)
                                {/* The full explanation is a tooltip rather than a paragraph: it is
                                    reference material for a single optional field. */}
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
                                // the field means "no prefix", whereas undefined means "untouched"
                                // and lets the workflow default apply.
                                onChange={(e) => onOutputPathPrefixChange(e.target.value)}
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            />
                        </label>
                    </div>
                </Card>
            )}

            {/* Metadata sources. A run with no input files has no assets or databases to derive the
                metadata from, so the entities are named here — as METADATA sources, not as inputs:
                they carry no file key and travel in their own request fields. */}
            {showMetadata && (
                <Card title="Metadata Sources">
                    {/* The wording the section exists for: a source is never required, and never an
                        input file. Named as a status region so it is announced when the section
                        appears. */}
                    <div
                        role="status"
                        aria-label="Metadata source selection is optional"
                        className="orch-outline p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded text-blue-900 dark:text-blue-200 text-sm"
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

                    {/* The database select takes the first column of the same three-column row as
                        the output target, so the two cards line up. */}
                    {wantsDatabaseMetadata && (
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
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
                                        onMetadataSourceDatabaseIdChange?.(
                                            e.target.value || undefined
                                        )
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
                        </div>
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
                                    className="orch-outline mb-2 p-3 border border-border-default rounded"
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
                                    className="orch-outline mt-2 px-3 py-2 text-sm text-blue-600 dark:text-blue-400 border border-blue-600 dark:border-blue-400 rounded hover:bg-blue-50 dark:hover:bg-blue-900/20"
                                >
                                    Add Metadata Source Asset
                                </button>
                            )}
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
};

export default WizardInputStage;
