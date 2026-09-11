/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTriggers, useTemplates } from "../api/queries";
import { setTrigger, deleteTrigger } from "../api/workflows";
import Dialog from "../components/Dialog";
import StringListInput from "../components/StringListInput";
import InfoTooltip from "../components/InfoTooltip";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
import { TRIGGER_TYPES, triggerBaseTypeOf } from "../types";
import { useToast, toastErrorMessage } from "../components/ToastProvider";

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

const btnPrimary = "px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700";
const btnSecondary =
    "px-4 py-2 bg-surface-secondary text-text-primary rounded hover:bg-surface-hover";
const btnDanger = "px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700";
const cell = "orch-outline border border-border-default px-4 py-2 text-text-primary";

/** The label for a trigger type, falling back to the raw type for one the UI does not know yet. */
function typeLabel(baseType: string): string {
    return TRIGGER_TYPES.find((t) => t.type === baseType)?.label || baseType;
}

/** What the form holds while a trigger is being created or edited. */
interface TriggerDraft {
    /** The trigger key being written. Empty triggerId means the first trigger of its type. */
    baseType: string;
    triggerId: string;
    /** The key this draft replaces, or "" when creating. Distinguishes an edit from an add. */
    editingKey: string;
    enabled: boolean;
    allow: string[];
    exclude: string[];
    defaultTemplateIds: Record<string, string>;
}

function emptyDraft(baseType: string): TriggerDraft {
    return {
        baseType,
        triggerId: "",
        editingKey: "",
        // Matches the backend trigger default, so a trigger created without touching the box fires.
        enabled: true,
        allow: [],
        exclude: [],
        defaultTemplateIds: {},
    };
}

function draftFrom(trigger: WorkflowTrigger): TriggerDraft {
    const key = trigger.triggerType || "";
    return {
        baseType: triggerBaseTypeOf(trigger),
        triggerId: trigger.triggerId || key.split("#")[1] || "",
        editingKey: key,
        enabled: trigger.enabled ?? true,
        allow: trigger.inputFileFilters?.allow || [],
        exclude: trigger.inputFileFilters?.exclude || [],
        defaultTemplateIds: trigger.defaultTemplateIds || {},
    };
}

/** The key a draft writes to: the bare type, or "type#triggerId" for an additional trigger. */
function draftKey(draft: TriggerDraft): string {
    const id = draft.triggerId.trim();
    return id ? `${draft.baseType}#${id}` : draft.baseType;
}

const TRIGGER_ID_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

interface TriggersEditorProps {
    databaseId: string;
    workflowId: string;
    pipelineRefs: SpecifiedPipelineRef[];
}

/**
 * A workflow's triggers.
 *
 * A workflow may carry SEVERAL triggers of one type, each with its own input-file filters and default
 * templates, so an upload can run the workflow once per matching trigger — the same workflow reacting
 * differently to different uploads. The editor is therefore a list keyed by trigger key rather than a
 * single form, and the types it offers come from TRIGGER_TYPES: adding a type there surfaces it here
 * with no change to this component. `fileUpload` is the only type implemented today.
 *
 * The first trigger of a type is keyed by the bare type and an additional one carries an id suffix,
 * which is what lets a trigger created before multiple triggers existed keep working untouched.
 */
