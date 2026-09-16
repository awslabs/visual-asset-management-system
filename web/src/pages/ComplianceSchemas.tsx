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
        label: "Blank rule set",
        description: "An empty vams-rules-v1 rule set to build from scratch",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {},
        },
    },
    pipelineRules: {
        label: "Pipeline and metadata rules",
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
                    metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "defaultAsset" },
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
        description: "Owner, classification and retention metadata plus a parent assembly",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "ownership-and-lifecycle": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "baseline-fields-present",
                            description:
                                "The default asset metadata schema's required fields are present and typed as declared, and the asset names its owner, classification level and retention period.",
                            validateRequired: true,
                            validateTypes: true,
                            additionalRequiredFields: ["owner", "classification", "retention_days"],
                        },
                    ],
                },
                "review-schedule": {
                    ruleType: "metadata",
                    enforcement: "inform",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "review-fields-present",
                            description:
                                "The owning department and the next scheduled review date are recorded.",
                            additionalRequiredFields: ["department", "review_date"],
                        },
                    ],
                },
                "assembly-membership": {
                    ruleType: "relationship",
                    enforcement: "warn",
                    checks: [
                        {
                            name: "has-parent-assembly",
                            description: "The asset belongs to at least one parent assembly.",
                            direction: "parents",
                            relationshipType: "parentChild",
                            minCount: 1,
                        },
                    ],
                },
            },
        },
    },
    classification: {
        label: "Data Classification",
        description: "Security classification with handling instructions and a data steward",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "classification-labels": {
                    ruleType: "metadata",
                    enforcement: "quarantine",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "classification-fields-present",
                            description:
                                "The asset carries a security classification level, handling instructions and an accountable data steward.",
                            validateRequired: true,
                            validateTypes: true,
                            additionalRequiredFields: [
                                "classification",
                                "handling_instructions",
                                "data_steward",
                            ],
                        },
                    ],
                },
                "dissemination-markings": {
                    ruleType: "metadata",
                    enforcement: "inform",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "dissemination-controls-recorded",
                            description:
                                "Dissemination control markings and the originating agency are recorded.",
                            additionalRequiredFields: [
                                "dissemination_controls",
                                "originating_agency",
                            ],
                        },
                    ],
                },
            },
        },
    },
    model3d: {
        label: "3D Model Quality",
        description: "Geometry metadata and a conversion through the built-in 3D pipeline",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "model-metadata": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "geometry-fields-present",
                            description:
                                "The default asset metadata schema's required fields are present and typed as declared, and the model documents its polygon count, coordinate reference system and unit of measurement.",
                            validateRequired: true,
                            validateTypes: true,
                            additionalRequiredFields: [
                                "polygon_count",
                                "coordinate_system",
                                "units",
                            ],
                        },
                    ],
                },
                "converts-to-glb": {
                    ruleType: "pipeline",
                    enforcement: "quarantine",
                    pipelineRef: {
                        databaseId: "GLOBAL",
                        workflowId: "conversion-3d-basic",
                        pipelineDatabaseId: "GLOBAL",
                        pipelineId: "conversion-3d-basic",
                        templateId: "convert-to-glb",
                    },
                    inputFiles: {
                        mode: "matching",
                        filter: ["*.stl", "*.obj", "*.ply", "*.gltf", "*.glb", "*.xyz"],
                    },
                    checks: [
                        {
                            name: "conversion-succeeds",
                            description:
                                "The model file converts to GLB. The pipeline writes no compliance-output document, so the check reads the execution's own success measurement.",
                            outputField: "execution_success",
                            tolerance: {
                                operator: "eq",
                                value: 1,
                            },
                        },
                        {
                            name: "conversion-duration",
                            description: "The conversion completes within ten minutes.",
                            outputField: "processing_duration_seconds",
                            tolerance: {
                                operator: "lte",
                                value: 600,
                            },
                        },
                    ],
                },
            },
        },
    },
    retention: {
        label: "Retention Policy",
        description: "Retention period, disposal method and data owner metadata",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "retention-terms": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "retention-fields-present",
                            description:
                                "The asset states its retention period, the disposal method that applies once it expires, and the owner accountable for retention compliance.",
                            validateRequired: true,
                            validateTypes: true,
                            additionalRequiredFields: [
                                "retention_days",
                                "disposal_method",
                                "data_owner",
                            ],
                        },
                    ],
                },
                "review-cadence": {
                    ruleType: "metadata",
                    enforcement: "inform",
                    metadataSchemaRef: {
                        databaseId: "GLOBAL",
                        schemaName: "defaultAsset",
                    },
                    checks: [
                        {
                            name: "review-dates-recorded",
                            description:
                                "The last access review and the next scheduled retention review are recorded.",
                            additionalRequiredFields: ["last_access_review", "next_review_date"],
                        },
                    ],
                },
            },
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
                            description="A vams-rules-v1 rule set: pipeline, metadata and relationship rules with an enforcement level each. Use the JSON editor for full control or the Visual Builder for guided editing."
                        >
                            <ComplianceSchemaEditor value={formBody} onChange={setFormBody} />
                        </FormField>
                    </SpaceBetween>
                </Modal>
            </SpaceBetween>
        </Box>
    );
}
