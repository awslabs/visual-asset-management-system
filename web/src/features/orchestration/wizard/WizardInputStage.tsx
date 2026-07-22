/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, ExecuteInputFile } from "../types";
import { useDatabases, useAssets } from "../api/queries";
import InputFileSelector from "./InputFileSelector";
import SearchableSelect from "../components/SearchableSelect";

interface WizardInputStageProps {
    workflow: Workflow;
    databaseId: string;
    presetAsset?: { databaseId: string; assetId: string };
    inputFiles: ExecuteInputFile[];
    outputAssetId?: string;
    outputDatabaseId?: string;
    outputPathPrefix?: string;
    onInputFilesChange: (files: ExecuteInputFile[]) => void;
    onOutputAssetIdChange: (assetId?: string) => void;
    onOutputDatabaseIdChange: (dbId?: string) => void;
    onOutputPathPrefixChange: (prefix?: string) => void;
    offendingPipelines?: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
}

const WizardInputStage: React.FC<WizardInputStageProps> = ({
    workflow,
    databaseId,
    presetAsset,
    inputFiles,
    outputAssetId,
    outputDatabaseId,
    outputPathPrefix,
    onInputFilesChange,
    onOutputAssetIdChange,
    onOutputDatabaseIdChange,
    onOutputPathPrefixChange,
    offendingPipelines = [],
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
    // Assets for the optional output-target asset selector (scoped to the chosen output database).
    const outputDbForAssets = outputDatabaseId || databaseId;
    const { data: outputAssets } = useAssets(outputDbForAssets, !!outputDbForAssets);

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
        onInputFilesChange([...inputFiles, { databaseId, assetId: "", relativeFileKey: "/" }]);
    };

    const handleRemoveInputFile = (index: number) => {
        const updated = inputFiles.filter((_, i) => i !== index);
        onInputFilesChange(updated);
    };

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">Input Files</h3>

            {inputFileArity === "none" && (
                <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded text-blue-900 dark:text-blue-200 text-sm">
                    This workflow does not require input files (results-only execution).
                </div>
            )}

            {inputFileArity === "one" && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Input File
                    </label>
                    {presetAsset ? (
                        <div className="p-3 bg-surface-secondary rounded text-sm text-text-primary">
                            Preset Asset: {presetAsset.databaseId} / {presetAsset.assetId}
                        </div>
                    ) : (
                        <div className="p-3 border border-border-default rounded">
                            <InputFileSelector
                                databaseOptions={databaseOptions}
                                value={
                                    inputFiles[0] || {
                                        databaseId,
                                        assetId: "",
                                        relativeFileKey: "/",
                                    }
                                }
                                onChange={(file) => onInputFilesChange([file])}
                            />
                        </div>
                    )}
                </div>
            )}

            {inputFileArity === "multi" && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Input Files
                    </label>
                    {inputFiles.map((file, index) => (
                        <div key={index} className="mb-2 p-3 border border-border-default rounded">
                            <InputFileSelector
                                databaseOptions={databaseOptions}
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
                        <span className="block text-xs text-text-secondary mb-1">
                            Output path prefix (optional)
                        </span>
                        <input
                            type="text"
                            aria-label="Output path prefix"
                            placeholder="/ (asset root)"
                            value={outputPathPrefix || ""}
                            onChange={(e) => onOutputPathPrefixChange(e.target.value || undefined)}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                        {/* Output files are written under this sub-path of the output asset. Dynamic
                            tags resolved at launch are supported. Must not contain ".." or backslashes. */}
                        <span className="block text-xs text-text-secondary mt-1">
                            Written beneath the output asset. Supports dynamic tags, e.g.{" "}
                            <code>{"{{firstAssetFileFileNameNoExt}}"}</code>. Leave blank for the
                            asset root.
                        </span>
                    </label>
                </div>
            )}
        </div>
    );
};

export default WizardInputStage;
