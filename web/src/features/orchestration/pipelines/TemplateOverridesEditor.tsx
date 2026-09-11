/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import InfoTooltip from "../components/InfoTooltip";
import StringListInput from "../components/StringListInput";
import AssetSpanControl, {
    assetSpanFromScope,
    scopeWithSpan,
} from "../components/AssetSpanControl";
import type { InputFileArity } from "../types";
import { METADATA_LABELS, metadataEnabled } from "../wizard/resolveRestrictions";
import type { MetadataKey } from "../wizard/resolveRestrictions";

/**
 * The metadata-input keys a template may override, in the order the editor shows them: widest entity
 * first, so the rows read database -> asset -> file as the containment they describe.
 */
const METADATA_OVERRIDE_FIELDS: { key: MetadataKey; label: string }[] = [
    { key: "databaseMetadata", label: METADATA_LABELS.databaseMetadata },
    { key: "assetMetadata", label: METADATA_LABELS.assetMetadata },
    { key: "fileMetadata", label: METADATA_LABELS.fileMetadata },
    { key: "fileAttributes", label: METADATA_LABELS.fileAttributes },
];

/**
 * Structured editor for a template's `overrides` object. A template may override only these four
 * keys of the pipeline's systemConfig: inputFileArity, metadataInputs, assetScope, inputFileFilters
 * (the backend ignores any other key). Each section has an "override" toggle — off means the key is
 * omitted from the overrides object and the pipeline's value is inherited. Producing the object here
 * (rather than a raw-JSON box) guarantees a valid shape.
 */
interface TemplateOverridesEditorProps {
    value: Record<string, any>;
    onChange: (overrides: Record<string, any>) => void;
    /** The pipeline's assetScope, used as the starting point when the override is toggled on. */
    inheritedAssetScope?: Record<string, any>;
    /** The pipeline's inputFileArity, used as the starting point when the override is toggled on. */
    inheritedArity?: InputFileArity;
    /** The pipeline's inputFileFilters, used as the starting point when the override is toggled on. */
    inheritedFilters?: { allow?: string[]; exclude?: string[] };
}

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary";

const OverrideToggle: React.FC<{
    checked: boolean;
    onChange: (v: boolean) => void;
    label: string;
    info: string;
}> = ({ checked, onChange, label, info }) => (
    <label className="flex items-center gap-2 text-sm font-medium text-text-primary">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        Override {label}
        <InfoTooltip text={info} />
    </label>
);

/** The pipeline's scope as the four canonical booleans, so toggling the override on starts from the
 *  inherited rules rather than narrowing them. Accepts the registration `wholeAsset` shorthand. */
const seedAssetScope = (inherited?: Record<string, any>) => {
    const source = inherited || {};
    const scope = {
        wholeAssetAllowed: !!(source.wholeAssetAllowed ?? source.wholeAsset),
        folderAllowed: !!source.folderAllowed,
    };
    return scopeWithSpan(scope, assetSpanFromScope(source));
};

/** The pipeline's declared arity, defaulting an absent one to "one" as the backend's own read does. */
const seedArity = (inherited?: InputFileArity): InputFileArity => inherited || "one";

/** The pipeline's filters as fresh lists, so toggling the override on keeps its file restriction
 *  rather than writing empty lists the backend reads as allow-all. */
const seedFilters = (inherited?: { allow?: string[]; exclude?: string[] }) => ({
    allow: [...(inherited?.allow || [])],
    exclude: [...(inherited?.exclude || [])],
});

const MetaRow: React.FC<{ checked: boolean; onChange: (v: boolean) => void; label: string }> = ({
    checked,
    onChange,
    label,
}) => (
    <label className="flex items-center gap-2 text-sm text-text-primary">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        {label}
    </label>
);

