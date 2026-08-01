/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTriggers, useTemplates } from "../api/queries";
import { setTrigger, deleteTrigger } from "../api/workflows";
import Dialog from "../components/Dialog";
import StringListInput from "../components/StringListInput";
import InfoTooltip from "../components/InfoTooltip";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
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
            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
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

interface TriggersEditorProps {
    databaseId: string;
    workflowId: string;
    pipelineRefs: SpecifiedPipelineRef[];
}

const TriggersEditor: React.FC<TriggersEditorProps> = ({
    databaseId,
    workflowId,
    pipelineRefs,
}) => {
    const toast = useToast();
    const queryClient = useQueryClient();
    const { data: triggers = [] } = useTriggers(databaseId, workflowId);

    const [editing, setEditing] = useState(false);
    // Matches the backend trigger default, so a trigger created without touching the box fires.
    const [enabled, setEnabled] = useState(true);
    const [allowFilters, setAllowFilters] = useState<string[]>([]);
    const [excludeFilters, setExcludeFilters] = useState<string[]>([]);
    const [defaultTemplateIds, setDefaultTemplateIds] = useState<Record<string, string>>({});
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);
    // Save error shown inline on the edit form (e.g. a 400 triggerTemplateErrors rejection when a
    // chosen default template has a required tag with no default value).
    const [saveError, setSaveError] = useState<string | null>(null);

    const setTriggerMutation = useMutation({
        // The service returns a [ok, data] tuple; throwing on a falsy `ok` is what routes the
        // failure into onError. An async function is used directly rather than wrapping it in a
        // `new Promise(async ...)` executor, where a throw before the reject would be swallowed.
        mutationFn: async (body: WorkflowTrigger) => {
            const [ok, data] = await setTrigger(databaseId, workflowId, "fileUpload", body);
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to set trigger");
            return data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
            setSaveError(null);
            setEditing(false);
            // The form closes on success, so the toast is the only confirmation the trigger saved.
            toast.success("Trigger saved", {
                description: "File upload trigger",
            });
        },
        onError: (err) => {
            // Kept inline as well: a triggerTemplateErrors rejection names the offending template,
            // which belongs next to the fields that produced it.
            const message = toastErrorMessage(err, "Failed to set trigger");
            setSaveError(message);
            toast.error("Save failed", { description: message });
        },
    });

    const deleteTriggerMutation = useMutation({
        mutationFn: async () => {
            const [ok, data] = await deleteTrigger(databaseId, workflowId, "fileUpload");
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to delete trigger");
            return data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
            // The hydration effect only assigns while a trigger row exists, so clear the form here
            // rather than leaving the deleted values pre-filled for the next create.
            setEnabled(true);
            setAllowFilters([]);
            setExcludeFilters([]);
            setDefaultTemplateIds({});
            toast.success("Trigger deleted", {
                description: "File upload trigger",
            });
        },
        onError: (err) => {
            // The confirm dialog has already closed by this point, so a toast is the only place the
            // failure can be reported — without it a rejected delete looked like a success.
            toast.error("Delete failed", {
                description: toastErrorMessage(err, "Failed to delete trigger"),
            });
        },
    });

    // Load existing trigger when editing
    useEffect(() => {
        const fileUploadTrigger = triggers.find(
            (t: WorkflowTrigger) => t.triggerType === "fileUpload"
        );
        if (fileUploadTrigger) {
            setEnabled(fileUploadTrigger.enabled ?? true);
            setAllowFilters(fileUploadTrigger.inputFileFilters?.allow || []);
            setExcludeFilters(fileUploadTrigger.inputFileFilters?.exclude || []);
            setDefaultTemplateIds(fileUploadTrigger.defaultTemplateIds || {});
        }
    }, [triggers]);

    const handleSave = () => {
        setSaveError(null);
        // Only pipelines still in the workflow are submitted — a stored entry for a since-removed
        // pipeline is invisible in the form but the backend validates every entry it receives.
        const currentKeys = new Set(
            pipelineRefs.map((item) => `${item.pipelineDatabaseId}:${item.pipelineId}`)
        );
        const body: WorkflowTrigger = {
            triggerType: "fileUpload",
            enabled,
            inputFileFilters: {
                allow: allowFilters,
                exclude: excludeFilters,
            },
            defaultTemplateIds: Object.fromEntries(
                Object.entries(defaultTemplateIds).filter(([key]) => currentKeys.has(key))
            ),
        };
        setTriggerMutation.mutate(body);
    };

    const handleDelete = () => {
        setShowDeleteDialog(true);
    };

    const confirmDelete = () => {
        deleteTriggerMutation.mutate();
        setShowDeleteDialog(false);
    };

    const handleTemplateIdChange = (compositeKey: string, templateId: string) => {
        setDefaultTemplateIds({
            ...defaultTemplateIds,
            [compositeKey]: templateId,
        });
    };

    const fileUploadTrigger = triggers.find((t: WorkflowTrigger) => t.triggerType === "fileUpload");

    if (!editing && !fileUploadTrigger) {
        return (
            <div className="border border-border-default rounded p-6 bg-surface-container">
                <h2 className="text-xl font-semibold text-text-primary mb-4">Triggers</h2>
                <div className="text-center py-8">
                    <div className="space-y-2">
                        <div className="font-semibold text-text-primary">
                            No file upload trigger configured
                        </div>
                        <button
                            onClick={() => setEditing(true)}
                            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                        >
                            Create File Upload Trigger
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    if (!editing && fileUploadTrigger) {
        return (
            <>
                <div className="border border-border-default rounded p-6 bg-surface-container">
                    <h2 className="text-xl font-semibold text-text-primary mb-4">Triggers</h2>
                    <table className="min-w-full border-collapse border border-border-default">
                        <thead className="bg-surface-secondary">
                            <tr>
                                <th className="border border-border-default px-4 py-2 text-left text-text-primary">
                                    Type
                                </th>
                                <th className="border border-border-default px-4 py-2 text-left text-text-primary">
                                    Enabled
                                </th>
                                <th className="border border-border-default px-4 py-2 text-left text-text-primary">
                                    Actions
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr className="hover:bg-surface-hover">
                                <td className="border border-border-default px-4 py-2 text-text-primary">
                                    File Upload
                                </td>
                                <td className="border border-border-default px-4 py-2 text-text-primary">
                                    {fileUploadTrigger.enabled ? "Yes" : "No"}
                                </td>
                                <td className="border border-border-default px-4 py-2 text-text-primary">
                                    <div className="flex gap-2">
                                        <button
                                            onClick={() => setEditing(true)}
                                            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                                        >
                                            Edit
                                        </button>
                                        <button
                                            onClick={handleDelete}
                                            className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                                        >
                                            Delete
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <Dialog
                    open={showDeleteDialog}
                    onOpenChange={setShowDeleteDialog}
                    title="Confirm Delete"
                    footer={
                        <>
                            <button
                                onClick={() => setShowDeleteDialog(false)}
                                className="px-4 py-2 bg-surface-secondary text-text-primary rounded hover:bg-surface-hover"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={confirmDelete}
                                className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                            >
                                Delete
                            </button>
                        </>
                    }
                >
                    <p>Delete this trigger?</p>
                </Dialog>
            </>
        );
    }

    return (
        <div className="border border-border-default rounded p-6 bg-surface-container">
            <h2 className="text-xl font-semibold text-text-primary mb-4">
                Edit File Upload Trigger
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
                            checked={enabled}
                            onChange={(e) => setEnabled(e.target.checked)}
                        />
                        <span className="text-sm font-medium text-text-primary">
                            {enabled ? "Enabled" : "Disabled"}
                        </span>
                    </label>
                </div>

                <div>
                    <div className="flex items-center gap-1.5 text-sm font-medium mb-1 text-text-primary">
                        Fire on uploads matching — allow
                        <InfoTooltip text="The trigger fires only when an uploaded file matches an allow entry. Each entry may be an extension (*.glb), a file name, a path, or a wildcard." />
                    </div>
                    <StringListInput
                        ariaLabel="Add trigger allow filter"
                        value={allowFilters}
                        onChange={setAllowFilters}
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
                        value={excludeFilters}
                        onChange={setExcludeFilters}
                        placeholder="e.g. *.tmp"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1 text-text-primary">
                        Default Template IDs (per pipeline)
                    </label>
                    <table className="min-w-full border-collapse border border-border-default">
                        <thead className="bg-surface-secondary">
                            <tr>
                                <th className="border border-border-default px-4 py-2 text-left text-text-primary">
                                    Pipeline
                                </th>
                                <th className="border border-border-default px-4 py-2 text-left text-text-primary">
                                    Default template
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {pipelineRefs.map((item, idx) => {
                                const compositeKey = `${item.pipelineDatabaseId}:${item.pipelineId}`;
                                return (
                                    <tr key={idx} className="hover:bg-surface-hover">
                                        <td className="border border-border-default px-4 py-2 text-text-primary">
                                            {compositeKey}
                                        </td>
                                        <td className="border border-border-default px-4 py-2 text-text-primary">
                                            {/* Pick by template name — the trigger stores the id. */}
                                            <PipelineTemplateSelect
                                                pipelineDatabaseId={item.pipelineDatabaseId || ""}
                                                pipelineId={item.pipelineId}
                                                value={defaultTemplateIds[compositeKey] || ""}
                                                onChange={(templateId) =>
                                                    handleTemplateIdChange(compositeKey, templateId)
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
                        onClick={() => setEditing(false)}
                        className="px-4 py-2 bg-surface-secondary text-text-primary rounded hover:bg-surface-hover"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={setTriggerMutation.isPending}
                        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                        {setTriggerMutation.isPending ? "Saving..." : "Save"}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default TriggersEditor;
