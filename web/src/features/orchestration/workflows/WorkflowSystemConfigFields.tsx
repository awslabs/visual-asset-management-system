/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { InputFileArity, ConcurrencyRestriction, OutputLocationType } from "../types";
import InfoTooltip from "../components/InfoTooltip";
import StringListInput from "../components/StringListInput";
import AssetSpanControl from "../components/AssetSpanControl";
import { metadataEnabled } from "../wizard/resolveRestrictions";

interface WorkflowSystemConfigFieldsProps {
    inputFileArity: InputFileArity;
    assetScope: Record<string, boolean>;
    metadataInputs: Record<string, boolean>;
    allowFilters: string[];
    excludeFilters: string[];
    concurrencyRestriction: ConcurrencyRestriction;
    locationType: OutputLocationType;
    allowOverride: boolean;
    allowWorkflowTriggerChaining: boolean;
    defaultOutputPathPrefix: string;
    onInputFileArityChange: (value: InputFileArity) => void;
    onAssetScopeChange: (scope: Record<string, boolean>) => void;
    onMetadataInputsChange: (inputs: Record<string, boolean>) => void;
    onAllowFiltersChange: (value: string[]) => void;
    onExcludeFiltersChange: (value: string[]) => void;
    onConcurrencyRestrictionChange: (value: ConcurrencyRestriction) => void;
    onLocationTypeChange: (value: OutputLocationType) => void;
    onAllowOverrideChange: (value: boolean) => void;
    onAllowWorkflowTriggerChainingChange: (value: boolean) => void;
    onDefaultOutputPathPrefixChange: (value: string) => void;
}

// A labeled toggle row (checkbox + label + info). Keys below match the backend systemConfig shape
// exactly — the backend ignores unknown keys, so mislabeled keys silently drop the setting.
const ToggleRow: React.FC<{
    checked: boolean;
    onChange: (v: boolean) => void;
    label: string;
    info: string;
}> = ({ checked, onChange, label, info }) => (
    <label className="flex items-center gap-2">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="text-sm text-text-primary">{label}</span>
        <InfoTooltip text={info} />
    </label>
);

const FieldLabel: React.FC<{ text: string; info: string; htmlFor?: string }> = ({
    text,
    info,
    htmlFor,
}) => (
    <label
        htmlFor={htmlFor}
        className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary"
    >
        {text}
        <InfoTooltip text={info} />
    </label>
);

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50";

/**
 * The workflow admin systemConfig editor. Field labels + info tooltips describe each setting, and
 * the asset-scope / metadata-input toggles use the exact backend keys (see
 * common/workflows/workflowRecords.py + executionValidation.py).
 */
