/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    Table,
    TextFilter,
    Modal,
    FormField,
    Input,
    Select,
    Flashbar,
    FlashbarProps,
} from "@cloudscape-design/components";
import { usePageTitle } from "../hooks/usePageTitle";
import {
    fetchComplianceSchemas,
    createComplianceSchema,
    updateComplianceSchema,
    sweepSchema,
    ComplianceSchema,
} from "../services/ComplianceService";
import ComplianceSchemaEditor from "../components/compliance/ComplianceSchemaEditor";
import { EXAMPLE_PIPELINE_RULE } from "../components/compliance/complianceSchemaRules";

const SCHEMA_TEMPLATES: Record<
    string,
    { label: string; description: string; body: Record<string, any> }
> = {
    blank: {
        label: "Blank schema",
        description: "Start from scratch with an empty object schema",
        body: {
            type: "object",
            required: [],
            properties: {},
            additionalProperties: true,
        },
    },
    pipelineRules: {
        label: "Pipeline and metadata rules (vams-rules-v1)",
        description:
            "Runs a workflow and checks its output against tolerances; also validates metadata",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "output-within-tolerance": {
                    ...EXAMPLE_PIPELINE_RULE,
                    pipelineRef: {
                        databaseId: "GLOBAL",
                        workflowId: "quality-check-workflow",
                        pipelineDatabaseId: "GLOBAL",
                        pipelineId: "quality-check-pipeline",
                    },
                    checks: [
                        {
                            name: "polygon-budget",
                            description:
                                "Polygon count reported by the pipeline stays under budget",
                            outputField: "polygonCount",
                            tolerance: { operator: "lte", value: 500000 },
                        },
                    ],
                },
                "required-metadata": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "asset-metadata" },
                    checks: [
                        {
                            name: "required-fields-present",
                            validateRequired: true,
                            validateTypes: true,
                        },
                    ],
                },
            },
        },
    },
    engineering: {
        label: "Engineering Asset Standard",
        description: "Requires name, owner, classification, and retention",
        body: {
            type: "object",
            required: ["name", "owner", "classification", "retention_days"],
            properties: {
                name: {
                    type: "string",
                    minLength: 1,
                    maxLength: 256,
                    description: "Asset name or identifier",
                },
                owner: { type: "string", description: "Owner email address" },
                classification: {
                    type: "string",
                    enum: ["public", "internal", "confidential", "restricted"],
                    description: "Data classification level",
                },
                retention_days: {
                    type: "integer",
                    minimum: 1,
                    maximum: 3650,
                    description: "Retention period in days",
                },
                department: { type: "string", description: "Owning department or team" },
                version: { type: "string", description: "Asset version string" },
            },
            additionalProperties: true,
        },
    },
    classification: {
        label: "Data Classification",
        description: "Security classification with handling instructions",
        body: {
            type: "object",
            required: ["classification", "handling_instructions", "data_steward"],
            properties: {
                classification: {
                    type: "string",
                    enum: ["unclassified", "cui", "confidential", "secret", "top_secret"],
                    description: "Security classification level",
                },
                handling_instructions: {
                    type: "string",
                    minLength: 10,
                    maxLength: 2000,
                    description: "Handling instructions",
                },
                data_steward: { type: "string", description: "Data steward email" },
                dissemination_controls: {
                    type: "array",
                    items: { type: "string", enum: ["noforn", "relto", "orcon", "propin", "fouo"] },
                    description: "Dissemination control markings",
                },
            },
            additionalProperties: false,
        },
    },
    model3d: {
        label: "3D Model Quality",
        description: "Polygon budgets, coordinate system, and units",
        body: {
            type: "object",
            required: ["polygon_count", "coordinate_system", "units"],
            properties: {
                polygon_count: {
                    type: "integer",
                    minimum: 1,
                    maximum: 50000000,
                    description: "Total polygon count",
                },
                coordinate_system: {
                    type: "string",
                    enum: ["wgs84", "utm", "local", "enu", "ecef"],
                    description: "Coordinate reference system",
                },
                units: {
                    type: "string",
                    enum: ["meters", "centimeters", "millimeters", "feet", "inches"],
                    description: "Measurement units",
                },
                lod_levels: {
                    type: "integer",
                    minimum: 1,
                    maximum: 10,
                    description: "Number of LOD variants",
                },
                texture_resolution_max: {
                    type: "integer",
                    minimum: 64,
                    maximum: 16384,
                    description: "Max texture resolution in px",
                },
                has_collision_mesh: {
                    type: "boolean",
                    description: "Whether collision mesh is included",
                },
            },
            additionalProperties: true,
        },
    },
    retention: {
        label: "Retention Policy",
        description: "Lifecycle management with disposal method and legal hold",
        body: {
            type: "object",
            required: ["retention_days", "disposal_method", "data_owner"],
            properties: {
                retention_days: {
                    type: "integer",
                    minimum: 30,
                    maximum: 36500,
                    description: "Minimum retention in days",
                },
                disposal_method: {
                    type: "string",
                    enum: ["delete", "archive", "anonymize", "transfer"],
                    description: "Disposal method after retention expires",
                },
                data_owner: { type: "string", description: "Accountable person or team email" },
                legal_hold: {
                    type: "boolean",
                    description: "Under legal hold (prevents disposal)",
                },
                regulation: {
                    type: "string",
                    description: "Applicable regulation (e.g., GDPR, HIPAA)",
                },
                archive_tier: {
                    type: "string",
                    enum: ["hot", "warm", "cold", "glacier"],
                    description: "Storage tier",
                },
            },
            additionalProperties: true,
        },
    },
};

