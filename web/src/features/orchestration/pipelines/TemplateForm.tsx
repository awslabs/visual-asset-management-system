/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTemplate, useTemplateMutations, usePipeline } from "../api/queries";
import type { Template, ConfigFormat, TagSchemaField } from "../types";
import ConfigEditor from "../components/ConfigEditor";
import DynamicTagForm from "../components/DynamicTagForm";
import SystemTagHelp, { CONFIG_BODY_SYSTEM_TAG_INSTRUCTIONS } from "../components/SystemTagHelp";
import TagSchemaBuilder, { TAG_KEY_PATTERN } from "./TagSchemaBuilder";
import TemplateOverridesEditor from "./TemplateOverridesEditor";
import Stepper from "../components/Stepper";
import InfoTooltip from "../components/InfoTooltip";
import Breadcrumb from "../components/Breadcrumb";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import InstructionsPanel from "../components/InstructionsPanel";

interface TemplateFormProps {
    mode: "create" | "edit";
    databaseId: string;
    pipelineId: string;
    /** The template being edited (edit mode). */
    initial?: Template;
}

const CONFIG_FORMATS: ConfigFormat[] = ["json", "yaml", "openjd", "xml", "raw"];

// Mirrors templateBodyStorage.ABSOLUTE_CAP_BYTES — the server rejects a larger combined body.
const TEMPLATE_BODY_CAP_MB = 5;
const TEMPLATE_BODY_CAP_BYTES = TEMPLATE_BODY_CAP_MB * 1024 * 1024;

const STEPS = [
    { id: "basic", label: "Basic" },
    { id: "config", label: "Configuration" },
    { id: "tags", label: "Tags" },
    { id: "review", label: "Review" },
];

/**
 * The declared tags no `{{tagKey}}` in the body references. The renderer only substitutes tags the
 * body names, so such a tag is collected on the execute form and then dropped — matched with the
 * whitespace tolerance of the backend's own _TAG_PATTERN (common/workflows/templateRender.py). Keys
 * outside the substitutable charset are skipped: they can never be referenced, and the tag builder
 * already reports them.
 */
const unreferencedTagKeys = (schema: TagSchemaField[], body: string): string[] =>
    schema
        .map((field) => field.tagKey)
        .filter(
            (key) =>
                TAG_KEY_PATTERN.test(key || "") &&
                !new RegExp(`\\{\\{\\s*${key}\\s*\\}\\}`).test(body)
        );

/**
 * Full-page create/edit Template wizard (mirrors the pipeline/workflow builder pages). Reached from
 * the pipeline's Templates list. Steps: Basic → Configuration → Tags → Review.
 */
