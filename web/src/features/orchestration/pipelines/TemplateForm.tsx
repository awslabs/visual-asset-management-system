/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTemplate, useTemplateMutations, usePipeline } from "../api/queries";
import type { Template, ConfigFormat, TagSchemaField } from "../types";
import ConfigEditor from "../components/ConfigEditor";
import type { ConfigEditorHandle } from "../components/ConfigEditor";
import DynamicTagForm from "../components/DynamicTagForm";
import SystemTagHelp, { CONFIG_BODY_SYSTEM_TAG_INSTRUCTIONS } from "../components/SystemTagHelp";
import TagSchemaBuilder from "./TagSchemaBuilder";
import TemplateOverridesEditor from "./TemplateOverridesEditor";
import {
    TAG_KEY_PATTERN,
    placeholderFor,
    rendersJsonValue,
    unreferencedTagKeys,
    validateJsonConfigBody,
} from "./templateBodyValidation";
import Stepper from "../components/Stepper";
import InfoTooltip from "../components/InfoTooltip";
import Breadcrumb from "../components/Breadcrumb";
import Callout from "../components/Callout";
import CollapsibleSection from "../components/CollapsibleSection";
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
    { id: "config", label: "Pipeline overrides" },
    { id: "tags", label: "Tags and Config Body" },
    { id: "review", label: "Review" },
];

const OVERRIDE_LABELS: Record<string, string> = {
    inputFileArity: "Input file count",
    assetScope: "Asset selection rules",
    metadataInputs: "Metadata inputs",
    inputFileFilters: "Input file filters",
};

const fieldClass =
    "orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary";

/** A tag's default as the review table shows it. */
const formatDefault = (value: unknown): string => {
    if (value === undefined || value === null || value === "") return "—";
    return Array.isArray(value) ? value.join(", ") : String(value);
};

/**
 * Full-page create/edit Template wizard (mirrors the pipeline/workflow builder pages). Reached from
 * the pipeline's Templates list. Steps: Basic → Pipeline overrides → Tags and Config Body → Review.
 * The tag schema and the config body are authored side by side, so the body can be written against
 * the tags it references and the placeholders can be inserted rather than typed.
 */
