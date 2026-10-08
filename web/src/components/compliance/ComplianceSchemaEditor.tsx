/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useEffect, useCallback, useState } from "react";
import Editor, { OnMount, OnValidate, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import SpaceBetween from "@cloudscape-design/components/space-between";

(self as any).MonacoEnvironment = {
    getWorker(_: any, label: string) {
        if (label === "json") {
            return new jsonWorker();
        }
        return new editorWorker();
    },
};

loader.config({ monaco });
import Box from "@cloudscape-design/components/box";
import Tabs from "@cloudscape-design/components/tabs";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Toggle from "@cloudscape-design/components/toggle";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Header from "@cloudscape-design/components/header";
import Alert from "@cloudscape-design/components/alert";
import RadioGroup from "@cloudscape-design/components/radio-group";
import AttributeEditor from "@cloudscape-design/components/attribute-editor";
import type { editor } from "monaco-editor";
import Synonyms from "../../synonyms";
import {
    CompliancePipelineInputFilesMode,
    CompliancePipelineRef,
    PIPELINE_INPUT_FILES_MODES,
    VAMS_RULES_V1_FORMAT,
} from "../../services/ComplianceService";
import {
    COMPLIANCE_ID_PATTERN,
    ENFORCEMENT_OPTIONS,
    INPUT_FILES_MAX_FILTERS,
    INPUT_FILES_MAX_FILTER_LENGTH,
    INPUT_FILES_MAX_KEYS,
    INPUT_FILES_MODE_OPTIONS,
    InputFilesDraft,
    PIPELINE_REF_FIELDS,
    RulesDraft,
    bodyFromRulesDraft,
    isVamsRulesBody,
    newPipelineRuleDraft,
    rulesDraftFromBody,
    validateInputFiles,
    validatePipelineRef,
} from "./complianceSchemaRules";

const ID_STRING = { type: "string", pattern: COMPLIANCE_ID_PATTERN.source };

const PIPELINE_REF_META_SCHEMA = {
    type: "object",
    required: ["databaseId", "workflowId", "pipelineDatabaseId", "pipelineId"],
    properties: {
        databaseId: {
            ...ID_STRING,
            description: `Workflow ${Synonyms.database}: the ${Synonyms.database} the workflow belongs to, or GLOBAL.`,
        },
        workflowId: { ...ID_STRING, description: "Workflow ID: the workflow to run." },
        pipelineDatabaseId: {
            ...ID_STRING,
            description: `Pipeline ${Synonyms.database}: the ${Synonyms.database} the pipeline belongs to, or GLOBAL.`,
        },
        pipelineId: {
            ...ID_STRING,
            description: "Pipeline ID: the pipeline in the workflow whose output is checked.",
        },
        templateId: {
            ...ID_STRING,
            description:
                "Template ID (optional): pipeline template applied when the workflow runs.",
        },
    },
    additionalProperties: false,
};

const INPUT_FILES_META_SCHEMA = {
    type: "object",
    required: ["mode"],
    description: `Which of the ${Synonyms.asset}'s files the workflow receives when the rule runs. Absent means matching.`,
    properties: {
        mode: {
            type: "string",
            enum: PIPELINE_INPUT_FILES_MODES,
            description: `matching: the ${Synonyms.asset}'s files passing the workflow's, the pipeline's and this rule's filters; wholeAsset: the ${Synonyms.asset} root, where the workflow allows it; explicit: exactly the listed keys.`,
        },
        filter: {
            type: "array",
            maxItems: INPUT_FILES_MAX_FILTERS,
            items: { type: "string", minLength: 1, maxLength: INPUT_FILES_MAX_FILTER_LENGTH },
            description:
                "Glob allow list applied after the workflow's and pipeline's own input filters (matching only). A single-input workflow must end with exactly one file.",
        },
        keys: {
            type: "array",
            minItems: 1,
            maxItems: INPUT_FILES_MAX_KEYS,
            items: { type: "string", pattern: "^/.+" },
            description: `${Synonyms.Asset}-relative file paths beginning with / (explicit only).`,
        },
    },
    additionalProperties: false,
    allOf: [
        {
            if: { required: ["mode"], properties: { mode: { const: "matching" } } },
            then: { not: { required: ["keys"] } },
        },
        {
            if: { required: ["mode"], properties: { mode: { const: "wholeAsset" } } },
            then: { not: { anyOf: [{ required: ["keys"] }, { required: ["filter"] }] } },
        },
        {
            if: { required: ["mode"], properties: { mode: { const: "explicit" } } },
            then: { required: ["keys"], not: { required: ["filter"] } },
        },
    ],
};

const TOLERANCE_META_SCHEMA = {
    type: "object",
    required: ["operator"],
    properties: {
        operator: { type: "string", enum: ["lte", "gte", "eq", "between"] },
        value: { type: "number", description: "Bound for lte, gte and eq." },
        min: { type: "number", description: "Lower bound for between." },
        max: { type: "number", description: "Upper bound for between." },
        epsilon: { type: "number", description: "Allowed difference for eq." },
    },
};

const RULE_META_SCHEMA = {
    type: "object",
    required: ["ruleType", "enforcement", "checks"],
    properties: {
        ruleType: {
            type: "string",
            enum: ["pipeline", "metadata", "relationship"],
            description: `What the rule inspects: a workflow run, the metadata, or the ${Synonyms.asset} links.`,
        },
        enforcement: {
            type: "string",
            enum: ["quarantine", "warn", "inform"],
            description: `What a failed check does to the ${Synonyms.asset}.`,
        },
        pipelineRef: PIPELINE_REF_META_SCHEMA,
        inputFiles: INPUT_FILES_META_SCHEMA,
        inputParameters: {
            type: "object",
            description: "Parameters handed to the workflow execution (pipeline rules).",
        },
        metadataSchemaRef: {
            type: "object",
            required: ["databaseId", "schemaName"],
            properties: { databaseId: ID_STRING, schemaName: { type: "string", minLength: 1 } },
        },
        checks: {
            type: "array",
            minItems: 1,
            items: {
                type: "object",
                required: ["name"],
                properties: {
                    name: { type: "string", minLength: 1 },
                    description: { type: "string" },
                    outputField: {
                        type: "string",
                        description: "Pipeline output field compared against the tolerance.",
                    },
                    tolerance: TOLERANCE_META_SCHEMA,
                    validateRequired: { type: "boolean" },
                    validateTypes: { type: "boolean" },
                    additionalRequiredFields: { type: "array", items: { type: "string" } },
                    direction: { type: "string", enum: ["parents", "children", "related"] },
                    relationshipType: { type: "string", enum: ["parentChild", "related"] },
                    minCount: { type: "integer", minimum: 0 },
                    maxCount: { type: "integer", minimum: 0 },
                },
            },
        },
    },
    allOf: [
        {
            if: { properties: { ruleType: { const: "pipeline" } } },
            then: { required: ["pipelineRef"] },
        },
        {
            if: { properties: { ruleType: { const: "metadata" } } },
            then: { required: ["metadataSchemaRef"] },
        },
    ],
};

const VAMS_RULES_META_SCHEMA = {
    type: "object",
    required: ["schemaFormat", "rules"],
    properties: {
        schemaFormat: { type: "string", const: VAMS_RULES_V1_FORMAT },
        extends: {
            type: "string",
            description: "Name of a parent schema whose rules this schema inherits.",
        },
        rules: {
            type: "object",
            minProperties: 1,
            additionalProperties: RULE_META_SCHEMA,
            description: "Named rules; a child rule replaces a parent rule of the same name.",
        },
    },
    additionalProperties: false,
};

const JSON_SCHEMA_META_SCHEMA = {
    type: "object",
    required: ["type"],
    properties: {
        type: {
            type: "string",
            enum: ["object", "array", "string", "number", "integer", "boolean", "null"],
            description: "The root type of the schema. Usually 'object' for compliance schemas.",
        },
        required: {
            type: "array",
            items: { type: "string" },
            description: "List of property names that are required.",
        },
        properties: {
            type: "object",
            additionalProperties: {
                type: "object",
                properties: {
                    type: {
                        type: "string",
                        enum: ["string", "number", "integer", "boolean", "array", "object", "null"],
                    },
                    description: { type: "string" },
                    enum: { type: "array" },
                    minimum: { type: "number" },
                    maximum: { type: "number" },
                    minLength: { type: "integer", minimum: 0 },
                    maxLength: { type: "integer", minimum: 0 },
                    pattern: { type: "string" },
                    items: { type: "object" },
                    default: {},
                },
            },
            description: "Define the fields (properties) of the schema.",
        },
        additionalProperties: {
            oneOf: [{ type: "boolean" }, { type: "object" }],
            description: "Whether to allow properties not defined in 'properties'.",
        },
        description: { type: "string" },
        title: { type: "string" },
    },
};

/**
 * Meta-schema Monaco validates the body against. A body carrying `schemaFormat` is a
 * vams-rules-v1 rule set, the only format the API registers; the JSON Schema branch keeps
 * an existing legacy body readable in the JSON view.
 */
const COMPLIANCE_META_SCHEMA = {
    uri: "http://vams/compliance-schema",
    fileMatch: ["*"],
    schema: {
        type: "object",
        if: { required: ["schemaFormat"] },
        then: VAMS_RULES_META_SCHEMA,
        else: JSON_SCHEMA_META_SCHEMA,
    },
};

interface PropertyDef {
    name: string;
    type: string;
    description: string;
    required: boolean;
    enumValues: string;
    minimum: string;
    maximum: string;
    minLength: string;
    maxLength: string;
}

const EMPTY_PROPERTY: PropertyDef = {
    name: "",
    type: "string",
    description: "",
    required: false,
    enumValues: "",
    minimum: "",
    maximum: "",
    minLength: "",
    maxLength: "",
};

const TYPE_OPTIONS = [
    { value: "string", label: "String" },
    { value: "number", label: "Number" },
    { value: "integer", label: "Integer" },
    { value: "boolean", label: "Boolean" },
    { value: "array", label: "Array" },
    { value: "object", label: "Object" },
];

const EMPTY_RULES_DRAFT: RulesDraft = { extends: "", rules: [] };

const cardStyle: React.CSSProperties = {
    padding: "12px",
    border: "1px solid var(--color-border-divider-default, #eaeded)",
    borderRadius: "8px",
};

interface StringListEditorProps {
    items: string[];
    onChange: (items: string[]) => void;
    /** Label of every entry's field, e.g. "Glob pattern"; also names the entries to screen readers. */
    itemLabel: string;
    addButtonText: string;
    placeholder: string;
    empty: string;
    max: number;
    disabled?: boolean;
}

/** One text field per entry with add and remove controls, for a rule's globs or file paths. */
function StringListEditor({
    items,
    onChange,
    itemLabel,
    addButtonText,
    placeholder,
    empty,
    max,
    disabled = false,
}: StringListEditorProps) {
    const lower = itemLabel.toLowerCase();
    return (
        <AttributeEditor<string>
            items={items}
            addButtonText={addButtonText}
            addButtonVariant="inline-link"
            removeButtonText="Remove"
            removeButtonAriaLabel={(item) => `Remove ${lower} ${item || "(empty)"}`}
            disableAddButton={disabled || items.length >= max}
            isItemRemovable={() => !disabled}
            empty={empty}
            onAddButtonClick={() => onChange([...items, ""])}
            onRemoveButtonClick={({ detail }) =>
                onChange(items.filter((_, i) => i !== detail.itemIndex))
            }
            definition={[
                {
                    label: itemLabel,
                    control: (item, index) => (
                        <Input
                            value={item}
                            ariaLabel={`${itemLabel} ${index + 1}`}
                            placeholder={placeholder}
                            disabled={disabled}
                            onChange={({ detail }) =>
                                onChange(items.map((v, i) => (i === index ? detail.value : v)))
                            }
                        />
                    ),
                },
            ]}
        />
    );
}

interface ComplianceSchemaEditorProps {
    value: string;
    onChange: (value: string) => void;
    readOnly?: boolean;
}

export default function ComplianceSchemaEditor({
    value,
    onChange,
    readOnly = false,
}: ComplianceSchemaEditorProps) {
    const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
    const [markers, setMarkers] = useState<editor.IMarkerData[]>([]);
    const [activeTab, setActiveTab] = useState("json");
    // Which body format the Visual Builder is editing; decided from the JSON on each sync.
    const [builderMode, setBuilderMode] = useState<"properties" | "rules">("properties");
    const [properties, setProperties] = useState<PropertyDef[]>([]);
    const [additionalProperties, setAdditionalProperties] = useState(true);
    const [rulesDraft, setRulesDraft] = useState<RulesDraft>(EMPTY_RULES_DRAFT);
    // The JSON the builder last wrote. A value equal to it is the builder's own output, so
    // re-deriving the builder state from it would only drop rows the user is still filling in.
    const lastEmittedJson = useRef<string | null>(null);

    const emit = useCallback(
        (json: string) => {
            lastEmittedJson.current = json;
            onChange(json);
        },
        [onChange]
    );

    const syncWizardFromJson = useCallback((json: string) => {
        try {
            const parsed = JSON.parse(json);

            if (isVamsRulesBody(parsed)) {
                setRulesDraft(rulesDraftFromBody(parsed));
                setBuilderMode("rules");
                return;
            }

            // The API accepts only vams-rules-v1 bodies, so any other document opens as an empty rule set.
            setRulesDraft(rulesDraftFromBody({ schemaFormat: VAMS_RULES_V1_FORMAT, rules: {} }));
            setBuilderMode("rules");
        } catch {
            // Don't sync on invalid JSON
        }
    }, []);

    const buildJsonFromWizard = useCallback(() => {
        const schema: any = { type: "object" };
        const required: string[] = [];
        const props: any = {};

        for (const p of properties) {
            if (!p.name.trim()) continue;
            const propDef: any = { type: p.type };
            if (p.description) propDef.description = p.description;
            if (p.enumValues.trim()) {
                propDef.enum = p.enumValues
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean);
            }
            if (p.minimum !== "") propDef.minimum = Number(p.minimum);
            if (p.maximum !== "") propDef.maximum = Number(p.maximum);
            if (p.minLength !== "") propDef.minLength = Number(p.minLength);
            if (p.maxLength !== "") propDef.maxLength = Number(p.maxLength);
            if (p.required) required.push(p.name.trim());
            props[p.name.trim()] = propDef;
        }

        if (required.length > 0) schema.required = required;
        schema.properties = props;
        schema.additionalProperties = additionalProperties;

        return JSON.stringify(schema, null, 2);
    }, [properties, additionalProperties]);

    useEffect(() => {
        if (activeTab === "wizard" && value !== lastEmittedJson.current) {
            syncWizardFromJson(value);
        }
    }, [activeTab, value, syncWizardFromJson]);

    const handleEditorMount: OnMount = (editor, monaco) => {
        editorRef.current = editor;

        (monaco.languages.json as any).jsonDefaults.setDiagnosticsOptions({
            validate: true,
            schemas: [COMPLIANCE_META_SCHEMA],
            enableSchemaRequest: false,
            allowComments: false,
            trailingCommas: "error",
        });
    };

    const handleValidation: OnValidate = (newMarkers) => {
        setMarkers(newMarkers);
    };

    const handleEditorChange = (newValue: string | undefined) => {
        if (newValue !== undefined) {
            onChange(newValue);
        }
    };

    const handleWizardChange = () => {
        emit(buildJsonFromWizard());
    };

    const updateProperty = (index: number, field: keyof PropertyDef, val: any) => {
        const updated = [...properties];
        updated[index] = { ...updated[index], [field]: val };
        setProperties(updated);
    };

    const addProperty = () => {
        setProperties([...properties, { ...EMPTY_PROPERTY }]);
    };

    const removeProperty = (index: number) => {
        setProperties(properties.filter((_, i) => i !== index));
    };

    useEffect(() => {
        if (activeTab === "wizard" && builderMode === "properties" && properties.length > 0) {
            handleWizardChange();
        }
    }, [properties, additionalProperties]);

    const applyRulesDraft = (next: RulesDraft) => {
        setRulesDraft(next);
        emit(JSON.stringify(bodyFromRulesDraft(next), null, 2));
    };

    const updateRule = (index: number, patch: Partial<RulesDraft["rules"][number]>) => {
        const rules = [...rulesDraft.rules];
        rules[index] = { ...rules[index], ...patch };
        applyRulesDraft({ ...rulesDraft, rules });
    };

    const updatePipelineRef = (index: number, key: keyof CompliancePipelineRef, val: string) => {
        const rule = rulesDraft.rules[index];
        updateRule(index, { pipelineRef: { ...rule.pipelineRef, [key]: val } });
    };

    const updateInputFiles = (index: number, patch: Partial<InputFilesDraft>) => {
        const rule = rulesDraft.rules[index];
        updateRule(index, { inputFiles: { ...rule.inputFiles, ...patch } });
    };

    const addPipelineRule = () => {
        const names = rulesDraft.rules.map((r) => r.name);
        applyRulesDraft({
            ...rulesDraft,
            rules: [...rulesDraft.rules, newPipelineRuleDraft(names)],
        });
    };

    const removeRule = (index: number) => {
        applyRulesDraft({
            ...rulesDraft,
            rules: rulesDraft.rules.filter((_, i) => i !== index),
        });
    };

    const formatDocument = () => {
        if (editorRef.current) {
            editorRef.current.getAction("editor.action.formatDocument")?.run();
        }
    };

    const errorCount = markers.filter((m) => m.severity === 8).length;
    const warningCount = markers.filter((m) => m.severity === 4).length;

    const renderInputFiles = (idx: number, inputFiles: InputFilesDraft) => {
        const errors = validateInputFiles(inputFiles);
        return (
            <SpaceBetween size="s">
                <FormField
                    label="Input files"
                    description={`Which of the ${Synonyms.asset}'s files the workflow receives when the rule runs.`}
                    errorText={errors.mode}
                    stretch
                >
                    <RadioGroup
                        value={inputFiles.mode}
                        onChange={({ detail }) =>
                            updateInputFiles(idx, {
                                mode: detail.value as CompliancePipelineInputFilesMode,
                            })
                        }
                        items={INPUT_FILES_MODE_OPTIONS.map((option) => ({
                            value: option.value,
                            label: option.label,
                            description: option.description,
                            disabled: readOnly,
                        }))}
                        ariaRequired
                    />
                </FormField>
                {inputFiles.mode === "matching" && (
                    <FormField
                        label="File globs (optional)"
                        description="Applied after the workflow's and the pipeline's own input filters. A single-input workflow must be left with exactly one matching file."
                        constraintText={`Up to ${INPUT_FILES_MAX_FILTERS} globs.`}
                        errorText={errors.filter}
                        stretch
                    >
                        <StringListEditor
                            items={inputFiles.filter}
                            onChange={(filter) => updateInputFiles(idx, { filter })}
                            itemLabel="Glob pattern"
                            addButtonText="Add glob"
                            placeholder="*.glb"
                            empty="No globs: every file the workflow and pipeline filters accept is a candidate."
                            max={INPUT_FILES_MAX_FILTERS}
                            disabled={readOnly}
                        />
                    </FormField>
                )}
                {inputFiles.mode === "explicit" && (
                    <FormField
                        label="File paths"
                        description={`${Synonyms.Asset}-relative paths beginning with /; each must exist on the ${Synonyms.asset} when the rule runs.`}
                        constraintText={`At least one and up to ${INPUT_FILES_MAX_KEYS} paths.`}
                        errorText={errors.keys}
                        stretch
                    >
                        <StringListEditor
                            items={inputFiles.keys}
                            onChange={(keys) => updateInputFiles(idx, { keys })}
                            itemLabel="File path"
                            addButtonText="Add file path"
                            placeholder="/models/part.stl"
                            empty="No file paths listed."
                            max={INPUT_FILES_MAX_KEYS}
                            disabled={readOnly}
                        />
                    </FormField>
                )}
            </SpaceBetween>
        );
    };

    const renderRulesBuilder = () => (
        <SpaceBetween size="m">
            <Header
                variant="h3"
                description="Rules of a vams-rules-v1 schema. A pipeline rule runs a workflow on the input files it selects and compares the output against tolerances; checks and input parameters are edited in the JSON Editor tab and kept as they are."
                actions={
                    <Button onClick={addPipelineRule} disabled={readOnly}>
                        Add pipeline rule
                    </Button>
                }
            >
                Compliance Rules
            </Header>

            <FormField
                label="Extends (optional)"
                description="Parent schema whose rules this schema inherits."
            >
                <Input
                    value={rulesDraft.extends}
                    onChange={({ detail }) =>
                        applyRulesDraft({ ...rulesDraft, extends: detail.value })
                    }
                    placeholder="parent-schema-name"
                    disabled={readOnly}
                />
            </FormField>

            {rulesDraft.rules.length === 0 && (
                <Box textAlign="center" color="text-status-inactive" padding="l">
                    No rules defined. Click "Add pipeline rule" to start.
                </Box>
            )}

            {rulesDraft.rules.map((rule, idx) => {
                const refErrors =
                    rule.ruleType === "pipeline" ? validatePipelineRef(rule.pipelineRef) : {};
                const duplicateName =
                    rulesDraft.rules.filter((r) => r.name.trim() === rule.name.trim()).length > 1;
                return (
                    <div key={idx} style={cardStyle}>
                        <SpaceBetween size="s">
                            <ColumnLayout columns={3}>
                                <FormField
                                    label="Rule name"
                                    errorText={
                                        !rule.name.trim()
                                            ? "Rule name is required"
                                            : duplicateName
                                            ? "Rule names must be unique"
                                            : undefined
                                    }
                                >
                                    <Input
                                        value={rule.name}
                                        onChange={({ detail }) =>
                                            updateRule(idx, { name: detail.value })
                                        }
                                        placeholder="rule-name"
                                        disabled={readOnly}
                                    />
                                </FormField>
                                <FormField label="Rule type">
                                    <Input value={rule.ruleType} readOnly disabled />
                                </FormField>
                                <FormField label="Enforcement">
                                    <Select
                                        selectedOption={
                                            ENFORCEMENT_OPTIONS.find(
                                                (o) => o.value === rule.enforcement
                                            ) || ENFORCEMENT_OPTIONS[1]
                                        }
                                        onChange={({ detail }) =>
                                            updateRule(idx, {
                                                enforcement: detail.selectedOption.value as any,
                                            })
                                        }
                                        options={ENFORCEMENT_OPTIONS}
                                        disabled={readOnly}
                                    />
                                </FormField>
                            </ColumnLayout>
                            {rule.ruleType === "pipeline" && (
                                <ColumnLayout columns={2}>
                                    {PIPELINE_REF_FIELDS.map((field) => (
                                        <FormField
                                            key={field.key}
                                            label={field.label}
                                            errorText={refErrors[field.key]}
                                        >
                                            <Input
                                                value={rule.pipelineRef[field.key] || ""}
                                                onChange={({ detail }) =>
                                                    updatePipelineRef(idx, field.key, detail.value)
                                                }
                                                placeholder={field.placeholder}
                                                disabled={readOnly}
                                            />
                                        </FormField>
                                    ))}
                                </ColumnLayout>
                            )}
                            {rule.ruleType === "pipeline" && renderInputFiles(idx, rule.inputFiles)}
                            {rule.ruleType !== "pipeline" && (
                                <Box color="text-body-secondary" fontSize="body-s">
                                    The checks of a {rule.ruleType} rule are edited in the JSON
                                    Editor tab.
                                </Box>
                            )}
                            {!readOnly && (
                                <Box float="right">
                                    <Button variant="link" onClick={() => removeRule(idx)}>
                                        Remove
                                    </Button>
                                </Box>
                            )}
                        </SpaceBetween>
                    </div>
                );
            })}
        </SpaceBetween>
    );

    const renderPropertiesBuilder = () => (
        <SpaceBetween size="m">
            <Header
                variant="h3"
                actions={
                    <Button onClick={addProperty} disabled={readOnly}>
                        Add property
                    </Button>
                }
            >
                Schema Properties
            </Header>

            {properties.length === 0 && (
                <Box textAlign="center" color="text-status-inactive" padding="l">
                    No properties defined. Click "Add property" to start.
                </Box>
            )}

            {properties.map((prop, idx) => (
                <div key={idx} style={cardStyle}>
                    <SpaceBetween size="s">
                        <ColumnLayout columns={3}>
                            <FormField label="Name">
                                <Input
                                    value={prop.name}
                                    onChange={({ detail }) =>
                                        updateProperty(idx, "name", detail.value)
                                    }
                                    placeholder="property_name"
                                    disabled={readOnly}
                                />
                            </FormField>
                            <FormField label="Type">
                                <Select
                                    selectedOption={
                                        TYPE_OPTIONS.find((o) => o.value === prop.type) ||
                                        TYPE_OPTIONS[0]
                                    }
                                    onChange={({ detail }) =>
                                        updateProperty(idx, "type", detail.selectedOption.value)
                                    }
                                    options={TYPE_OPTIONS}
                                    disabled={readOnly}
                                />
                            </FormField>
                            <FormField label="Required">
                                <Toggle
                                    checked={prop.required}
                                    onChange={({ detail }) =>
                                        updateProperty(idx, "required", detail.checked)
                                    }
                                    disabled={readOnly}
                                >
                                    Required
                                </Toggle>
                            </FormField>
                        </ColumnLayout>
                        <FormField label="Description">
                            <Input
                                value={prop.description}
                                onChange={({ detail }) =>
                                    updateProperty(idx, "description", detail.value)
                                }
                                placeholder="What this property represents"
                                disabled={readOnly}
                            />
                        </FormField>
                        <ColumnLayout columns={2}>
                            {prop.type === "string" && (
                                <FormField label="Allowed values (comma-separated)">
                                    <Input
                                        value={prop.enumValues}
                                        onChange={({ detail }) =>
                                            updateProperty(idx, "enumValues", detail.value)
                                        }
                                        placeholder="value1, value2, value3"
                                        disabled={readOnly}
                                    />
                                </FormField>
                            )}
                            {(prop.type === "number" || prop.type === "integer") && (
                                <>
                                    <FormField label="Minimum">
                                        <Input
                                            value={prop.minimum}
                                            onChange={({ detail }) =>
                                                updateProperty(idx, "minimum", detail.value)
                                            }
                                            type="number"
                                            disabled={readOnly}
                                        />
                                    </FormField>
                                    <FormField label="Maximum">
                                        <Input
                                            value={prop.maximum}
                                            onChange={({ detail }) =>
                                                updateProperty(idx, "maximum", detail.value)
                                            }
                                            type="number"
                                            disabled={readOnly}
                                        />
                                    </FormField>
                                </>
                            )}
                            {prop.type === "string" && (
                                <>
                                    <FormField label="Min length">
                                        <Input
                                            value={prop.minLength}
                                            onChange={({ detail }) =>
                                                updateProperty(idx, "minLength", detail.value)
                                            }
                                            type="number"
                                            disabled={readOnly}
                                        />
                                    </FormField>
                                    <FormField label="Max length">
                                        <Input
                                            value={prop.maxLength}
                                            onChange={({ detail }) =>
                                                updateProperty(idx, "maxLength", detail.value)
                                            }
                                            type="number"
                                            disabled={readOnly}
                                        />
                                    </FormField>
                                </>
                            )}
                        </ColumnLayout>
                        {!readOnly && (
                            <Box float="right">
                                <Button variant="link" onClick={() => removeProperty(idx)}>
                                    Remove
                                </Button>
                            </Box>
                        )}
                    </SpaceBetween>
                </div>
            ))}

            <FormField label="Additional properties">
                <Toggle
                    checked={additionalProperties}
                    onChange={({ detail }) => setAdditionalProperties(detail.checked)}
                    disabled={readOnly}
                >
                    Allow properties not defined above
                </Toggle>
            </FormField>
        </SpaceBetween>
    );

    return (
        <SpaceBetween size="xs">
            <Tabs
                activeTabId={activeTab}
                onChange={({ detail }) => setActiveTab(detail.activeTabId)}
                tabs={[
                    {
                        id: "json",
                        label: "JSON Editor",
                        content: (
                            <SpaceBetween size="xs">
                                <Box float="right">
                                    <SpaceBetween direction="horizontal" size="xs">
                                        {errorCount > 0 && (
                                            <Box color="text-status-error" fontSize="body-s">
                                                {errorCount} error{errorCount > 1 ? "s" : ""}
                                            </Box>
                                        )}
                                        {warningCount > 0 && (
                                            <Box color="text-status-warning" fontSize="body-s">
                                                {warningCount} warning{warningCount > 1 ? "s" : ""}
                                            </Box>
                                        )}
                                        <Button
                                            iconName="edit"
                                            variant="icon"
                                            onClick={formatDocument}
                                            ariaLabel="Format JSON"
                                        />
                                    </SpaceBetween>
                                </Box>
                                <div
                                    style={{
                                        border: "1px solid var(--color-border-input-default, #aab7b8)",
                                        borderRadius: "8px",
                                        overflow: "hidden",
                                    }}
                                >
                                    <Editor
                                        height="400px"
                                        language="json"
                                        theme="vs-dark"
                                        value={value}
                                        onChange={handleEditorChange}
                                        onMount={handleEditorMount}
                                        onValidate={handleValidation}
                                        options={{
                                            readOnly,
                                            minimap: { enabled: false },
                                            scrollBeyondLastLine: false,
                                            fontSize: 13,
                                            tabSize: 2,
                                            wordWrap: "on",
                                            automaticLayout: true,
                                            formatOnPaste: true,
                                            formatOnType: true,
                                            suggest: {
                                                showKeywords: true,
                                                showSnippets: true,
                                            },
                                            quickSuggestions: true,
                                        }}
                                    />
                                </div>
                                {markers.length > 0 && (
                                    <Alert type={errorCount > 0 ? "error" : "warning"}>
                                        <SpaceBetween size="xxs">
                                            {markers.slice(0, 5).map((m, i) => (
                                                <Box key={i} fontSize="body-s">
                                                    Line {m.startLineNumber}: {m.message}
                                                </Box>
                                            ))}
                                            {markers.length > 5 && (
                                                <Box fontSize="body-s">
                                                    ...and {markers.length - 5} more issues
                                                </Box>
                                            )}
                                        </SpaceBetween>
                                    </Alert>
                                )}
                            </SpaceBetween>
                        ),
                    },
                    {
                        id: "wizard",
                        label: "Visual Builder",
                        content:
                            builderMode === "rules"
                                ? renderRulesBuilder()
                                : renderPropertiesBuilder(),
                    },
                ]}
            />
        </SpaceBetween>
    );
}