const TemplateOverridesEditor: React.FC<TemplateOverridesEditorProps> = ({
    value,
    onChange,
    inheritedAssetScope,
    inheritedArity,
    inheritedFilters,
}) => {
    // A key is "overridden" when present in the object. Toggling off removes it (inherit).
    const has = (key: string) => value[key] !== undefined && value[key] !== null;
    const setKey = (key: string, v: any) => onChange({ ...value, [key]: v });
    const removeKey = (key: string) => {
        const next = { ...value };
        delete next[key];
        onChange(next);
    };

    const metadata = value.metadataInputs || {};
    const filters = value.inputFileFilters || { allow: [], exclude: [] };

    return (
        <div className="space-y-4">
            <p className="text-xs text-text-secondary">
                Optionally override the pipeline's input-handling settings for executions that use
                this template. Anything left un-toggled inherits the pipeline's value.
            </p>

            {/* inputFileArity */}
            <div className="space-y-1">
                <OverrideToggle
                    checked={has("inputFileArity")}
                    onChange={(on) =>
                        on
                            ? setKey("inputFileArity", seedArity(inheritedArity))
                            : removeKey("inputFileArity")
                    }
                    label="input file count"
                    info="Override how many input files an execution using this template takes."
                />
                {has("inputFileArity") && (
                    <>
                        <p className="text-xs text-text-secondary">
                            Starts from the pipeline's current count.
                        </p>
                        <select
                            aria-label="Override input file count"
                            value={value.inputFileArity}
                            onChange={(e) =>
                                setKey("inputFileArity", e.target.value as InputFileArity)
                            }
                            className={selectClass}
                        >
                            <option value="none">None</option>
                            <option value="one">One file</option>
                            <option value="multi">Multiple files</option>
                        </select>
                    </>
                )}
            </div>

            {/* assetScope */}
            <div className="space-y-1">
                <OverrideToggle
                    checked={has("assetScope")}
                    onChange={(on) =>
                        on
                            ? setKey("assetScope", seedAssetScope(inheritedAssetScope))
                            : removeKey("assetScope")
                    }
                    label="asset selection rules"
                    info="Override the pipeline's asset-span / whole-asset / folder rules for this template."
                />
                {has("assetScope") && (
                    <>
                        <p className="text-xs text-text-secondary">
                            Starts from the pipeline's current rules.
                        </p>
                        <AssetSpanControl
                            scope={value.assetScope}
                            onChange={(s) => setKey("assetScope", s)}
                        />
                    </>
                )}
            </div>

            {/* inputFileFilters — sits directly beneath the asset selection rules (both constrain
                the input selection), matching the pipeline form's ordering. */}
            <div className="space-y-1">
                <OverrideToggle
                    checked={has("inputFileFilters")}
                    onChange={(on) =>
                        on
                            ? setKey("inputFileFilters", seedFilters(inheritedFilters))
                            : removeKey("inputFileFilters")
                    }
                    label="input file filters"
                    info="Override the pipeline's allow/exclude input-file filters for this template."
                />
                {has("inputFileFilters") && (
                    <div className="space-y-2 pl-1">
                        <p className="text-xs text-text-secondary">
                            Starts from the pipeline's current filters. An empty allow list accepts
                            any file.
                        </p>
                        <div>
                            <span className="block text-xs text-text-secondary mb-1">Allow</span>
                            <StringListInput
                                ariaLabel="Override allow filter"
                                value={filters.allow || []}
                                onChange={(allow) =>
                                    setKey("inputFileFilters", { ...filters, allow })
                                }
                                placeholder="e.g. *.glb"
                            />
                        </div>
                        <div>
                            <span className="block text-xs text-text-secondary mb-1">Exclude</span>
                            <StringListInput
                                ariaLabel="Override exclude filter"
                                value={filters.exclude || []}
                                onChange={(exclude) =>
                                    setKey("inputFileFilters", { ...filters, exclude })
                                }
                                placeholder="e.g. *.tmp"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* metadataInputs */}
            <div className="space-y-1">
                <OverrideToggle
                    checked={has("metadataInputs")}
                    onChange={(on) =>
                        on
                            ? setKey("metadataInputs", {
                                  assetMetadata: true,
                                  fileMetadata: true,
                                  fileAttributes: true,
                                  databaseMetadata: true,
                              })
                            : removeKey("metadataInputs")
                    }
                    label="metadata inputs"
                    info="Override which metadata is provided to the pipeline for this template."
                />
                {has("metadataInputs") && (
                    <div className="space-y-1 pl-1">
                        {/* Every key reads through metadataEnabled, which defaults an omitted one ON
                            to match the record builders — an override map that omits a key keeps
                            providing that metadata, and binding the raw value would render it as
                            opted out and then persist that opt-out on the next save. */}
                        {METADATA_OVERRIDE_FIELDS.map(({ key, label }) => (
                            <MetaRow
                                key={key}
                                checked={metadataEnabled(metadata, key)}
                                onChange={(v) =>
                                    setKey("metadataInputs", { ...metadata, [key]: v })
                                }
                                label={label}
                            />
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default TemplateOverridesEditor;
