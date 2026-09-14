/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useTemplates } from "../api/queries";
import StringListInput from "../components/StringListInput";
import InfoTooltip from "../components/InfoTooltip";
import type { SpecifiedPipelineRef } from "../types";
import { typeLabel, validateDraft } from "./triggerDraft";
import type { TriggerDraft } from "./triggerDraft";
import { triggerBtnPrimary, triggerBtnSecondary, triggerCell } from "./triggerStyles";

/**
 * Per-pipeline default-template dropdown for the trigger form. Queries that pipeline's templates so
 * the user picks by name instead of typing a template id. Value/onChange operate on the templateId.
 */
const PipelineTemplateSelect: React.FC<{
    pipelineDatabaseId: string;
    pipelineId: string;
    value: string;
    onChange: (templateId: string) => void;
}> = ({ pipelineDatabaseId, pipelineId, value, onChange }) => {
    const { data: templates = [], isLoading } = useTemplates(pipelineDatabaseId, pipelineId);
    return (
        <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
        >
            <option value="">
                {isLoading ? "Loading templates…" : "None (choose at run time)"}
            </option>
            {templates.map((t) => (
                <option key={t.templateId} value={t.templateId}>
                    {t.templateName}
                </option>
            ))}
        </select>
    );
};

interface TriggerFormProps {
    draft: TriggerDraft;
    onChange: (draft: TriggerDraft) => void;
    onSave: () => void;
    onCancel: () => void;
    isSaving: boolean;
    /** Shown inline above the fields: a rejection names the offending template or sibling trigger. */
    saveError: string | null;
    pipelineRefs: SpecifiedPipelineRef[];
    /** Keys already in use, so adding under a taken key is refused rather than replacing a sibling. */
    existingKeys: string[];
    /**
     * True for a system workflow's trigger: only `enabled` may change, so the name, filters and
     * templates are shown read-only (the backend answers 400 to any other change).
     */
    locked?: boolean;
}

/**
 * The add/edit form for one trigger. Controlled: the owner holds the draft and decides what Save
 * does — a PUT for a stored workflow, a list update for one that is not created yet.
 */
const TriggerForm: React.FC<TriggerFormProps> = ({
    draft,
    onChange,
    onSave,
    onCancel,
    isSaving,
    saveError,
    pipelineRefs,
    existingKeys,
    locked = false,
}) => {
    const editing = !!draft.editingKey;
    const { triggerIdInvalid, keyCollides } = validateDraft(draft, existingKeys);

    return (
        <div className="orch-outline border border-border-default rounded p-6 bg-surface-container">
            <h2 className="text-xl font-semibold text-text-primary mb-4">
                {editing ? "Edit" : "Add"} {typeLabel(draft.baseType).toLowerCase()} trigger
            </h2>
            <div className="space-y-4">
                {saveError && (
                    <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded whitespace-pre-line">
                        {saveError}
                    </div>
                )}
                {locked && (
                    <div className="p-3 bg-blue-100 dark:bg-blue-900/20 text-blue-800 dark:text-blue-300 rounded">
                        <strong>System workflow:</strong> this trigger's filters and templates are
                        shipped with the deployment; only <em>Enabled</em> can be changed.
                    </div>
                )}

                <div>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            aria-label="Enabled"
                            checked={draft.enabled}
                            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
                        />
                        <span className="text-sm font-medium text-text-primary">
                            {draft.enabled ? "Enabled" : "Disabled"}
                        </span>
                    </label>
                </div>

                <fieldset disabled={locked} className="m-0 p-0 border-0 min-w-0 space-y-4">
                    {/* The id is what allows a SECOND trigger of this type. Editing an existing trigger
                    cannot change it, because the id is part of the key that addresses the row. */}
                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary">
                            Trigger name
                            <InfoTooltip text="Leave empty for this workflow's first trigger of the type. Give a name to add another trigger of the same type with its own filters and templates — an upload runs the workflow once per matching trigger. Letters, numbers, hyphens and underscores (3-63)." />
                        </div>
                        <input
                            type="text"
                            aria-label="Trigger name"
                            value={draft.triggerId}
                            disabled={editing}
                            onChange={(e) => onChange({ ...draft, triggerId: e.target.value })}
                            placeholder="e.g. nightly (optional)"
                            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-60"
                        />
                        {triggerIdInvalid && (
                            <p className="mt-1 text-sm text-vams-error">
                                Letters, numbers, hyphens and underscores only (3-63).
                            </p>
                        )}
                        {keyCollides && (
                            <p className="mt-1 text-sm text-vams-error">
                                This workflow already has a trigger with that name. Choose another —
                                saving would replace it.
                            </p>
                        )}
                    </div>

                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary">
                            Fire on uploads matching — allow
                            <InfoTooltip text="The trigger fires only when an uploaded file matches an allow entry. Each entry may be an extension (*.glb), a file name, a path, or a wildcard." />
                        </div>
                        <StringListInput
                            ariaLabel="Add trigger allow filter"
                            value={draft.allow}
                            onChange={(allow) => onChange({ ...draft, allow })}
                            placeholder="e.g. *.glb  or  /models/"
                        />
                    </div>

                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary">
                            Fire on uploads matching — exclude
                            <InfoTooltip text="Uploaded files matching an exclude entry never fire the trigger. Exclude takes precedence over allow." />
                        </div>
                        <StringListInput
                            ariaLabel="Add trigger exclude filter"
                            value={draft.exclude}
                            onChange={(exclude) => onChange({ ...draft, exclude })}
                            placeholder="e.g. *.tmp"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1 text-text-primary">
                            Default Template IDs (per pipeline)
                        </label>
                        <table className="orch-outline min-w-full border-collapse border border-border-default">
                            <thead className="bg-surface-secondary">
                                <tr>
                                    <th className={`${triggerCell} text-left`}>Pipeline</th>
                                    <th className={`${triggerCell} text-left`}>Default template</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pipelineRefs.map((item, idx) => {
                                    const compositeKey = `${item.pipelineDatabaseId}:${item.pipelineId}`;
                                    return (
                                        <tr key={idx} className="hover:bg-surface-hover">
                                            <td className={triggerCell}>{compositeKey}</td>
                                            <td className={triggerCell}>
                                                {/* Pick by template name — the trigger stores the id. */}
                                                <PipelineTemplateSelect
                                                    pipelineDatabaseId={
                                                        item.pipelineDatabaseId || ""
                                                    }
                                                    pipelineId={item.pipelineId}
                                                    value={
                                                        draft.defaultTemplateIds[compositeKey] || ""
                                                    }
                                                    onChange={(templateId) =>
                                                        onChange({
                                                            ...draft,
                                                            defaultTemplateIds: {
                                                                ...draft.defaultTemplateIds,
                                                                [compositeKey]: templateId,
                                                            },
                                                        })
                                                    }
                                                />
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </fieldset>

                <div className="flex justify-end gap-2">
                    <button type="button" onClick={onCancel} className={triggerBtnSecondary}>
                        Cancel
                    </button>
                    <button
                        type="button"
                        onClick={onSave}
                        disabled={isSaving || triggerIdInvalid || keyCollides}
                        className={`${triggerBtnPrimary} disabled:opacity-50`}
                    >
                        {isSaving ? "Saving..." : "Save"}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default TriggerForm;
