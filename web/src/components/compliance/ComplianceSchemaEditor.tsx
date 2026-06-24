/*
 * Copyright 2024 Balfour Beatty. All Rights Reserved.
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
import type { editor } from "monaco-editor";

const COMPLIANCE_META_SCHEMA = {
    uri: "http://vams/compliance-schema",
    fileMatch: ["*"],
    schema: {
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
    const [properties, setProperties] = useState<PropertyDef[]>([]);
    const [additionalProperties, setAdditionalProperties] = useState(true);

    const syncWizardFromJson = useCallback((json: string) => {
        try {
            const parsed = JSON.parse(json);
            const props: PropertyDef[] = [];
            const requiredList: string[] = parsed.required || [];

            if (parsed.properties) {
                for (const [name, def] of Object.entries(parsed.properties)) {
                    const d = def as any;
                    props.push({
                        name,
                        type: d.type || "string",
                        description: d.description || "",
                        required: requiredList.includes(name),
                        enumValues: d.enum ? d.enum.join(", ") : "",
                        minimum: d.minimum !== undefined ? String(d.minimum) : "",
                        maximum: d.maximum !== undefined ? String(d.maximum) : "",
                        minLength: d.minLength !== undefined ? String(d.minLength) : "",
                        maxLength: d.maxLength !== undefined ? String(d.maxLength) : "",
                    });
                }
            }
            setProperties(props);
            setAdditionalProperties(parsed.additionalProperties !== false);
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
                propDef.enum = p.enumValues.split(",").map((v) => v.trim()).filter(Boolean);
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
        if (activeTab === "wizard") {
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
        const json = buildJsonFromWizard();
        onChange(json);
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
        if (activeTab === "wizard" && properties.length > 0) {
            handleWizardChange();
        }
    }, [properties, additionalProperties]);

    const formatDocument = () => {
        if (editorRef.current) {
            editorRef.current.getAction("editor.action.formatDocument")?.run();
        }
    };

    const errorCount = markers.filter((m) => m.severity === 8).length;
    const warningCount = markers.filter((m) => m.severity === 4).length;

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
                                <div style={{ border: "1px solid var(--color-border-input-default, #aab7b8)", borderRadius: "8px", overflow: "hidden" }}>
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
                        content: (
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
                                    <div
                                        key={idx}
                                        style={{
                                            padding: "12px",
                                            border: "1px solid var(--color-border-divider-default, #eaeded)",
                                            borderRadius: "8px",
                                        }}
                                    >
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
                                                {(prop.type === "string") && (
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
                                                    <Button
                                                        variant="link"
                                                        onClick={() => removeProperty(idx)}
                                                    >
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
                        ),
                    },
                ]}
            />
        </SpaceBetween>
    );
}