const TriggersEditor: React.FC<TriggersEditorProps> = ({
    databaseId,
    workflowId,
    pipelineRefs,
}) => {
    const toast = useToast();
    const queryClient = useQueryClient();
    const { data: triggers = [], isLoading } = useTriggers(databaseId, workflowId);

    const [draft, setDraft] = useState<TriggerDraft | null>(null);
    const [pendingDelete, setPendingDelete] = useState<WorkflowTrigger | null>(null);
    // Save error shown inline on the form (e.g. a 400 triggerTemplateErrors rejection, or the
    // duplicate-templates / per-asset-concurrency rejections the backend applies to an extra trigger).
    const [saveError, setSaveError] = useState<string | null>(null);

    // A trigger write changes what the workflow queries report as well as this list: the workflow
    // LIST rows carry server-computed triggerCount/triggersEnabledCount (which the list's cards and
    // trigger facet read), and the single-workflow response embeds the trigger rows themselves. All
    // three are invalidated so a saved or deleted trigger is not contradicted by a cached workflow.
    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
        queryClient.invalidateQueries({ queryKey: ["workflows"] });
        queryClient.invalidateQueries({ queryKey: ["workflow", databaseId, workflowId] });
    };

    const setTriggerMutation = useMutation({
        // The service returns a [ok, data] tuple; throwing on a falsy `ok` is what routes the
        // failure into onError. An async function is used directly rather than wrapping it in a
        // `new Promise(async ...)` executor, where a throw before the reject would be swallowed.
        mutationFn: async ({ key, body }: { key: string; body: WorkflowTrigger }) => {
            const [ok, data] = await setTrigger(databaseId, workflowId, key, body);
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to set trigger");
            return data;
        },
        onSuccess: (_data, variables) => {
            invalidate();
            setSaveError(null);
            setDraft(null);
            // The form closes on success, so the toast is the only confirmation the trigger saved.
            toast.success("Trigger saved", { description: variables.key });
        },
        onError: (err) => {
            // Kept inline as well: a rejection names the offending template or the conflicting
            // sibling trigger, which belongs next to the fields that produced it.
            const message = toastErrorMessage(err, "Failed to set trigger");
            setSaveError(message);
            toast.error("Save failed", { description: message });
        },
    });

    const deleteTriggerMutation = useMutation({
        mutationFn: async (key: string) => {
            const [ok, data] = await deleteTrigger(databaseId, workflowId, key);
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to delete trigger");
            return data;
        },
        onSuccess: (_data, key) => {
            invalidate();
            toast.success("Trigger deleted", { description: key });
        },
        onError: (err) => {
            // The confirm dialog has already closed by this point, so a toast is the only place the
            // failure can be reported — without it a rejected delete looked like a success.
            toast.error("Delete failed", {
                description: toastErrorMessage(err, "Failed to delete trigger"),
            });
        },
    });

    const triggerIdInvalid =
        !!draft && !!draft.triggerId.trim() && !TRIGGER_ID_PATTERN.test(draft.triggerId.trim());
    // A key already in use would REPLACE that trigger rather than add one, so adding under a taken key
    // is refused here instead of silently overwriting a sibling.
    const keyCollides =
        !!draft &&
        !draft.editingKey &&
        triggers.some((t: WorkflowTrigger) => t.triggerType === draftKey(draft));

    const handleSave = () => {
        if (!draft || triggerIdInvalid || keyCollides) return;
        setSaveError(null);
        // Only pipelines still in the workflow are submitted — a stored entry for a since-removed
        // pipeline is invisible in the form but the backend validates every entry it receives.
        const currentKeys = new Set(
            pipelineRefs.map((item) => `${item.pipelineDatabaseId}:${item.pipelineId}`)
        );
        const body: WorkflowTrigger = {
            triggerType: draftKey(draft),
            enabled: draft.enabled,
            inputFileFilters: { allow: draft.allow, exclude: draft.exclude },
            defaultTemplateIds: Object.fromEntries(
                Object.entries(draft.defaultTemplateIds).filter(([key]) => currentKeys.has(key))
            ),
        };
        setTriggerMutation.mutate({ key: draftKey(draft), body });
    };

    const confirmDelete = () => {
        if (pendingDelete) deleteTriggerMutation.mutate(pendingDelete.triggerType);
        setPendingDelete(null);
    };

    // ---------------------------------------------------------------- the form

    if (draft) {
        const editing = !!draft.editingKey;
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

                    <div>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={draft.enabled}
                                onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
                            />
                            <span className="text-sm font-medium text-text-primary">
                                {draft.enabled ? "Enabled" : "Disabled"}
                            </span>
                        </label>
                    </div>

                    {/* The id is what allows a SECOND trigger of this type. Editing an existing
                        trigger cannot change it, because the id is part of the key that addresses
                        the row. */}
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
                            onChange={(e) => setDraft({ ...draft, triggerId: e.target.value })}
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
                            onChange={(allow) => setDraft({ ...draft, allow })}
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
                            onChange={(exclude) => setDraft({ ...draft, exclude })}
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
                                    <th className={`${cell} text-left`}>Pipeline</th>
                                    <th className={`${cell} text-left`}>Default template</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pipelineRefs.map((item, idx) => {
                                    const compositeKey = `${item.pipelineDatabaseId}:${item.pipelineId}`;
                                    return (
                                        <tr key={idx} className="hover:bg-surface-hover">
                                            <td className={cell}>{compositeKey}</td>
                                            <td className={cell}>
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
                                                        setDraft({
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

                    <div className="flex justify-end gap-2">
                        <button
                            type="button"
                            onClick={() => {
                                setDraft(null);
                                setSaveError(null);
                            }}
                            className={btnSecondary}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={
                                setTriggerMutation.isPending || triggerIdInvalid || keyCollides
                            }
                            className={`${btnPrimary} disabled:opacity-50`}
                        >
                            {setTriggerMutation.isPending ? "Saving..." : "Save"}
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    // ---------------------------------------------------------------- the list

    return (
        <>
            <div className="orch-outline border border-border-default rounded p-6 bg-surface-container">
                <div className="flex items-start justify-between mb-4">
                    <h2 className="text-xl font-semibold text-text-primary">Triggers</h2>
                    <div className="flex gap-2">
                        {/* One add button per configurable type, so a type added to TRIGGER_TYPES
                            appears here without touching this component. */}
                        {TRIGGER_TYPES.map((t) => (
                            <button
                                key={t.type}
                                type="button"
                                onClick={() => {
                                    setSaveError(null);
                                    setDraft(emptyDraft(t.type));
                                }}
                                title={t.description}
                                className={btnPrimary}
                            >
                                Add {t.label.toLowerCase()} trigger
                            </button>
                        ))}
                    </div>
                </div>

                {isLoading ? (
                    // An in-flight list is not an empty one: the query defaults to [], so rendering the
                    // empty state while it loads tells the reader this workflow has no trigger when it
                    // may well have several.
                    <div className="text-center py-8 text-text-secondary">Loading triggers...</div>
                ) : triggers.length === 0 ? (
                    <div className="text-center py-8">
                        <div className="font-semibold text-text-primary">
                            No triggers configured
                        </div>
                        <p className="mt-1 text-text-secondary">
                            This workflow runs only when it is started explicitly.
                        </p>
                    </div>
                ) : (
                    <table className="orch-outline min-w-full border-collapse border border-border-default">
                        <thead className="bg-surface-secondary">
                            <tr>
                                <th className={`${cell} text-left`}>Type</th>
                                <th className={`${cell} text-left`}>Name</th>
                                <th className={`${cell} text-left`}>Fires on</th>
                                <th className={`${cell} text-left`}>Enabled</th>
                                <th className={`${cell} text-left`}>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {triggers.map((trigger: WorkflowTrigger) => {
                                const allow = trigger.inputFileFilters?.allow || [];
                                const triggerId =
                                    trigger.triggerId ||
                                    (trigger.triggerType || "").split("#")[1] ||
                                    "";
                                return (
                                    <tr
                                        key={trigger.triggerType}
                                        className="hover:bg-surface-hover"
                                    >
                                        <td className={cell}>
                                            {typeLabel(triggerBaseTypeOf(trigger))}
                                        </td>
                                        <td className={cell}>
                                            {triggerId || (
                                                <span className="text-text-secondary">
                                                    (first of type)
                                                </span>
                                            )}
                                        </td>
                                        <td className={cell}>
                                            {allow.length ? (
                                                allow.join(", ")
                                            ) : (
                                                <span className="text-text-secondary">
                                                    any uploaded file
                                                </span>
                                            )}
                                        </td>
                                        <td className={cell}>{trigger.enabled ? "Yes" : "No"}</td>
                                        <td className={cell}>
                                            <div className="flex gap-2">
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        setSaveError(null);
                                                        setDraft(draftFrom(trigger));
                                                    }}
                                                    aria-label={`Edit trigger ${trigger.triggerType}`}
                                                    className={btnPrimary}
                                                >
                                                    Edit
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setPendingDelete(trigger)}
                                                    aria-label={`Delete trigger ${trigger.triggerType}`}
                                                    className={btnDanger}
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            <Dialog
                open={!!pendingDelete}
                onOpenChange={(open) => !open && setPendingDelete(null)}
                title="Confirm Delete"
                footer={
                    <>
                        <button
                            type="button"
                            onClick={() => setPendingDelete(null)}
                            className={btnSecondary}
                        >
                            Cancel
                        </button>
                        <button type="button" onClick={confirmDelete} className={btnDanger}>
                            Delete
                        </button>
                    </>
                }
            >
                {/* Names the trigger: with several of one type, "this trigger" would not say which. */}
                <p>Delete the trigger {pendingDelete?.triggerType}?</p>
            </Dialog>
        </>
    );
};

export default TriggersEditor;