const TemplateForm: React.FC<TemplateFormProps> = ({ mode, databaseId, pipelineId, initial }) => {
    const toast = useToast();
    const navigate = useNavigate();
    const { createTemplate, updateTemplate } = useTemplateMutations();
    const { data: pipeline } = usePipeline(databaseId, pipelineId);
    const pipelineLabel = pipeline?.pipelineName || pipelineId;

    const [templateName, setTemplateName] = useState(initial?.templateName || "");
    const [description, setDescription] = useState(initial?.description || "");
    const [configFormat, setConfigFormat] = useState<ConfigFormat>(initial?.configFormat || "json");
    const [configBody, setConfigBody] = useState(initial?.configBody || "");
    const [inputInstructions, setInputInstructions] = useState(initial?.inputInstructions || "");
    const [allowCustomEdit, setAllowCustomEdit] = useState(initial?.allowCustomEdit || false);
    const [isDefault, setIsDefault] = useState(initial?.isDefault || false);
    // Structured overrides object (subset of pipeline systemConfig keys); empty = inherit pipeline.
    const [overrides, setOverrides] = useState<Record<string, any>>(initial?.overrides || {});
    const [tagSchema, setTagSchema] = useState<TagSchemaField[]>(initial?.tagSchema || []);
    // webFormJson is an independently authorable form definition (CLI/API), so it is rewritten from
    // the tag schema only once the tag schema is edited here.
    const [tagSchemaEdited, setTagSchemaEdited] = useState(false);
    // The tag builder withholds a row it considers invalid, so the parent schema would silently
    // lag the display. Advancing and saving are blocked while a row is invalid.
    const [tagSchemaValid, setTagSchemaValid] = useState(true);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [wizardStep, setWizardStep] = useState<string>("basic");

    const stepIndex = STEPS.findIndex((s) => s.id === wizardStep);
    const isLastStep = stepIndex === STEPS.length - 1;

    // Per-step validation: the Basic step requires a template name before advancing (and it is
    // required to save at all); the Tags step requires every tag row to be valid.
    const basicError = !templateName.trim() ? "Template name is required" : null;
    const tagsError = !tagSchemaValid ? "Fix the highlighted tag definitions to continue" : null;
    // A warning, not a save block: with allowCustomEdit the placeholder can legitimately be added to
    // the body at launch time, and the backend accepts the schema either way.
    const unreferencedTags = unreferencedTagKeys(tagSchema, configBody);
    const canAdvance =
        wizardStep === "basic" ? !basicError : wizardStep === "tags" ? !tagsError : true;

    const done = () => navigate(`/databases/${databaseId}/pipelines/${pipelineId}/templates`);

    const dirty =
        templateName !== (initial?.templateName || "") ||
        description !== (initial?.description || "") ||
        configFormat !== (initial?.configFormat || "json") ||
        configBody !== (initial?.configBody || "") ||
        inputInstructions !== (initial?.inputInstructions || "") ||
        allowCustomEdit !== (initial?.allowCustomEdit || false) ||
        isDefault !== (initial?.isDefault || false) ||
        JSON.stringify(overrides) !== JSON.stringify(initial?.overrides || {}) ||
        JSON.stringify(tagSchema) !== JSON.stringify(initial?.tagSchema || []);

    const cancel = () => {
        if (dirty && !confirm("Discard the unsaved changes to this template?")) return;
        done();
    };

    const handleSave = async () => {
        const configBodySize = new Blob([configBody]).size;
        const webFormJsonSize = new Blob([JSON.stringify(tagSchema)]).size;
        if (configBodySize + webFormJsonSize > TEMPLATE_BODY_CAP_BYTES) {
            setSaveError(
                `Combined size exceeds the ${TEMPLATE_BODY_CAP_MB}MB limit (current: ${(
                    (configBodySize + webFormJsonSize) /
                    1024 /
                    1024
                ).toFixed(2)}MB)`
            );
            return;
        }

        const templateData: Partial<Template> = {
            templateName,
            description,
            configFormat,
            configBody,
            inputInstructions,
            allowCustomEdit,
            isDefault,
            overrides,
        };

        // Only write the tag schema back when it was actually loaded for editing. The backend
        // preserves the stored schema when the field is omitted, so an edit form that never
        // received it (e.g. hydrated from a list response) cannot erase it.
        if (mode === "create" || initial?.tagSchema !== undefined) {
            templateData.tagSchema = tagSchema;
        }
        if (tagSchemaEdited) {
            templateData.webFormJson = JSON.stringify(tagSchema);
        }

        try {
            if (mode === "edit" && initial) {
                await updateTemplate.mutateAsync({
                    databaseId,
                    pipelineId,
                    templateId: initial.templateId,
                    body: templateData,
                });
            } else {
                // templateId is sent as null so the backend auto-generates it (an empty string is
                // rejected, min_length=1).
                await createTemplate.mutateAsync({
                    databaseId,
                    pipelineId,
                    body: {
                        databaseId,
                        pipelineId,
                        templateId: null,
                        ...templateData,
                    } as unknown as Template,
                });
            }
            // The page navigates away on success, so the toast is the only confirmation.
            toast.success(mode === "edit" ? "Template saved" : "Template created", {
                description: templateName || undefined,
            });
            done();
        } catch (err) {
            // Kept inline next to the Save button AND raised as a toast for a long form where the
            // inline message can sit off-screen.
            const message = toastErrorMessage(err, "Failed to save template");
            setSaveError(message);
            toast.error(mode === "edit" ? "Save failed" : "Create failed", {
                description: message,
            });
        }
    };

    return (
        <div className="orchestration-root orchestration-page space-y-6 bg-surface min-h-full">
            <div className="space-y-2">
                <Breadcrumb
                    items={[
                        { label: "Pipelines", to: `/databases/${databaseId}/pipelines` },
                        {
                            label: pipelineLabel,
                            to: `/databases/${databaseId}/pipelines/${pipelineId}`,
                        },
                        {
                            label: "Templates",
                            to: `/databases/${databaseId}/pipelines/${pipelineId}/templates`,
                        },
                        {
                            label:
                                mode === "create"
                                    ? "Create Template"
                                    : initial?.templateName || "Edit Template",
                        },
                    ]}
                />
                <h1 className="text-text-primary">
                    {mode === "create" ? "Create Template" : "Edit Template"}
                </h1>
            </div>

            <Stepper steps={STEPS} current={wizardStep} />

            <div className="orch-outline bg-surface-container border border-border-default rounded-lg p-4 space-y-4">
                {wizardStep === "basic" && (
                    <>
                        <div>
                            <label className="block text-sm font-medium mb-1">
                                Template Name *
                            </label>
                            <input
                                type="text"
                                value={templateName}
                                onChange={(e) => setTemplateName(e.target.value)}
                                className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                placeholder="Template name"
                            />
                            {basicError && (
                                <p className="text-vams-error text-sm mt-1">{basicError}</p>
                            )}
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Description</label>
                            <textarea
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                rows={2}
                                placeholder="Template description"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">
                                Input Instructions
                            </label>
                            <textarea
                                value={inputInstructions}
                                onChange={(e) => setInputInstructions(e.target.value)}
                                // Monospace and tall enough to author a metadata-key list: these
                                // instructions are where a pipeline documents every metadata field
                                // it reads, so line breaks and alignment are load-bearing and a
                                // 2-row proportional box made that effectively unwritable.
                                className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary font-mono text-xs"
                                rows={10}
                                placeholder={
                                    "Instructions shown to the person running an execution with this template.\n\n" +
                                    "Line breaks and indentation are preserved. For a pipeline that reads metadata, " +
                                    "list each key, whether it is asset- or file-level, and whether it is required."
                                }
                            />
                            <p className="mt-1 text-xs text-text-secondary">
                                Line breaks are preserved. Long instructions collapse into a hover
                                panel on the execute screen so they do not crowd out the form.
                            </p>
                            {inputInstructions.trim() && (
                                <div className="mt-2">
                                    <div className="text-xs font-medium text-text-secondary mb-1">
                                        Preview (as shown when running)
                                    </div>
                                    {/* Live preview: the inline/tooltip choice is length-based, so an
                                        author cannot otherwise tell which one their text will get. */}
                                    <InstructionsPanel
                                        text={inputInstructions}
                                        title="Instructions for this template"
                                    />
                                </div>
                            )}
                        </div>
                        <div>
                            <label className="flex items-center space-x-2">
                                <input
                                    type="checkbox"
                                    checked={isDefault}
                                    onChange={(e) => setIsDefault(e.target.checked)}
                                    className="w-4 h-4"
                                />
                                <span className="text-sm">
                                    Set as the pipeline's default template
                                </span>
                                <InfoTooltip text="The default template is pre-selected first on the execute form, and is auto-selected by the backend when a require-template pipeline runs without a template chosen. Only one template per pipeline can be the default — setting this clears any prior default." />
                            </label>
                            {isDefault && (
                                <p className="text-xs text-vams-warning mt-1">
                                    A pipeline can have only one default template. Saving this will
                                    unset the default on any other template of this pipeline.
                                </p>
                            )}
                        </div>
                    </>
                )}

                {wizardStep === "config" && (
                    <>
                        <div>
                            <label className="block text-sm font-medium mb-1">
                                Config Format *
                            </label>
                            <select
                                value={configFormat}
                                onChange={(e) => setConfigFormat(e.target.value as ConfigFormat)}
                                className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            >
                                {CONFIG_FORMATS.map((format) => (
                                    <option key={format} value={format}>
                                        {format}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <div className="flex items-center gap-1.5 text-sm font-medium mb-1">
                                Config Body
                                <InfoTooltip text={CONFIG_BODY_SYSTEM_TAG_INSTRUCTIONS} />
                            </div>
                            <ConfigEditor
                                value={configBody}
                                language={configFormat}
                                onChange={(val) => setConfigBody(val || "")}
                                height="300px"
                            />
                            <div className="mt-2">
                                <SystemTagHelp />
                            </div>
                        </div>
                        <div>
                            <label className="flex items-center space-x-2">
                                <input
                                    type="checkbox"
                                    checked={allowCustomEdit}
                                    onChange={(e) => setAllowCustomEdit(e.target.checked)}
                                    className="w-4 h-4"
                                />
                                <span className="text-sm">
                                    Allow editing the config body at execution time
                                </span>
                                <InfoTooltip text="When on, the person running an execution with this template may edit the config body inline before launch (a one-off change for that run)." />
                            </label>
                        </div>
                        <div>
                            <div className="flex items-center gap-1.5 text-sm font-medium mb-2">
                                Pipeline setting overrides
                                <InfoTooltip text="Optional. Overrides the pipeline's input-handling settings for executions that use this template (input file count, asset selection rules, metadata inputs, input-file filters). This does NOT edit the config body. Anything left un-toggled inherits the pipeline's value." />
                            </div>
                            <TemplateOverridesEditor
                                value={overrides}
                                onChange={setOverrides}
                                inheritedAssetScope={pipeline?.systemConfig?.assetScope}
                                inheritedArity={pipeline?.systemConfig?.inputFileArity}
                                inheritedFilters={pipeline?.systemConfig?.inputFileFilters}
                            />
                        </div>
                    </>
                )}

                {wizardStep === "tags" && (
                    <>
                        <div>
                            <div className="flex items-center gap-1.5 text-sm font-medium mb-1">
                                Tag Schema
                                <InfoTooltip text="Typed tags that fill the {{tagName}} placeholders in the config body. Each tag becomes a field on the execute form." />
                            </div>
                            <p className="text-xs text-text-secondary mb-2">
                                These tags define the execute-time form for this template — one
                                input field per tag. They fill the <code>{"{{tagName}}"}</code>{" "}
                                placeholders in the config body.
                            </p>
                            <TagSchemaBuilder
                                value={tagSchema}
                                onChange={(next) => {
                                    setTagSchema(next);
                                    setTagSchemaEdited(true);
                                }}
                                onValidityChange={setTagSchemaValid}
                            />
                        </div>
                        {tagSchema.length > 0 && (
                            <div>
                                <div className="flex items-center gap-1.5 text-sm font-medium mb-2">
                                    Live preview
                                    <InfoTooltip text="How the tag fields appear on the execute form when this template is chosen." />
                                </div>
                                <div className="orch-outline rounded-lg border border-border-default bg-surface-secondary p-4">
                                    <div className="text-xs text-text-secondary mb-3">
                                        Execute-form preview
                                    </div>
                                    <DynamicTagForm schema={tagSchema} />
                                </div>
                            </div>
                        )}
                    </>
                )}

                {wizardStep === "review" && (
                    <div className="text-sm text-text-primary space-y-1">
                        <div>
                            <span className="text-text-secondary">Name:</span> {templateName || "—"}
                        </div>
                        <div>
                            <span className="text-text-secondary">Format:</span> {configFormat}
                        </div>
                        <div>
                            <span className="text-text-secondary">Allow custom edit:</span>{" "}
                            {allowCustomEdit ? "Yes" : "No"}
                        </div>
                        <div>
                            <span className="text-text-secondary">Default template:</span>{" "}
                            {isDefault ? "Yes" : "No"}
                        </div>
                        <div>
                            <span className="text-text-secondary">Tags:</span> {tagSchema.length}
                        </div>
                        {isDefault && (
                            <p className="text-xs text-vams-warning pt-1">
                                Saving will make this the default template for this pipeline and
                                unset the default on any other template of this pipeline.
                            </p>
                        )}
                    </div>
                )}

                {tagsError && (
                    <p className="text-vams-error text-sm">
                        {tagsError} — the tag list shown may differ from what would be saved.
                    </p>
                )}

                {(wizardStep === "tags" || wizardStep === "review") &&
                    unreferencedTags.length > 0 && (
                        <p className="text-vams-warning text-sm">
                            {unreferencedTags.join(", ")}{" "}
                            {unreferencedTags.length === 1 ? "is" : "are"} declared but the config
                            body never references{" "}
                            {unreferencedTags.map((key) => `{{${key}}}`).join(", ")} — the value
                            {unreferencedTags.length === 1 ? " is" : "s are"} collected on the
                            execute form and then ignored.
                            {allowCustomEdit
                                ? " Add the placeholder to the body, or leave it for the execute-time body edit this template allows."
                                : " Add the placeholder to the body, or remove the tag."}
                        </p>
                    )}

                {saveError && (
                    <div className="orch-outline p-3 bg-red-100 dark:bg-red-900 border border-red-300 dark:border-red-700 rounded">
                        <p className="text-sm text-red-800 dark:text-red-200 whitespace-pre-line">
                            {saveError}
                        </p>
                    </div>
                )}
            </div>

            <div className="flex justify-between gap-2">
                <button onClick={cancel} className={btnSecondary}>
                    Cancel
                </button>
                <div className="flex gap-2">
                    {stepIndex > 0 && (
                        <button
                            onClick={() => setWizardStep(STEPS[stepIndex - 1].id)}
                            className={btnSecondary}
                        >
                            Back
                        </button>
                    )}
                    {!isLastStep ? (
                        <button
                            onClick={() => setWizardStep(STEPS[stepIndex + 1].id)}
                            disabled={!canAdvance}
                            className={btnPrimary}
                        >
                            Next
                        </button>
                    ) : (
                        <button
                            onClick={handleSave}
                            disabled={
                                !!basicError ||
                                !!tagsError ||
                                createTemplate.isPending ||
                                updateTemplate.isPending
                            }
                            className={btnPrimary}
                        >
                            {createTemplate.isPending || updateTemplate.isPending
                                ? "Saving..."
                                : "Save"}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default TemplateForm;

/**
 * Edit-mode wrapper that loads the single template by id. This must use the single-template GET
 * (not the templates list): the list response omits tagSchema and blanks S3-offloaded bodies, and
 * the form writes every field back on save.
 */
export const TemplateFormEditLoader: React.FC<{
    databaseId: string;
    pipelineId: string;
    templateId: string;
}> = ({ databaseId, pipelineId, templateId }) => {
    const { data: template, isLoading } = useTemplate(databaseId, pipelineId, templateId);
    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <div className="orch-outline inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400 mb-3" />
                    <p className="text-text-secondary">Loading template…</p>
                </div>
            </div>
        );
    }
    if (!template) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <p className="text-vams-error text-xl">Template not found</p>
            </div>
        );
    }
    return (
        <TemplateForm
            mode="edit"
            databaseId={databaseId}
            pipelineId={pipelineId}
            initial={template}
        />
    );
};