const TEMPLATE_OPTIONS = Object.entries(SCHEMA_TEMPLATES).map(
    ([value, { label, description }]) => ({
        value,
        label,
        description,
    })
);

const DEFAULT_TEMPLATE_KEY = "engineering";
const DEFAULT_TEMPLATE_OPTION =
    TEMPLATE_OPTIONS.find((o) => o.value === DEFAULT_TEMPLATE_KEY) || TEMPLATE_OPTIONS[0];
const DEFAULT_TEMPLATE_BODY = JSON.stringify(SCHEMA_TEMPLATES[DEFAULT_TEMPLATE_KEY].body, null, 2);

export default function ComplianceSchemas() {
    usePageTitle("Compliance Schemas");

    const [schemas, setSchemas] = useState<ComplianceSchema[]>([]);
    const [loading, setLoading] = useState(false);
    const [filterText, setFilterText] = useState("");
    const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);

    // Modal state
    const [modalVisible, setModalVisible] = useState(false);
    const [editingSchema, setEditingSchema] = useState<ComplianceSchema | null>(null);
    const [formName, setFormName] = useState("");
    const [formDescription, setFormDescription] = useState("");
    const [formBody, setFormBody] = useState(DEFAULT_TEMPLATE_BODY);
    const [selectedTemplate, setSelectedTemplate] = useState(DEFAULT_TEMPLATE_OPTION);
    const [formError, setFormError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        loadSchemas();
    }, []);

    const loadSchemas = async () => {
        setLoading(true);
        const [success, result] = await fetchComplianceSchemas();
        if (success && Array.isArray(result)) {
            setSchemas(result);
        } else if (typeof result === "string") {
            addFlashMessage("error", result);
        }
        setLoading(false);
    };

    const addFlashMessage = (type: "success" | "error" | "info", content: string) => {
        setFlashMessages((prev) => [
            ...prev,
            {
                type,
                content,
                dismissible: true,
                id: Date.now().toString(),
                onDismiss: () =>
                    setFlashMessages((msgs) => msgs.filter((m) => m.id !== Date.now().toString())),
            },
        ]);
    };

    const openCreateModal = () => {
        setEditingSchema(null);
        setFormName("");
        setFormDescription("");
        setSelectedTemplate(DEFAULT_TEMPLATE_OPTION);
        setFormBody(DEFAULT_TEMPLATE_BODY);
        setFormError(null);
        setModalVisible(true);
    };

    const handleTemplateChange = (option: any) => {
        setSelectedTemplate(option);
        const template = SCHEMA_TEMPLATES[option.value as string];
        if (template) {
            setFormBody(JSON.stringify(template.body, null, 2));
        }
    };

    const openEditModal = (schema: ComplianceSchema) => {
        setEditingSchema(schema);
        setFormName(schema.schemaName);
        setFormDescription(schema.description || "");
        setFormBody(JSON.stringify(schema.schemaBody, null, 2));
        setFormError(null);
        setModalVisible(true);
    };

    const handleSave = async () => {
        setFormError(null);

        if (!formName.trim()) {
            setFormError("Schema name is required");
            return;
        }

        let parsedBody: Record<string, any>;
        try {
            parsedBody = JSON.parse(formBody);
        } catch {
            setFormError("Invalid JSON in schema body");
            return;
        }

        setSaving(true);

        if (editingSchema) {
            const [success, result] = await updateComplianceSchema(editingSchema.schemaName, {
                description: formDescription,
                schemaBody: parsedBody,
            });
            if (success) {
                addFlashMessage("success", `Schema "${editingSchema.schemaName}" updated`);
                setModalVisible(false);
                await loadSchemas();
            } else {
                setFormError(typeof result === "string" ? result : "Failed to update schema");
            }
        } else {
            const [success, result] = await createComplianceSchema({
                schemaName: formName.trim(),
                description: formDescription,
                schemaBody: parsedBody,
            });
            if (success) {
                addFlashMessage("success", `Schema "${formName}" created`);
                setModalVisible(false);
                await loadSchemas();
            } else {
                setFormError(typeof result === "string" ? result : "Failed to create schema");
            }
        }

        setSaving(false);
    };

    const handleSweep = async (schemaName: string) => {
        const [success, result] = await sweepSchema(schemaName);
        if (success) {
            addFlashMessage("info", `Sweep triggered for schema "${schemaName}"`);
        } else {
            addFlashMessage("error", typeof result === "string" ? result : "Sweep failed");
        }
    };

    const filteredSchemas = schemas.filter(
        (s) =>
            s.schemaName.toLowerCase().includes(filterText.toLowerCase()) ||
            (s.description || "").toLowerCase().includes(filterText.toLowerCase())
    );

    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <SpaceBetween size="l">
                <Header
                    variant="h1"
                    actions={
                        <Button variant="primary" onClick={openCreateModal}>
                            Create Schema
                        </Button>
                    }
                >
                    Compliance Schemas
                </Header>

                <Flashbar items={flashMessages} />

                <Container>
                    <Table
                        loading={loading}
                        items={filteredSchemas}
                        empty={
                            <Box textAlign="center" padding="l">
                                No compliance schemas registered. Create one to get started.
                            </Box>
                        }
                        filter={
                            <TextFilter
                                filteringText={filterText}
                                filteringPlaceholder="Find schemas"
                                onChange={({ detail }) => setFilterText(detail.filteringText)}
                            />
                        }
                        columnDefinitions={[
                            {
                                id: "schemaName",
                                header: "Schema Name",
                                cell: (item) => (
                                    <Button variant="link" onClick={() => openEditModal(item)}>
                                        {item.schemaName}
                                    </Button>
                                ),
                                sortingField: "schemaName",
                                width: 200,
                            },
                            {
                                id: "description",
                                header: "Description",
                                cell: (item) => item.description || "-",
                                maxWidth: 300,
                            },
                            {
                                id: "version",
                                header: "Version",
                                cell: (item) => item.version || 1,
                                width: 80,
                            },
                            {
                                id: "actions",
                                header: "Actions",
                                width: 150,
                                cell: (item) => (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button variant="link" onClick={() => openEditModal(item)}>
                                            Edit
                                        </Button>
                                        <Button
                                            variant="link"
                                            onClick={() => handleSweep(item.schemaName)}
                                        >
                                            Sweep
                                        </Button>
                                    </SpaceBetween>
                                ),
                            },
                        ]}
                    />
                </Container>

                {/* Create/Edit Modal */}
                <Modal
                    visible={modalVisible}
                    onDismiss={() => setModalVisible(false)}
                    header={editingSchema ? "Edit Schema" : "Create Schema"}
                    size="large"
                    footer={
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={() => setModalVisible(false)}>Cancel</Button>
                                <Button variant="primary" loading={saving} onClick={handleSave}>
                                    {editingSchema ? "Update" : "Create"}
                                </Button>
                            </SpaceBetween>
                        </Box>
                    }
                >
                    <SpaceBetween size="m">
                        {formError && <Box color="text-status-error">{formError}</Box>}
                        <FormField label="Schema Name">
                            <Input
                                value={formName}
                                onChange={({ detail }) => setFormName(detail.value)}
                                disabled={!!editingSchema}
                                placeholder="e.g., engineering-asset-standard"
                            />
                        </FormField>
                        {!editingSchema && (
                            <FormField
                                label="Start from template"
                                description="Choose a template to pre-fill the schema body. You can customize it after."
                            >
                                <Select
                                    selectedOption={selectedTemplate}
                                    onChange={({ detail }) =>
                                        handleTemplateChange(detail.selectedOption)
                                    }
                                    options={TEMPLATE_OPTIONS}
                                />
                            </FormField>
                        )}
                        <FormField label="Description">
                            <Input
                                value={formDescription}
                                onChange={({ detail }) => setFormDescription(detail.value)}
                                placeholder="Brief description of what this schema enforces"
                            />
                        </FormField>
                        <FormField
                            label="Schema Body"
                            description="Either a vams-rules-v1 rule set (pipeline, metadata and relationship rules that are evaluated) or a JSON Schema that asset metadata must conform to. Use the JSON editor for full control or the Visual Builder for guided editing."
                        >
                            <ComplianceSchemaEditor value={formBody} onChange={setFormBody} />
                        </FormField>
                    </SpaceBetween>
                </Modal>
            </SpaceBetween>
        </Box>
    );
}
