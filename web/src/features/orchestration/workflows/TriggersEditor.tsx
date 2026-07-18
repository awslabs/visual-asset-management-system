/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTriggers } from "../api/queries";
import { setTrigger, deleteTrigger } from "../api/workflows";
import Dialog from "../components/Dialog";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";

interface TriggersEditorProps {
    databaseId: string;
    workflowId: string;
    pipelineRefs: SpecifiedPipelineRef[];
}

const TriggersEditor: React.FC<TriggersEditorProps> = ({ databaseId, workflowId, pipelineRefs }) => {
    const queryClient = useQueryClient();
    const { data: triggers = [] } = useTriggers(databaseId, workflowId);

    const [editing, setEditing] = useState(false);
    const [enabled, setEnabled] = useState(false);
    const [allowFilters, setAllowFilters] = useState("");
    const [excludeFilters, setExcludeFilters] = useState("");
    const [defaultTemplateIds, setDefaultTemplateIds] = useState<Record<string, string>>({});
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);

    const setTriggerMutation = useMutation({
        mutationFn: (body: WorkflowTrigger) => {
            return new Promise<any>(async (resolve, reject) => {
                const [ok, data] = await setTrigger(databaseId, workflowId, "fileUpload", body);
                if (!ok) reject(new Error(typeof data === "string" ? data : "Failed to set trigger"));
                else resolve(data);
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
            setEditing(false);
        },
    });

    const deleteTriggerMutation = useMutation({
        mutationFn: () => {
            return new Promise<any>(async (resolve, reject) => {
                const [ok, data] = await deleteTrigger(databaseId, workflowId, "fileUpload");
                if (!ok) reject(new Error(typeof data === "string" ? data : "Failed to delete trigger"));
                else resolve(data);
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
        },
    });

    // Load existing trigger when editing
    useEffect(() => {
        const fileUploadTrigger = triggers.find((t: WorkflowTrigger) => t.triggerType === "fileUpload");
        if (fileUploadTrigger) {
            setEnabled(fileUploadTrigger.enabled ?? false);
            setAllowFilters((fileUploadTrigger.inputFileFilters?.allow || []).join(", "));
            setExcludeFilters((fileUploadTrigger.inputFileFilters?.exclude || []).join(", "));
            setDefaultTemplateIds(fileUploadTrigger.defaultTemplateIds || {});
        }
    }, [triggers]);

    const handleSave = () => {
        const body: WorkflowTrigger = {
            triggerType: "fileUpload",
            enabled,
            inputFileFilters: {
                allow: allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                exclude: excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
            },
            defaultTemplateIds,
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
            <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Triggers</h2>
                <div className="text-center py-8">
                    <div className="space-y-2">
                        <div className="font-semibold text-gray-900 dark:text-gray-100">No file upload trigger configured</div>
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
                <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
                    <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Triggers</h2>
                    <table className="min-w-full border-collapse border border-gray-300 dark:border-gray-700">
                        <thead className="bg-gray-100 dark:bg-gray-800">
                            <tr>
                                <th className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100">Type</th>
                                <th className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100">Enabled</th>
                                <th className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr className="hover:bg-gray-50 dark:hover:bg-gray-800">
                                <td className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100">File Upload</td>
                                <td className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100">{fileUploadTrigger.enabled ? "Yes" : "No"}</td>
                                <td className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100">
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
                                className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded hover:bg-gray-300 dark:hover:bg-gray-600"
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
        <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Edit File Upload Trigger</h2>
            <div className="space-y-4">
                <div>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={enabled}
                            onChange={(e) => setEnabled(e.target.checked)}
                        />
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                            {enabled ? "Enabled" : "Disabled"}
                        </span>
                    </label>
                </div>

                <div>
                    <label htmlFor="allowFilters" className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                        Input File Filters - Allow (comma-separated)
                    </label>
                    <input
                        id="allowFilters"
                        type="text"
                        value={allowFilters}
                        onChange={(e) => setAllowFilters(e.target.value)}
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
                        onChange={(e) => setExcludeFilters(e.target.value)}
                        placeholder="e.g., *.tmp, *.bak"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1 text-gray-900 dark:text-gray-100">
                        Default Template IDs (per pipeline)
                    </label>
                    <table className="min-w-full border-collapse border border-gray-300 dark:border-gray-700">
                        <thead className="bg-gray-100 dark:bg-gray-800">
                            <tr>
                                <th className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100">Pipeline</th>
                                <th className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-left text-gray-900 dark:text-gray-100">Template ID</th>
                            </tr>
                        </thead>
                        <tbody>
                            {pipelineRefs.map((item, idx) => {
                                const compositeKey = `${item.pipelineDatabaseId}:${item.pipelineId}`;
                                return (
                                    <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                                        <td className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100">
                                            {compositeKey}
                                        </td>
                                        <td className="border border-gray-300 dark:border-gray-700 px-4 py-2 text-gray-900 dark:text-gray-100">
                                            <input
                                                type="text"
                                                value={defaultTemplateIds[compositeKey] || ""}
                                                onChange={(e) => handleTemplateIdChange(compositeKey, e.target.value)}
                                                placeholder="Template ID"
                                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
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
                        className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded hover:bg-gray-300 dark:hover:bg-gray-600"
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
