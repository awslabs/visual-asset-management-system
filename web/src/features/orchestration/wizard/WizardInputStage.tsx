/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { Workflow, ExecuteInputFile } from "../types";

interface WizardInputStageProps {
    workflow: Workflow;
    databaseId: string;
    presetAsset?: { databaseId: string; assetId: string };
    inputFiles: ExecuteInputFile[];
    outputAssetId?: string;
    outputDatabaseId?: string;
    onInputFilesChange: (files: ExecuteInputFile[]) => void;
    onOutputAssetIdChange: (assetId?: string) => void;
    onOutputDatabaseIdChange: (dbId?: string) => void;
    offendingPipelines?: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
}

const WizardInputStage: React.FC<WizardInputStageProps> = ({
    workflow,
    databaseId,
    presetAsset,
    inputFiles,
    outputAssetId,
    outputDatabaseId,
    onInputFilesChange,
    onOutputAssetIdChange,
    onOutputDatabaseIdChange,
    offendingPipelines = [],
}) => {
    const inputFileArity = workflow.systemConfig?.inputFileArity || "one";
    const allowOutputOverride = workflow.systemConfig?.outputTarget?.allowOverride || false;

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
        onInputFilesChange([
            ...inputFiles,
            { databaseId, assetId: "", relativeFileKey: "/" },
        ]);
    };

    const handleRemoveInputFile = (index: number) => {
        const updated = inputFiles.filter((_, i) => i !== index);
        onInputFilesChange(updated);
    };

    const handleInputFileChange = (index: number, field: string, value: string) => {
        const updated = [...inputFiles];
        updated[index] = { ...updated[index], [field]: value };
        onInputFilesChange(updated);
    };

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Input Files</h3>

            {inputFileArity === "none" && (
                <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded text-blue-900 dark:text-blue-200 text-sm">
                    This workflow does not require input files (results-only execution).
                </div>
            )}

            {inputFileArity === "one" && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Input File
                    </label>
                    {presetAsset ? (
                        <div className="p-3 bg-gray-100 dark:bg-gray-800 rounded text-sm text-gray-700 dark:text-gray-300">
                            Preset Asset: {presetAsset.databaseId} / {presetAsset.assetId}
                        </div>
                    ) : (
                        <div className="space-y-2">
                            <input
                                type="text"
                                placeholder="Database ID"
                                value={inputFiles[0]?.databaseId || databaseId}
                                onChange={(e) => handleInputFileChange(0, "databaseId", e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                            <input
                                type="text"
                                placeholder="Asset ID"
                                value={inputFiles[0]?.assetId || ""}
                                onChange={(e) => handleInputFileChange(0, "assetId", e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                            <input
                                type="text"
                                placeholder="Relative File Key (default: /)"
                                value={inputFiles[0]?.relativeFileKey || "/"}
                                onChange={(e) => handleInputFileChange(0, "relativeFileKey", e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                            <input
                                type="text"
                                placeholder="Version ID (optional)"
                                value={inputFiles[0]?.versionId || ""}
                                onChange={(e) => handleInputFileChange(0, "versionId", e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                    )}
                </div>
            )}

            {inputFileArity === "multi" && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Input Files
                    </label>
                    {inputFiles.map((file, index) => (
                        <div key={index} className="mb-2 p-3 border border-gray-300 dark:border-gray-600 rounded">
                            <div className="space-y-2">
                                <input
                                    type="text"
                                    placeholder="Database ID"
                                    value={file.databaseId}
                                    onChange={(e) => handleInputFileChange(index, "databaseId", e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                />
                                <input
                                    type="text"
                                    placeholder="Asset ID"
                                    value={file.assetId}
                                    onChange={(e) => handleInputFileChange(index, "assetId", e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                />
                                <input
                                    type="text"
                                    placeholder="Relative File Key"
                                    value={file.relativeFileKey}
                                    onChange={(e) => handleInputFileChange(index, "relativeFileKey", e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                />
                                <button
                                    onClick={() => handleRemoveInputFile(index)}
                                    className="text-sm text-red-600 dark:text-red-400 hover:underline"
                                >
                                    Remove
                                </button>
                            </div>
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

            {allowOutputOverride && (
                <div className="mt-4 space-y-2">
                    <h4 className="text-md font-semibold text-gray-900 dark:text-gray-100">
                        Output Target (Optional)
                    </h4>
                    <input
                        type="text"
                        placeholder="Output Asset ID"
                        value={outputAssetId || ""}
                        onChange={(e) => onOutputAssetIdChange(e.target.value || undefined)}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    />
                    <input
                        type="text"
                        placeholder="Output Database ID"
                        value={outputDatabaseId || ""}
                        onChange={(e) => onOutputDatabaseIdChange(e.target.value || undefined)}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    />
                </div>
            )}
        </div>
    );
};

export default WizardInputStage;