const TemplateForm: React.FC<TemplateFormProps> = ({ mode, databaseId, pipelineId, initial }) => {
    const toast = useToast();
    const navigate = useNavigate();
    const { createTemplate, updateTemplate } = useTemplateMutations();
    const { data: pipeline } = usePipeline(databaseId, pipelineId);
    const pipelineLabel = pipeline?.pipelineName || pipelineId;
    // Templates of a system pipeline: only the config body and the tag schema may change (the backend
    // compares every other supplied field to the stored value), and none may be added.
    const systemPipeline = !!pipeline?.isSystem;
    const lockedFields = mode === "edit" && systemPipeline;
    const createRefused = mode === "create" && systemPipeline;

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
    const editorRef = useRef<ConfigEditorHandle>(null);

    const stepIndex = STEPS.findIndex((s) => s.id === wizardStep);
    const isLastStep = stepIndex === STEPS.length - 1;

    // Per-step validation: the Basic step requires a template name before advancing (and it is
    // required to save at all); the Tags step requires every tag row to be valid.
    const basicError = !templateName.trim() ? "Template name is required" : null;
    const tagsError = !tagSchemaValid ? "Fix the highlighted tag definitions to continue" : null;
    const stepError = (id: string) =>
        id === "basic" ? basicError : id === "tags" ? tagsError : null;
    const canAdvance = !stepError(wizardStep);
    // Back is always allowed; forward jumps need every step from here to the target to pass its gate.
    const canJumpTo = (targetId: string) => {
        const target = STEPS.findIndex((s) => s.id === targetId);
        if (target < 0 || target === stepIndex) return false;
        return target < stepIndex || STEPS.slice(stepIndex, target).every((s) => !stepError(s.id));
    };
    // A warning, not a save block: with allowCustomEdit the placeholder can legitimately be added to
    // the body at launch time, and the backend accepts the schema either way.
    const unreferencedTags = unreferencedTagKeys(tagSchema, configBody);
    // The save-time verdict on a json body, shown while typing. Not a save block either: the backend
    // re-checks and is the authority, and the same text comes back inline if it disagrees.
    const bodyError = validateJsonConfigBody(configBody, configFormat, tagSchema);
    // The declared tags a chip can insert — keys the renderer can substitute at all.
    const insertableTags = tagSchema.filter((field) => TAG_KEY_PATTERN.test(field.tagKey || ""));
    const overriddenKeys = Object.keys(overrides).filter((key) => overrides[key] !== undefined);

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

    const insertPlaceholder = (field: TagSchemaField) => {
        const text = placeholderFor(field, configFormat);
        if (editorRef.current) {
            editorRef.current.insertAtCursor(text);
        } else {
            setConfigBody((body) => `${body}${text}`);
        }
    };

    // Rendered on both the authoring step and Review, so the two never word the verdict differently.
    const bodyErrorCallout = bodyError ? (
        <div data-testid="config-body-error">
            <Callout tone="error">{bodyError}</Callout>
        </div>
    ) : null;
    const unreferencedWarning =
        unreferencedTags.length > 0 ? (
            <p className="text-vams-warning text-sm" data-testid="unreferenced-tags-warning">
                {unreferencedTags.join(", ")} {unreferencedTags.length === 1 ? "is" : "are"}{" "}
                declared but the config body never references{" "}
                {unreferencedTags.map((key) => `{{${key}}}`).join(", ")} — the value
                {unreferencedTags.length === 1 ? " is" : "s are"} collected on the execute form and
                then ignored.
                {allowCustomEdit
                    ? " Add the placeholder to the body, or leave it for the execute-time body edit this template allows."
                    : " Add the placeholder to the body, or remove the tag."}
            </p>
        ) : null;

    const handleSave = async () => {
        if (createRefused) return;
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

            <Stepper
                steps={STEPS}
                current={wizardStep}
                onJumpTo={setWizardStep}
                canJumpTo={canJumpTo}
            />

            <div className="orch-outline bg-surface-container border border-border-default rounded-lg p-4 space-y-4">
                {lockedFields && (
                    <div className="p-3 bg-blue-100 dark:bg-blue-900/20 text-blue-800 dark:text-blue-300 rounded">
                        <strong>System template:</strong> shipped with the deployment. Only the
                        config body and the tag schema can be changed here; a redeploy re-asserts
                        the shipped values.
                    </div>
                )}
                {createRefused && (
                    <div className="p-3 bg-yellow-100 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300 rounded">
                        Templates of system pipelines cannot be added; edit the shipped template's
                        config body and tag schema instead.
                    </div>
                )}
                {wizardStep === "basic" && (
                    <fieldset
                        disabled={lockedFields}
                        className="m-0 p-0 border-0 min-w-0 space-y-4"
                    >
                        <div>
                            <label
                                htmlFor="templateName"
                                className="block text-sm font-medium mb-1"
                            >
                                Template Name *
                            </label>
                            <input
                                id="templateName"
                                type="text"
                                value={templateName}
                                onChange={(e) => setTemplateName(e.target.value)}
                                className={fieldClass}
                                placeholder="Template name"
                            />
                            {basicError && (
                                <p className="text-vams-error text-sm mt-1">{basicError}</p>
                            )}
                        </div>
                        <div>
                            <label
                                htmlFor="templateDescription"
                                className="block text-sm font-medium mb-1"
                            >
                                Description
                            </label>
                            <textarea
                                id="templateDescription"
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                className={fieldClass}
                                rows={2}
                                placeholder="Template description"
                            />
                        </div>
                        <div>
                            <label
                                htmlFor="inputInstructions"
                                className="block text-sm font-medium mb-1"
                            >
                                Input Instructions
                            </label>
                            <textarea
                                id="inputInstructions"
                                value={inputInstructions}
                                onChange={(e) => setInputInstructions(e.target.value)}
                                // Monospace and tall enough to author a metadata-key list: these
                                // instructions are where a pipeline documents every metadata field
                                // it reads, so line breaks and alignment are load-bearing and a
                                // 2-row proportional box made that effectively unwritable.
                                className={`${fieldClass} font-mono text-xs`}
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
                    </fieldset>
                )}

                {wizardStep === "config" && (
                    <fieldset disabled={lockedFields} className="m-0 p-0 border-0 min-w-0">
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-2">
                            Pipeline setting overrides
                            <InfoTooltip text="Optional. Overrides the pipeline's input-handling settings for executions that use this template (input file count, asset selection rules, metadata inputs, input-file filters). This does NOT edit the config body. Anything left un-toggled inherits the pipeline's value." />
                        </div>
                        <p className="text-xs text-text-secondary mb-3">
                            Optional. Each setting left un-toggled inherits the pipeline&apos;s
                            value; the config body itself is authored on the next step.
                        </p>
                        <TemplateOverridesEditor
                            value={overrides}
                            onChange={setOverrides}
                            inheritedAssetScope={pipeline?.systemConfig?.assetScope}
                            inheritedArity={pipeline?.systemConfig?.inputFileArity}
                            inheritedFilters={pipeline?.systemConfig?.inputFileFilters}
                        />
                    </fieldset>
                )}

                {wizardStep === "tags" && (
                    <>
                        {/* Two panes on md and up, stacked below: the tag schema on the left, the body
                            on the right and sticky, so the editor stays in view while a long tag list
                            is edited. */}
                        <div
                            className="grid grid-cols-1 gap-4 md:grid-cols-2"
                            data-testid="tags-and-body"
                        >
                            <section className="min-w-0 space-y-2" aria-label="Tag schema">
                                <div className="flex items-center gap-1.5 text-sm font-medium">
                                    Tag Schema
                                    <InfoTooltip text="Typed tags that fill the {{tagName}} placeholders in the config body. Each tag becomes a field on the execute form." />
                                </div>
                                <p className="text-xs text-text-secondary">
                                    One execute-form field per tag. Declare a tag here, then click
                                    its chip under the editor to place the placeholder it fills.
                                </p>
                                <TagSchemaBuilder
                                    value={tagSchema}
                                    onChange={(next) => {
                                        setTagSchema(next);
                                        setTagSchemaEdited(true);
                                    }}
                                    onValidityChange={setTagSchemaValid}
                                />
                            </section>

                            <section
                                className="min-w-0 space-y-3 md:sticky md:top-4 md:self-start"
                                aria-label="Config body"
                            >
                                <div>
                                    <label
                                        htmlFor="configFormat"
                                        className="block text-sm font-medium mb-1"
                                    >
                                        Config Format *
                                    </label>
                                    <select
                                        id="configFormat"
                                        value={configFormat}
                                        onChange={(e) =>
                                            setConfigFormat(e.target.value as ConfigFormat)
                                        }
                                        disabled={lockedFields}
                                        className={fieldClass}
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
                                        ref={editorRef}
                                        value={configBody}
                                        language={configFormat}
                                        onChange={(val) => setConfigBody(val || "")}
                                        height="360px"
                                    />
                                </div>
                                {bodyErrorCallout}
                                <div>
                                    <div className="text-xs font-medium text-text-secondary mb-1">
                                        This template&apos;s tags
                                    </div>
                                    {insertableTags.length > 0 ? (
                                        <div
                                            className="flex flex-wrap gap-1.5"
                                            data-testid="template-tag-chips"
                                        >
                                            {insertableTags.map((field) => (
                                                <button
                                                    key={field.tagKey}
                                                    type="button"
                                                    data-testid="tag-chip"
                                                    data-tag-key={field.tagKey}
                                                    aria-label={`Insert {{${field.tagKey}}}`}
                                                    title={`Inserts ${placeholderFor(
                                                        field,
                                                        configFormat
                                                    )} at the cursor`}
                                                    onClick={() => insertPlaceholder(field)}
                                                    className="orch-outline inline-flex items-center gap-1 rounded-full border border-border-input bg-surface px-2 py-0.5 font-mono text-xs text-text-primary hover:bg-surface-hover"
                                                >
                                                    {`{{${field.tagKey}}}`}
                                                    <span className="font-sans text-text-secondary">
                                                        {field.type}
                                                        {configFormat === "json" &&
                                                            (rendersJsonValue(field.type)
                                                                ? " · bare"
                                                                : " · quoted")}
                                                    </span>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-xs text-text-secondary">
                                            No tags declared yet — add one on the left and its chip
                                            appears here.
                                        </p>
                                    )}
                                    <p className="mt-1 text-xs text-text-secondary">
                                        Click a chip to insert the placeholder at the cursor. In a{" "}
                                        <strong>json</strong> body a string or enum tag is inserted
                                        in quotes (<code>{'"{{KEY}}"'}</code>) because it renders
                                        text, and an integer, number, boolean or string-list tag
                                        bare (<code>{"{{KEY}}"}</code>) because it renders a JSON
                                        value of that type. Other formats always insert the bare
                                        placeholder.
                                    </p>
                                </div>
                                {unreferencedWarning}
                                <SystemTagHelp templateTags={tagSchema} />
                            </section>
                        </div>
                        {tagSchema.length > 0 && (
                            <CollapsibleSection
                                title="Execute-form preview"
                                description="How the tag fields appear on the execute form when this template is chosen."
                                defaultOpen={false}
                            >
                                <DynamicTagForm schema={tagSchema} />
                            </CollapsibleSection>
                        )}
                    </>
                )}

                {wizardStep === "review" && (
                    <div className="text-sm text-text-primary space-y-4">
                        <div className="space-y-1">
                            <div>
                                <span className="text-text-secondary">Name:</span>{" "}
                                {templateName || "—"}
                            </div>
                            {description.trim() && (
                                <div>
                                    <span className="text-text-secondary">Description:</span>{" "}
                                    {description}
                                </div>
                            )}
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
                                <span className="text-text-secondary">Input instructions:</span>{" "}
                                {inputInstructions.trim() ? "Yes" : "None"}
                            </div>
                            <div>
                                <span className="text-text-secondary">Pipeline overrides:</span>{" "}
                                {overriddenKeys.length > 0
                                    ? overriddenKeys
                                          .map((key) => OVERRIDE_LABELS[key] || key)
                                          .join(", ")
                                    : "None (inherits the pipeline's settings)"}
                            </div>
                            <div>
                                <span className="text-text-secondary">Tags:</span>{" "}
                                {tagSchema.length}
                            </div>
                        </div>
                        {isDefault && (
                            <p className="text-xs text-vams-warning">
                                Saving will make this the default template for this pipeline and
                                unset the default on any other template of this pipeline.
                            </p>
                        )}
                        {tagSchema.length > 0 && (
                            <table className="w-full text-xs" data-testid="review-tag-table">
                                <thead>
                                    <tr className="text-left text-text-secondary">
                                        <th className="py-1 pr-3 font-medium">Tag</th>
                                        <th className="py-1 pr-3 font-medium">Type</th>
                                        <th className="py-1 pr-3 font-medium">Required</th>
                                        <th className="py-1 pr-3 font-medium">Default</th>
                                        <th className="py-1 pr-3 font-medium">Label</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {tagSchema.map((field, index) => (
                                        <tr
                                            key={`${field.tagKey}-${index}`}
                                            className="border-t border-border-default"
                                        >
                                            <td className="py-1 pr-3">
                                                <code>{`{{${field.tagKey}}}`}</code>
                                            </td>
                                            <td className="py-1 pr-3">{field.type}</td>
                                            <td className="py-1 pr-3">
                                                {field.required ? "Yes" : "No"}
                                            </td>
                                            <td className="py-1 pr-3">
                                                {formatDefault(field.default)}
                                            </td>
                                            <td className="py-1 pr-3">{field.label || "—"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                        <div>
                            <div className="text-xs font-medium text-text-secondary mb-1">
                                Config body
                            </div>
                            <ConfigEditor
                                value={configBody}
                                language={configFormat}
                                readOnly
                                height="200px"
                            />
                        </div>
                        {bodyErrorCallout}
                        {unreferencedWarning}
                    </div>
                )}

                {tagsError && (
                    <p className="text-vams-error text-sm">
                        {tagsError} — the tag list shown may differ from what would be saved.
                    </p>
                )}

                {saveError && (
                    <div
                        className="orch-outline p-3 bg-red-100 dark:bg-red-900 border border-red-300 dark:border-red-700 rounded"
                        data-testid="template-save-error"
                    >
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
                                createRefused ||
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
        // Keyed on the template's identity. Every field is seeded from `initial` at mount, and a route
        // change between two edit targets under the same path pattern reuses this element — with the
        // next template already cached, `isLoading` never goes true, so without the key the form would
        // keep the previous template's name, body, tags and overrides and save them onto this one.
        <TemplateForm
            key={templateId}
            mode="edit"
            databaseId={databaseId}
            pipelineId={pipelineId}
            initial={template}
        />
    );
};
