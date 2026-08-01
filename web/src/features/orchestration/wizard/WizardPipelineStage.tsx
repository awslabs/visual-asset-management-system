/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo } from "react";
import ConfigEditor from "../components/ConfigEditor";
import DynamicTagForm, { formDataToTags } from "../components/DynamicTagForm";
import SystemTagHelp from "../components/SystemTagHelp";
import { useTemplates, useTemplate } from "../api/queries";
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

    // Initial template selection precedence: the run's already-chosen template (revisiting the
    // step), then the workflow ref's default, then the pipeline's own default template (isDefault).
    const initialTemplateId =
        data?.templateId ||
        pipelineRef.defaultTemplateId ||
        (templates || []).find((t) => t.isDefault)?.templateId;

    const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>(
        initialTemplateId
    );
    const [tagFormData, setTagFormData] = useState<Record<string, any>>({});
    // Single "Customize configuration" toggle: when on, the config editor is editable and the edited
    // body is sent as a custom override. Replaces the earlier separate override/edit toggles.
    const [customize, setCustomize] = useState<boolean>(
        !!data?.customTemplateOverride || !!data?.customEditedBody
    );
    const [customBody, setCustomBody] = useState<string>(
        data?.customTemplateOverride || data?.customEditedBody || ""
    );

    // The templates LIST omits tagSchema and blanks S3-offloaded bodies, so the selected template
    // is fetched individually to obtain the tag schema (which drives the tag form) and the full
    // config body. The list row is used until the detail arrives so the picker stays responsive.
    const { data: selectedTemplateDetail } = useTemplate(
        pipeline.databaseId,
        pipeline.pipelineId,
        selectedTemplateId || ""
    );

    const selectedTemplate = useMemo(() => {
        if (!selectedTemplateId) return undefined;
        if (selectedTemplateDetail?.templateId === selectedTemplateId) {
            return selectedTemplateDetail;
        }
        if (!templates) return undefined;
        return templates.find((t) => t.templateId === selectedTemplateId);
    }, [templates, selectedTemplateId, selectedTemplateDetail]);

    // Once templates load, adopt the pipeline's default template if nothing is selected yet.
    useEffect(() => {
        if (!selectedTemplateId && templates && templates.length > 0) {
            const def = templates.find((t) => t.isDefault);
            if (def) setSelectedTemplateId(def.templateId);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [templates]);

    // Initialize tagFormData from data or template defaults (only on template change)
    const [initializedTemplateId, setInitializedTemplateId] = useState<string | undefined>(
        undefined
    );
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
        setCustomize(false);
        setCustomBody("");
    };

    const handleTagChange = (formData: any) => {
        setTagFormData(formData);
    };

    // The config editor shows the customized body when customizing, otherwise the template body.
    const resolvedConfigBody = useMemo(() => {
        if (customize) return customBody;
        return selectedTemplate?.configBody || "";
    }, [selectedTemplate, customize, customBody]);

    // Run resolvePipelineParams to compute validation errors and resolved params. A customized body
    // is sent as customTemplateOverride (the backend accepts it under either the pipeline's override
    // grant or the template's allowCustomEdit grant).
    const validationResult = useMemo(() => {
        const tags = formDataToTags(tagFormData);
        return resolvePipelineParams({
            pipeline,
            template: selectedTemplate,
            templateId: selectedTemplateId,
            tags,
            customTemplateOverride: customize ? customBody : undefined,
        });
    }, [pipeline, selectedTemplate, selectedTemplateId, tagFormData, customize, customBody]);

    // Update parent whenever local state changes
    useEffect(() => {
        const tags = formDataToTags(tagFormData);
        const newData: PipelineStageData = {
            pipelineId: pipeline.pipelineId,
            templateId: selectedTemplateId,
            tags,
            customTemplateOverride: customize ? customBody : undefined,
            templateOverrides: selectedTemplate?.overrides,
            errors: validationResult.errors,
            params: validationResult.params,
            mode: validationResult.mode,
        };
        onChange(newData);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        selectedTemplateId,
        tagFormData,
        customize,
        customBody,
        pipeline.pipelineId,
        selectedTemplate,
        validationResult,
        // Intentionally omit onChange to avoid infinite loop
    ]);

    const allowOverride = !!pipeline.systemConfig?.allowCustomTemplateOverride;
    const allowCustomEdit = selectedTemplate?.allowCustomEdit || false;
    // The unified "Customize configuration" toggle is available when either grant is present.
    const canCustomize = allowOverride || allowCustomEdit;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">{pipeline.pipelineName}</h3>

            {/* Template selection */}
            {templates && templates.length > 0 && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Template
                    </label>
                    <select
                        value={selectedTemplateId || ""}
                        onChange={(e) => handleTemplateChange(e.target.value)}
                        className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
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
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Template Tags
                    </label>
                    <DynamicTagForm
                        schema={selectedTemplate.tagSchema}
                        formData={tagFormData}
                        onChange={handleTagChange}
                    />
                </div>
            )}

            {/* Unified "Customize configuration" toggle — available when the pipeline allows a custom
                override OR the selected template allows custom edit. When on, the config editor below
                becomes editable and the edited body is sent as the run's config. */}
            {canCustomize && (
                <div>
                    <label className="flex items-center text-sm text-text-primary">
                        <input
                            type="checkbox"
                            checked={customize}
                            onChange={(e) => {
                                setCustomize(e.target.checked);
                                if (e.target.checked && !customBody) {
                                    setCustomBody(selectedTemplate?.configBody || "");
                                }
                            }}
                            className="mr-2"
                        />
                        Customize configuration before running
                    </label>
                    <p className="text-xs text-text-secondary mt-1 ml-6">
                        Edit the configuration below for this run only. Leave off to use the
                        template's configuration as-is.
                    </p>
                </div>
            )}

            {/* Config editor — shown when a template is selected OR the run is customizing a
                template-less config. Editable only while customizing. */}
            {(selectedTemplate || customize) && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        {customize ? "Configuration (editable)" : "Configuration (from template)"}
                    </label>
                    <ConfigEditor
                        value={resolvedConfigBody}
                        language={selectedTemplate?.configFormat || "json"}
                        readOnly={!customize}
                        onChange={(value) => {
                            if (customize) setCustomBody(value || "");
                        }}
                        height="300px"
                    />
                    <p className="text-xs text-text-secondary mt-1">
                        System tag placeholders shown here are resolved per pipeline task at launch.
                    </p>
                    {customize && (
                        <div className="mt-2">
                            <SystemTagHelp />
                        </div>
                    )}
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
