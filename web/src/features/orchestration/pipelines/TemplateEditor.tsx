/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useTemplates, useTemplateMutations } from "../api/queries";
import type { Template, ConfigFormat, TagSchemaField } from "../types";
import Dialog from "../components/Dialog";
import ConfigEditor from "../components/ConfigEditor";
import DynamicTagForm, { tagSchemaToJsonSchema } from "../components/DynamicTagForm";
import TagSchemaBuilder from "./TagSchemaBuilder";

interface TemplateEditorProps {
    databaseId: string;
    pipelineId: string;
}

const CONFIG_FORMATS: ConfigFormat[] = ["json", "yaml", "openjd", "xml", "raw"];

const TemplateEditor: React.FC<TemplateEditorProps> = ({ databaseId, pipelineId }) => {
    const { data: templates = [], isLoading, error } = useTemplates(databaseId, pipelineId);
    const { createTemplate, updateTemplate, archiveTemplate } = useTemplateMutations();

    const [isModalOpen, setIsModalOpen] = useState(false);
    const [editingTemplate, setEditingTemplate] = useState<Template | null>(null);

    // Form state
    const [templateName, setTemplateName] = useState("");
    const [description, setDescription] = useState("");
    const [configFormat, setConfigFormat] = useState<ConfigFormat>("json");
    const [configBody, setConfigBody] = useState("");
    const [inputInstructions, setInputInstructions] = useState("");
    const [allowCustomEdit, setAllowCustomEdit] = useState(false);
    const [overrides, setOverrides] = useState("{}");
    const [tagSchema, setTagSchema] = useState<TagSchemaField[]>([]);
    const [sizeError, setSizeError] = useState<string | null>(null);

    const handleOpenCreate = () => {
        setEditingTemplate(null);
        setTemplateName("");
        setDescription("");
        setConfigFormat("json");
        setConfigBody("");
        setInputInstructions("");
        setAllowCustomEdit(false);
        setOverrides("{}");
        setTagSchema([]);
        setSizeError(null);
        setIsModalOpen(true);
    };

    const handleOpenEdit = (template: Template) => {
        setEditingTemplate(template);
        setTemplateName(template.templateName);
        setDescription(template.description || "");
        setConfigFormat(template.configFormat);
        setConfigBody(template.configBody || "");
        setInputInstructions(template.inputInstructions || "");
        setAllowCustomEdit(template.allowCustomEdit || false);
        setOverrides(
            template.overrides ? JSON.stringify(template.overrides, null, 2) : "{}"
        );
        setTagSchema(template.tagSchema || []);
        setSizeError(null);
        setIsModalOpen(true);
    };

    const handleClose = () => {
        setIsModalOpen(false);
        setEditingTemplate(null);
    };

    const validateSize = (): boolean => {
        const configBodySize = new Blob([configBody]).size;
        const webFormJsonSize = new Blob([JSON.stringify(tagSchema)]).size;
        const totalSize = configBodySize + webFormJsonSize;

        if (totalSize > 6 * 1024 * 1024) {
            setSizeError(
                `Combined size exceeds 6MB limit (current: ${(totalSize / 1024 / 1024).toFixed(2)}MB)`
            );
            return false;
        }

        setSizeError(null);
        return true;
    };

    const handleSave = async () => {
        if (!validateSize()) {
            return;
        }

        let parsedOverrides: Record<string, any> = {};
        try {
            parsedOverrides = JSON.parse(overrides);
        } catch (err) {
            alert("Invalid JSON in overrides field");
            return;
        }

        const templateData: Partial<Template> = {
            templateName,
            description,
            configFormat,
            configBody,
            inputInstructions,
            allowCustomEdit,
            overrides: parsedOverrides,
            tagSchema,
            webFormJson: JSON.stringify(tagSchema),
        };

        try {
            if (editingTemplate) {
                await updateTemplate.mutateAsync({
                    databaseId,
                    pipelineId,
                    templateId: editingTemplate.templateId,
                    body: templateData,
                });
            } else {
                const newTemplate: Template = {
                    databaseId,
                    pipelineId,
                    templateId: templateName,
                    templateName,
                    ...templateData,
                } as Template;
                await createTemplate.mutateAsync({ databaseId, pipelineId, body: newTemplate });
            }
            handleClose();
        } catch (err: any) {
            alert(`Failed to save template: ${err.message}`);
        }
    };

    const handleArchive = async (templateId: string) => {
        if (!confirm("Are you sure you want to archive this template?")) {
            return;
        }

        try {
            await archiveTemplate.mutateAsync({ databaseId, pipelineId, templateId });
        } catch (err: any) {
            alert(`Failed to archive template: ${err.message}`);
        }
    };

    if (isLoading) {
        return (
            <div className="p-4 text-gray-700 dark:text-gray-300">Loading templates...</div>
        );
    }

    if (error) {
        return <div className="p-4 text-red-600 dark:text-red-400">Error loading templates</div>;
    }

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
                    Templates
                </h2>
                <button
                    onClick={handleOpenCreate}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                >
                    Create Template
                </button>
            </div>

            <div className="space-y-2">
                {templates.length === 0 ? (
                    <p className="text-gray-500 dark:text-gray-400">No templates found</p>
                ) : (
                    templates.map((template) => (
                        <div
                            key={template.templateId}
                            className="border border-gray-300 dark:border-gray-700 rounded p-4 flex justify-between items-start"
                        >
                            <div>
                                <h3 className="font-medium text-gray-900 dark:text-gray-100">
                                    {template.templateName}
                                </h3>
                                {template.description && (
                                    <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                        {template.description}
                                    </p>
                                )}
                                <p className="text-xs text-gray-500 dark:text-gray-500 mt-1">
                                    Format: {template.configFormat}
                                </p>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => handleOpenEdit(template)}
                                    className="px-3 py-1 text-sm bg-gray-600 text-white rounded hover:bg-gray-700"
                                >
                                    Edit
                                </button>
                                <button
                                    onClick={() => handleArchive(template.templateId)}
                                    className="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700"
                                >
                                    Archive
                                </button>
                            </div>
                        </div>
                    ))
                )}
            </div>

            <Dialog
                open={isModalOpen}
                onOpenChange={setIsModalOpen}
                title={editingTemplate ? "Edit Template" : "Create Template"}
                footer={
                    <>
                        <button
                            onClick={handleClose}
                            className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSave}
                            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                            disabled={createTemplate.isPending || updateTemplate.isPending}
                        >
                            Save
                        </button>
                    </>
                }
            >
                <div className="space-y-4 max-h-[70vh] overflow-y-auto">
                    <div>
                        <label className="block text-sm font-medium mb-1">Template Name *</label>
                        <input
                            type="text"
                            value={templateName}
                            onChange={(e) => setTemplateName(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            placeholder="template-name"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">Description</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            rows={2}
                            placeholder="Template description"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">Config Format *</label>
                        <select
                            value={configFormat}
                            onChange={(e) => setConfigFormat(e.target.value as ConfigFormat)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        >
                            {CONFIG_FORMATS.map((format) => (
                                <option key={format} value={format}>
                                    {format}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">Config Body</label>
                        <ConfigEditor
                            value={configBody}
                            language={configFormat}
                            onChange={(val) => setConfigBody(val || "")}
                            height="300px"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">
                            Input Instructions
                        </label>
                        <textarea
                            value={inputInstructions}
                            onChange={(e) => setInputInstructions(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            rows={2}
                            placeholder="Instructions for template users"
                        />
                    </div>

                    <div>
                        <label className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                checked={allowCustomEdit}
                                onChange={(e) => setAllowCustomEdit(e.target.checked)}
                                className="w-4 h-4"
                            />
                            <span className="text-sm">Allow Custom Edit</span>
                        </label>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">
                            Overrides (JSON)
                        </label>
                        <ConfigEditor
                            value={overrides}
                            language="json"
                            onChange={(val) => setOverrides(val || "{}")}
                            height="200px"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-2">Tag Schema</label>
                        <TagSchemaBuilder value={tagSchema} onChange={setTagSchema} />
                    </div>

                    {tagSchema.length > 0 && (
                        <div>
                            <label className="block text-sm font-medium mb-2">
                                Live Preview
                            </label>
                            <div className="border border-gray-300 dark:border-gray-700 rounded p-4 bg-gray-50 dark:bg-gray-800">
                                <DynamicTagForm schema={tagSchema} />
                            </div>
                        </div>
                    )}

                    {sizeError && (
                        <div className="p-3 bg-red-100 dark:bg-red-900 border border-red-300 dark:border-red-700 rounded">
                            <p className="text-sm text-red-800 dark:text-red-200">{sizeError}</p>
                        </div>
                    )}
                </div>
            </Dialog>
        </div>
    );
};

export default TemplateEditor;
