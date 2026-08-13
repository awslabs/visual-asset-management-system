/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo, useRef } from "react";
import CollapsibleSection from "../components/CollapsibleSection";
import InstructionsPanel from "../components/InstructionsPanel";
import ConfigEditor from "../components/ConfigEditor";
import DynamicTagForm, { formDataToTags } from "../components/DynamicTagForm";
import SystemTagHelp from "../components/SystemTagHelp";
import { useTemplates, useTemplate } from "../api/queries";
import { resolvePipelineParams, hasDeclaredDefault } from "./resolveTemplate";
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
    const { data: templates, isLoading: templatesLoading } = useTemplates(
        pipeline.databaseId,
        pipeline.pipelineId
    );

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
    // Opens the tag catalog from the icon next to the resolve-time note, independently of the
    // customize toggle.
    const [tagHelpOpen, setTagHelpOpen] = useState(false);

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

    // The tag form belongs to ONE template, so a template change re-seeds it from that template's own
    // tagSchema defaults. The run's already-entered tags (revisiting the step) are restored only for
    // the template the run was carrying — values entered against a different template are not its
    // values, and its schema may not even declare those keys.
    const runTags = useRef(data?.tags);
    const runTemplateId = useRef(data?.templateId);
    // The templates LIST omits tagSchema, so a template picked before its detail arrives seeds from an
    // incomplete schema; the seed is redone once the schema is in hand.
    const seedKey = `${selectedTemplateId || ""}:${selectedTemplate?.tagSchema ? "schema" : "row"}`;
    const [seededFor, setSeededFor] = useState<string | undefined>(undefined);
    useEffect(() => {
        if (seedKey === seededFor) return;
        setSeededFor(seedKey);

        const formData: Record<string, any> = {};
        // A blank optional tag is left out rather than seeded: the backend materializes an empty value
        // for the types that have one, and a metadata fallback fills the rest.
        (selectedTemplate?.tagSchema || []).forEach((field) => {
            if (hasDeclaredDefault(field)) {
                formData[field.tagKey] = field.default;
            }
        });
        if (selectedTemplateId === runTemplateId.current) {
            (runTags.current || []).forEach((tag) => {
                formData[tag.key] = tag.value;
            });
        }
        setTagFormData(formData);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [seedKey]);

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

    // Every block below is conditional, so a pipeline with no templates, no tag schema, and no
    // customize grant rendered nothing but the heading — a blank step that reads as still loading or
    // broken rather than as "this pipeline takes no configuration". `templatesLoading` is excluded
    // deliberately: saying "nothing to configure" while the list is still in flight would be wrong.
    const hasTemplates = !!templates && templates.length > 0;
    const hasTagFields = !!selectedTemplate?.tagSchema && selectedTemplate.tagSchema.length > 0;
    const showsConfigBody = !!selectedTemplate || customize;
    const nothingToConfigure =
        !templatesLoading && !hasTemplates && !hasTagFields && !showsConfigBody && !canCustomize;

    return (
        <div className="space-y-4">
            <h3 className="text-lg font-semibold text-text-primary">{pipeline.pipelineName}</h3>

            {nothingToConfigure && (
                <div className="orch-outline p-3 border border-border-default rounded bg-surface-secondary">
                    <p className="text-sm text-text-primary">
                        This pipeline takes no run-time configuration.
                    </p>
                    <p className="mt-1 text-xs text-text-secondary">
                        It defines no configuration templates, and it does not allow a custom
                        configuration for a single run. Continue to the next step — it will run with
                        its built-in settings.
                    </p>
                </div>
            )}

            {/* Template selection */}
            {templates && templates.length > 0 && (
                <div>
                    <label className="block text-sm font-medium text-text-primary mb-2">
                        Template
                    </label>
                    <select
                        value={selectedTemplateId || ""}
                        onChange={(e) => handleTemplateChange(e.target.value)}
                        className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                    >
                        <option value="">-- Select Template --</option>
                        {templates.map((tpl) => (
                            <option key={tpl.templateId} value={tpl.templateId}>
                                {tpl.templateName}
                            </option>
                        ))}
                    </select>
                    {/* The template's own description, then its instructions. Both were previously
                        only visible in the template EDITOR, so the person actually running the
                        pipeline never saw the guidance written for them. */}
                    {selectedTemplate?.description && (
                        <p className="mt-2 text-xs text-text-secondary">
                            {selectedTemplate.description}
                        </p>
                    )}
                    {selectedTemplate?.inputInstructions && (
                        <div className="mt-2">
                            <InstructionsPanel
                                text={selectedTemplate.inputInstructions}
                                title="Instructions for this template"
                            />
                        </div>
                    )}
                </div>
            )}

            {/* Tag form */}
            {selectedTemplate?.tagSchema && selectedTemplate.tagSchema.length > 0 && (
                <div>
                    <div className="text-sm font-semibold text-text-primary">Template inputs</div>
                    <p className="text-xs text-text-secondary mb-2">
                        Values this template asks for. A field left blank uses the template&apos;s
                        own default, or falls back to the asset&apos;s metadata where the pipeline
                        supports it.
                    </p>
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
                <CollapsibleSection
                    title={customize ? "Configuration (editable)" : "Configuration (from template)"}
                    description={
                        customize
                            ? "Edit the body sent for this run."
                            : "The body this run will send. Expand to review it."
                    }
                    // Collapsed by default: 300px of monospace dominated the step, and a read-only
                    // body is reference material rather than something to fill in. Opens
                    // automatically while customizing, since then it IS the thing being edited.
                    defaultOpen={customize}
                >
                    <ConfigEditor
                        value={resolvedConfigBody}
                        language={selectedTemplate?.configFormat || "json"}
                        readOnly={!customize}
                        onChange={(value) => {
                            if (customize) setCustomBody(value || "");
                        }}
                        height="300px"
                    />
                    <div className="mt-1 flex items-start gap-1.5">
                        <p className="text-xs text-text-secondary">
                            Dynamic and system tag placeholders are resolved per pipeline task at
                            launch.
                        </p>
                        {/* The full catalog is reachable from an icon as well as the panel below:
                            while editing a config body the question is "what can I write here?", and
                            an icon next to the note answers it without scrolling past the editor. */}
                        {canCustomize && (
                            <button
                                type="button"
                                aria-label="Show available template tags"
                                aria-expanded={tagHelpOpen}
                                onClick={() => setTagHelpOpen((o) => !o)}
                                className="orch-outline inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-text-secondary text-[10px] leading-none text-text-secondary hover:bg-surface-secondary focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                                i
                            </button>
                        )}
                    </div>
                    {/* Expanded either by the icon or because the run is customizing — that is when
                        the placeholders are actually actionable. */}
                    {(customize || tagHelpOpen) && (
                        <div className="mt-2">
                            <SystemTagHelp defaultOpen={tagHelpOpen} />
                        </div>
                    )}
                </CollapsibleSection>
            )}

            {/* Validation errors */}
            {validationResult.errors.length > 0 && (
                <div className="orch-outline p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
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
