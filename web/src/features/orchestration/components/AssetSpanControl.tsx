/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import InfoTooltip from "./InfoTooltip";

/**
 * The backend asset-scope shape. `crossAssetAllowed` and `singleAssetOnly` are two booleans that can
 * express a contradiction (both on) or a redundancy (!crossAssetAllowed already forbids multiple
 * assets), so the UI collapses them into ONE choice and derives the pair on save.
 */
export interface AssetScope {
    crossAssetAllowed?: boolean;
    singleAssetOnly?: boolean;
    wholeAssetAllowed?: boolean;
    folderAllowed?: boolean;
    /** Shorthand for `wholeAssetAllowed` emitted by the CDK pipeline registration schemas. */
    wholeAsset?: boolean;
}

export type AssetSpan = "single" | "multiple";

/** Fold the `wholeAsset` shorthand into the canonical key. An explicit canonical key wins. */
export function normalizeAssetScope(scope: AssetScope | undefined): AssetScope {
    const { wholeAsset, ...rest } = scope || {};
    if (wholeAsset === undefined || rest.wholeAssetAllowed !== undefined) return rest;
    return { ...rest, wholeAssetAllowed: wholeAsset };
}

/** Read the effective span from a stored scope: multiple only when cross-asset is allowed AND
 *  single-asset-only is not set. Everything else means single. */
export function assetSpanFromScope(scope: AssetScope | undefined): AssetSpan {
    const s = scope || {};
    return s.crossAssetAllowed && !s.singleAssetOnly ? "multiple" : "single";
}

/** Produce the backend boolean pair from the single choice, preserving the whole-asset/folder flags. */
export function scopeWithSpan(scope: AssetScope | undefined, span: AssetSpan): AssetScope {
    const s = normalizeAssetScope(scope);
    if (span === "multiple") {
        return { ...s, crossAssetAllowed: true, singleAssetOnly: false };
    }
    return { ...s, crossAssetAllowed: false, singleAssetOnly: true };
}

interface AssetSpanControlProps {
    scope: AssetScope;
    onChange: (scope: AssetScope) => void;
    disabled?: boolean;
}

/**
 * Single-choice asset-span selector plus the independent whole-asset / folder toggles. Replaces the
 * four free checkboxes that allowed the impossible "multiple assets" + "single asset only" combo.
 */
const AssetSpanControl: React.FC<AssetSpanControlProps> = ({
    scope: rawScope,
    onChange,
    disabled,
}) => {
    const scope = normalizeAssetScope(rawScope);
    const span = assetSpanFromScope(scope);
    return (
        <div className="space-y-3">
            <div>
                <div className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary">
                    Asset span
                    <InfoTooltip text="Whether an execution's input files may span more than one asset. 'Single asset only' rejects inputs from multiple assets; 'Allow multiple assets' permits cross-asset input." />
                </div>
                <select
                    aria-label="Asset span"
                    value={span}
                    disabled={disabled}
                    onChange={(e) => onChange(scopeWithSpan(scope, e.target.value as AssetSpan))}
                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                >
                    <option value="single">Single asset only</option>
                    <option value="multiple">Allow files from multiple assets</option>
                </select>
            </div>
            <div className="space-y-1">
                <label className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        checked={scope.wholeAssetAllowed || false}
                        disabled={disabled}
                        onChange={(e) =>
                            onChange({ ...scope, wholeAssetAllowed: e.target.checked })
                        }
                    />
                    <span className="text-sm text-text-primary">Allow selecting a whole asset</span>
                    <InfoTooltip text="Permit a '/' selection meaning every file in the asset." />
                </label>
                <label className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        checked={scope.folderAllowed || false}
                        disabled={disabled}
                        onChange={(e) => onChange({ ...scope, folderAllowed: e.target.checked })}
                    />
                    <span className="text-sm text-text-primary">Allow selecting a folder</span>
                    <InfoTooltip text="Permit a '/folder/' selection meaning every file under a folder." />
                </label>
            </div>
        </div>
    );
};

export default AssetSpanControl;