const WorkflowSystemConfigFields: React.FC<WorkflowSystemConfigFieldsProps> = ({
    inputFileArity,
    assetScope,
    metadataInputs,
    allowFilters,
    excludeFilters,
    concurrencyRestriction,
    locationType,
    allowOverride,
    allowWorkflowTriggerChaining,
    defaultOutputPathPrefix,
    onInputFileArityChange,
    onAssetScopeChange,
    onMetadataInputsChange,
    onAllowFiltersChange,
    onExcludeFiltersChange,
    onConcurrencyRestrictionChange,
    onLocationTypeChange,
    onAllowOverrideChange,
    onAllowWorkflowTriggerChainingChange,
    onDefaultOutputPathPrefixChange,
}) => {
    const setMeta = (key: string, checked: boolean) =>
        onMetadataInputsChange({ ...metadataInputs, [key]: checked });

    // Results-only (arity 'none') runs take no input files, so the input-file filters do not apply.
    // Changing arity to 'none' also clears any previously-set filters so a stale filter is not
    // silently persisted on the record.
    const handleArityChange = (value: InputFileArity) => {
        if (value === "none") {
            if (allowFilters.length > 0) onAllowFiltersChange([]);
            if (excludeFilters.length > 0) onExcludeFiltersChange([]);
        }
        onInputFileArityChange(value);
    };
    const showFilters = inputFileArity !== "none";

    return (
        <div className="space-y-4">
            <div>
                <FieldLabel
                    htmlFor="inputFileArity"
                    text="Input file count"
                    info="How many input files an execution takes: 'none' (no input file), 'one' (a single input file), or 'multi' (multiple input files, possibly across assets). This is the outer gate for the whole workflow, so set it to the MAXIMUM any of its pipelines - or any template those pipelines offer - can require. A template may raise its pipeline's own arity when it is chosen at execute time, and a workflow gate lower than that would reject a selection the template would have accepted."
                />
                <select
                    id="inputFileArity"
                    value={inputFileArity}
                    onChange={(e) => handleArityChange(e.target.value as InputFileArity)}
                    className={selectClass}
                >
                    <option value="none">None</option>
                    <option value="one">One file</option>
                    <option value="multi">Multiple files</option>
                </select>
            </div>

            <div>
                <div className="flex items-center gap-1.5 text-sm font-medium mb-2 text-text-primary">
                    Asset selection rules
                    <InfoTooltip text="Constrains which input-file selections an execution may make. Each rule is enforced at execute time. These are the OUTER boundary: a selection is offered only when every pipeline in the workflow also accepts it, so a whole-asset or folder selection allowed here is still withheld when one of the pipelines declines it. The validation panel names any pipeline that narrows what is set here." />
                </div>
                <AssetSpanControl
                    scope={assetScope}
                    onChange={(s) => onAssetScopeChange(s as Record<string, boolean>)}
                />
            </div>

            {/* Input-file filters sit directly beneath the asset selection rules (both constrain the
                input selection). Hidden for results-only runs, which take no input files. */}
            {showFilters && (
                <>
                    <div>
                        <FieldLabel
                            text="Input file filters — allow"
                            info="Only files matching an allow entry are eligible as inputs (when any allow entry is set). Each entry may be a file extension (*.glb), a file name, a path, or a wildcard pattern."
                        />
                        <StringListInput
                            ariaLabel="Add allow filter"
                            value={allowFilters}
                            onChange={onAllowFiltersChange}
                            placeholder="e.g. *.glb  or  /models/  or  building.fbx"
                        />
                    </div>

                    <div>
                        <FieldLabel
                            text="Input file filters — exclude"
                            info="Files matching an exclude entry are never eligible as inputs. Each entry may be an extension, file name, path, or wildcard. Exclude takes precedence over allow."
                        />
                        <StringListInput
                            ariaLabel="Add exclude filter"
                            value={excludeFilters}
                            onChange={onExcludeFiltersChange}
                            placeholder="e.g. *.tmp  or  /drafts/"
                        />
                    </div>
                </>
            )}

            <div>
                <div className="flex items-center gap-1.5 text-sm font-medium mb-2 text-text-primary">
                    Metadata provided to pipelines
                    <InfoTooltip text="Which metadata is gathered from the input assets/files and passed to the pipelines in the shared metadata envelope." />
                </div>
                <div className="space-y-1">
                    {/* Widest entity first, so the rows read database -> asset -> file as the
                        containment they describe. Every key reads through metadataEnabled, which
                        defaults an omitted one ON to match the record builders. Binding a checkbox
                        straight to the raw value would show a config that omits a key as having it
                        off, and saving the form would then persist that as an explicit opt-out. */}
                    <ToggleRow
                        checked={metadataEnabled(metadataInputs, "databaseMetadata")}
                        onChange={(v) => setMeta("databaseMetadata", v)}
                        label="Database metadata"
                        info="Include the metadata of each involved database. With input files these are the databases of the files' assets; a run with no input files reads the one database it names."
                    />
                    <ToggleRow
                        checked={metadataEnabled(metadataInputs, "assetMetadata")}
                        onChange={(v) => setMeta("assetMetadata", v)}
                        label="Asset metadata"
                        info="Include each input asset's asset-level metadata."
                    />
                    <ToggleRow
                        checked={metadataEnabled(metadataInputs, "fileMetadata")}
                        onChange={(v) => setMeta("fileMetadata", v)}
                        label="File metadata"
                        info="Include per-file metadata for each input file."
                    />
                    <ToggleRow
                        checked={metadataEnabled(metadataInputs, "fileAttributes")}
                        onChange={(v) => setMeta("fileAttributes", v)}
                        label="File attributes"
                        info="Include per-file attributes (the string-typed file attribute fields)."
                    />
                </div>
            </div>

            <div>
                <FieldLabel
                    htmlFor="locationType"
                    text="Output destination"
                    info="Where a run writes its output. 'Asset' writes output files + metadata onto a VAMS asset. 'Results only' writes no asset files — only results text and logs against the execution."
                />
                <select
                    id="locationType"
                    value={locationType}
                    onChange={(e) => onLocationTypeChange(e.target.value as OutputLocationType)}
                    className={selectClass}
                >
                    <option value="asset">Write to an asset</option>
                    <option value="none">Results only (no asset output)</option>
                </select>
            </div>

            {locationType === "asset" && (
                <div>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={allowOverride}
                            onChange={(e) => onAllowOverrideChange(e.target.checked)}
                        />
                        <span className="text-sm font-medium text-text-primary">
                            Allow choosing the output asset at execute time
                        </span>
                        <InfoTooltip text="When on, the person running the workflow may redirect output to a different asset (and set an output path prefix). When off, output is locked to the input asset." />
                    </label>

                    <div className="mt-3">
                        <FieldLabel
                            htmlFor="defaultOutputPathPrefix"
                            text="Default output path prefix"
                            info="The output path prefix an execution uses when it names none - the value the execute form is pre-filled with. Stored unresolved, so system and dynamic template tags are resolved per execution: '/{{executionId}}/' gives every run its own folder. Inserted immediately before each output file's name, so the folders a pipeline creates are preserved. A trailing '/' makes it a folder; without one it is joined onto the file name. Leave blank to add no prefix to the final output paths."
                        />
                        <input
                            id="defaultOutputPathPrefix"
                            type="text"
                            value={defaultOutputPathPrefix}
                            onChange={(e) => onDefaultOutputPathPrefixChange(e.target.value)}
                            placeholder="No prefix"
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                        <span className="block text-xs text-text-secondary mt-1">
                            Inserted immediately before each output file's name, so a pipeline's own
                            output folders are kept. Supports system and dynamic tags resolved per
                            execution, e.g. <code>{"{{executionId}}"}</code>. Leave blank to add no
                            prefix.
                        </span>
                    </div>
                </div>
            )}

            <div>
                <label className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        checked={allowWorkflowTriggerChaining}
                        onChange={(e) => onAllowWorkflowTriggerChainingChange(e.target.checked)}
                    />
                    <span className="text-sm font-medium text-text-primary">
                        Allow workflow trigger chaining
                    </span>
                    <InfoTooltip text="When on, a file written by ANOTHER workflow's execution may fire this workflow's triggers - for example generating a preview or metadata from a conversion pipeline's output. This workflow never fires on output it wrote itself, whatever this setting is, so it cannot re-trigger in a loop on its own files. Off by default; a chained file must still match the trigger's input-file filters." />
                </label>
                {allowWorkflowTriggerChaining && (
                    <div
                        role="alert"
                        className="mt-2 p-3 rounded bg-yellow-100 dark:bg-yellow-900/20 text-yellow-900 dark:text-yellow-200 text-sm"
                    >
                        <strong>Chained triggering is enabled.</strong> This workflow will run on
                        files produced by other workflows. Two or more workflows that each write a
                        file the other accepts can trigger each other indefinitely - check the
                        input-file filters of every workflow in the chain before enabling this.
                    </div>
                )}
            </div>

            <div>
                <FieldLabel
                    htmlFor="concurrencyRestriction"
                    text="Concurrency restriction"
                    info="Blocks a new execution while a conflicting one is still running: 'none' (no limit), 'perAsset' (one at a time per asset), or 'perInputFile' (one at a time per input file)."
                />
                <select
                    id="concurrencyRestriction"
                    value={concurrencyRestriction}
                    onChange={(e) =>
                        onConcurrencyRestrictionChange(e.target.value as ConcurrencyRestriction)
                    }
                    className={selectClass}
                >
                    <option value="none">None</option>
                    <option value="perAsset">One per asset</option>
                    <option value="perInputFile">One per input file</option>
                </select>
            </div>
        </div>
    );
};

export default WorkflowSystemConfigFields;
