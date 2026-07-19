/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo } from "react";
import ConfigEditor from "../components/ConfigEditor";
import DynamicTagForm, { formDataToTags } from "../components/DynamicTagForm";
import { useTemplates } from "../api/queries";
import { resolvePipelineParams } from "./resolveTemplate";
import type { Workflow, Pipeline, SpecifiedPipelineRef, Template } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";

interface WizardPipelineStageProps {
    workflow: Workflow;
    pipeline: Pipeline;
    pipelineRef: SpecifiedPipelineRef;
    data?: PipelineStageData;
    onChange: (data: PipelineStageData) => void;
}

const WizardPipelineStage: React.FC<WizardPipelineStageProps> = ({
    workflow,
    pipeline,
    pipelineRef,
    data,
    onChange,
}) => {
    const { data: templates } = useTemplates(pipeline.databaseId, pipeline.pipelineId);

    const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>(
        data?.templateId || pipelineRef.defaultTemplateId
    );
    const [tagFormData, setTagFormData] = useState<Record<string, any>>({});
    const [customOverrideMode, setCustomOverrideMode] = useState(false);
    const [customOverrideBody, setCustomOverrideBody] = useState<string>("");
    const [customEditMode, setCustomEditMode] = useState(false);
    const [customEditedBody, setCustomEditedBody] = useState<string>("");

    const selectedTemplate = useMemo(() => {
        if (!templates || !selectedTemplateId) return undefined;
        return templates.find((t) => t.templateId === selectedTemplateId);
    }, [templates, selectedTemplateId]);

    // Initialize tagFormData from data or template defaults (only on template change)
    const [initializedTemplateId, setInitializedTemplateId] = useState<string | undefined>(undefined);
    useEffect(() => {
        // Only initialize when the template ID changes
        if (selectedTemplateId !== initializedTemplateId) {
            setInitializedTemplateId(selectedTemplateId);

            if (data && data.tags) {
                const formData: Record<string, any> = {};
                data.tags.forEach((tag) => {
                    formData[tag.key] = tag.value;
                });
                setTagFormData(formData);
            } else if (selectedTemplate?.tagSchema) {
                const formData: Record<string, any> = {};
                selectedTemplate.tagSchema.forEach((field) => {
                    if (field.default !== undefined) {
                        formData[field.tagKey] = field.default;
                    }
                });
                setTagFormData(formData);
            } else {
                setTagFormData({});
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedTemplateId, selectedTemplate]);

    const handleTemplateChange = (templateId: string) => {
        setSelectedTemplateId(templateId);
        setTagFormData({});
        setCustomOverrideMode(false);
        setCustomOverrideBody("");
        setCustomEditMode(false);
        setCustomEditedBody("");
    };

    const handleTagChange = (formData: any) => {
        setTagFormData(formData);
    };

    // Compute resolved config body for display
    const resolvedConfigBody = useMemo(() => {
        if (customOverrideMode) {
            return customOverrideBody;
        }
        if (customEditMode) {
            return customEditedBody;
        }
        if (selectedTemplate?.configBody) {
            return selectedTemplate.configBody;
        }
        return "";
    }, [selectedTemplate, customOverrideMode, customOverrideBody, customEditMode, customEditedBody]);

    // Run resolvePipelineParams to compute validation errors and resolved params
    const validationResult = useMemo(() => {
        if (!selectedTemplate && !selectedTemplateId) {
            return { errors: [], params: {}, mode: 4 as const };
        }

        const tags = formDataToTags(tagFormData);
        return resolvePipelineParams({
            pipeline,
            template: selectedTemplate,
            templateId: selectedTemplateId,
            tags,
            customTemplateOverride: customOverrideMode ? customOverrideBody : undefined,
            customEditedBody: customEditMode ? customEditedBody : undefined,
        });
    }, [
        pipeline,
        selectedTemplate,
        selectedTemplateId,
        tagFormData,
        customOverrideMode,
        customOverrideBody,
        customEditMode,
        customEditedBody,
    ]);

    // Update parent whenever local state changes
    useEffect(() => {
        const tags = formDataToTags(tagFormData);
        const newData: PipelineStageData = {
            pipelineId: pipeline.pipelineId,
            templateId: selectedTemplateId,
            tags,
            customTemplateOverride: customOverrideMode ? customOverrideBody : undefined,
            customEditedBody: customEditMode ? customEditedBody : undefined,
            errors: validationResult.errors,
            params: validationResult.params,
            mode: validationResult.mode,
        };
        onChange(newData);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        selectedTemplateId,
        tagFormData,
        customOverrideMode,
        customOverrideBody,
        customEditMode,
        customEditedBody,
        pipeline.pipelineId,
        validationResult,
        // Intentionally omit onChange to avoid infinite loop
    ]);

    const allowOverride = !!pipeline.systemConfig?.allowCustomTemplateOverride;
    const allowCustomEdit = selectedTemplate?.allowCustomEdit || false;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {pipeline.pipelineName}
            </h3>

            {/* Template selection */}
            {templates && templates.length > 0 && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Template
                    </label>
                    <select
                        value={selectedTemplateId || ""}
                        onChange={(e) => handleTemplateChange(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    >
                        <option value="">-- Select Template --</option>
                        {templates.map((tpl) => (
                            <option key={tpl.templateId} value={tpl.templateId}>
                                {tpl.templateName}
                            </option>
                        ))}
                    </select>
                </div>
            )}

            {/* Tag form */}
            {selectedTemplate?.tagSchema && selectedTemplate.tagSchema.length > 0 && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Template Tags
                    </label>
                    <DynamicTagForm
                        schema={selectedTemplate.tagSchema}
                        formData={tagFormData}
                        onChange={handleTagChange}
                    />
                </div>
            )}

            {/* Custom override mode */}
            {allowOverride && (
                <div>
                    <label className="flex items-center text-sm text-gray-700 dark:text-gray-300">
                        <input
                            type="checkbox"
                            checked={customOverrideMode}
                            onChange={(e) => {
                                setCustomOverrideMode(e.target.checked);
                                if (e.target.checked && !customOverrideBody) {
                                    setCustomOverrideBody(selectedTemplate?.configBody || "");
                                }
                            }}
                            className="mr-2"
                        />
                        Use custom config override
                    </label>
                </div>
            )}

            {/* Custom edit mode (inline toggle) */}
            {allowCustomEdit && !customOverrideMode && (
                <div>
                    <label className="flex items-center text-sm text-gray-700 dark:text-gray-300">
                        <input
                            type="checkbox"
                            checked={customEditMode}
                            onChange={(e) => {
                                setCustomEditMode(e.target.checked);
                                if (e.target.checked && !customEditedBody) {
                                    setCustomEditedBody(selectedTemplate?.configBody || "");
                                }
                            }}
                            className="mr-2"
                        />
                        Edit resolved config inline
                    </label>
                </div>
            )}

            {/* Config editor */}
            {selectedTemplate && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Resolved Config
                    </label>
                    <ConfigEditor
                        value={resolvedConfigBody}
                        language={selectedTemplate.configFormat}
                        readOnly={!(customOverrideMode || customEditMode)}
                        onChange={(value) => {
                            if (customOverrideMode) {
                                setCustomOverrideBody(value || "");
                            } else if (customEditMode) {
                                setCustomEditedBody(value || "");
                            }
                        }}
                        height="300px"
                    />
                </div>
            )}

            {/* Validation errors */}
            {validationResult.errors.length > 0 && (
                <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
                    <p className="text-sm font-semibold text-red-900 dark:text-red-200 mb-1">
                        Validation Errors:
                    </p>
                    <ul className="list-disc list-inside text-sm text-red-800 dark:text-red-300">
                        {validationResult.errors.map((err, idx) => (
                            <li key={idx}>{err}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default WizardPipelineStage;
