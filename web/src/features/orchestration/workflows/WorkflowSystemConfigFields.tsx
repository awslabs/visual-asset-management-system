/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { InputFileArity, ConcurrencyRestriction, OutputLocationType } from "../types";

interface WorkflowSystemConfigFieldsProps {
    inputFileArity: InputFileArity;
    assetScope: Record<string, boolean>;
    metadataInputs: Record<string, boolean>;
    allowFilters: string;
    excludeFilters: string;
    concurrencyRestriction: ConcurrencyRestriction;
    locationType: OutputLocationType;
    allowOverride: boolean;
    isArityDisabled: boolean;
    onInputFileArityChange: (value: InputFileArity) => void;
    onAssetScopeChange: (scope: Record<string, boolean>) => void;
    onMetadataInputsChange: (inputs: Record<string, boolean>) => void;
    onAllowFiltersChange: (value: string) => void;
    onExcludeFiltersChange: (value: string) => void;
    onConcurrencyRestrictionChange: (value: ConcurrencyRestriction) => void;
    onLocationTypeChange: (value: OutputLocationType) => void;
    onAllowOverrideChange: (value: boolean) => void;
}

const WorkflowSystemConfigFields: React.FC<WorkflowSystemConfigFieldsProps> = ({
    inputFileArity,
    assetScope,
    metadataInputs,
    allowFilters,
    excludeFilters,
    concurrencyRestriction,
    locationType,
    allowOverride,
    isArityDisabled,
    onInputFileArityChange,
    onAssetScopeChange,
    onMetadataInputsChange,
    onAllowFiltersChange,
    onExcludeFiltersChange,
    onConcurrencyRestrictionChange,
    onLocationTypeChange,
    onAllowOverrideChange,
}) => {
    return (
        <div className="space-y-4">
            <div>
                <label htmlFor="inputFileArity" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                    Input File Arity
                </label>
                {isArityDisabled && (
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Locked to 'none' when output location is 'none' (results-only workflows require no input files)
                    </p>
                )}
                <select
                    id="inputFileArity"
                    value={inputFileArity}
                    onChange={(e) => onInputFileArityChange(e.target.value as InputFileArity)}
                    disabled={isArityDisabled}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 disabled:opacity-50"
                >
                    <option value="none">none</option>
                    <option value="one">one</option>
                    <option value="multi">multi</option>
                </select>
            </div>

            <div>
                <label className="block text-sm font-medium mb-2 text-gray-900 dark:text-gray-100">
                    Asset Scope
                </label>
                <div className="space-y-1">
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={assetScope.asset || false}
                            onChange={(e) => onAssetScopeChange({ ...assetScope, asset: e.target.checked })}
                        />
                        <span className="text-sm text-gray-900 dark:text-gray-100">Asset</span>
                    </label>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={assetScope.pipeline || false}
                            onChange={(e) => onAssetScopeChange({ ...assetScope, pipeline: e.target.checked })}
                        />
                        <span className="text-sm text-gray-900 dark:text-gray-100">Pipeline</span>
                    </label>
                </div>
            </div>

            <div>
                <label className="block text-sm font-medium mb-2 text-gray-900 dark:text-gray-100">
                    Metadata Inputs
                </label>
                <div className="space-y-1">
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={metadataInputs.asset || false}
                            onChange={(e) => onMetadataInputsChange({ ...metadataInputs, asset: e.target.checked })}
                        />
                        <span className="text-sm text-gray-900 dark:text-gray-100">Asset Metadata</span>
                    </label>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={metadataInputs.file || false}
                            onChange={(e) => onMetadataInputsChange({ ...metadataInputs, file: e.target.checked })}
                        />
                        <span className="text-sm text-gray-900 dark:text-gray-100">File Metadata</span>
                    </label>
                </div>
            </div>

            <div>
                <label htmlFor="allowFilters" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                    Input File Filters - Allow (comma-separated)
                </label>
                <input
                    id="allowFilters"
                    type="text"
                    value={allowFilters}
                    onChange={(e) => onAllowFiltersChange(e.target.value)}
                    placeholder="e.g., *.jpg, *.png"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                />
            </div>

            <div>
                <label htmlFor="excludeFilters" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                    Input File Filters - Exclude (comma-separated)
                </label>
                <input
                    id="excludeFilters"
                    type="text"
                    value={excludeFilters}
                    onChange={(e) => onExcludeFiltersChange(e.target.value)}
                    placeholder="e.g., *.tmp, *.bak"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                />
            </div>

            <div>
                <label htmlFor="concurrencyRestriction" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                    Concurrency Restriction
                </label>
                <select
                    id="concurrencyRestriction"
                    value={concurrencyRestriction}
                    onChange={(e) => onConcurrencyRestrictionChange(e.target.value as ConcurrencyRestriction)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                >
                    <option value="none">none</option>
                    <option value="perAsset">perAsset</option>
                    <option value="perInputFile">perInputFile</option>
                </select>
            </div>

            <div>
                <label htmlFor="locationType" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                    Output Target - Location Type
                </label>
                <select
                    id="locationType"
                    value={locationType}
                    onChange={(e) => onLocationTypeChange(e.target.value as OutputLocationType)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                >
                    <option value="asset">asset</option>
                    <option value="none">none</option>
                </select>
            </div>

            <div>
                <label className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        checked={allowOverride}
                        onChange={(e) => onAllowOverrideChange(e.target.checked)}
                    />
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                        {allowOverride ? "Allow Override" : "No Override"}
                    </span>
                </label>
            </div>
        </div>
    );
};

export default WorkflowSystemConfigFields;
