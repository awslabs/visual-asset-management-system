/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
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
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { usePageTitle } from "../hooks/usePageTitle";
import Synonyms from "../synonyms";
import {
    fetchComplianceSchemas,
    createComplianceSchema,
    updateComplianceSchema,
    sweepSchema,
    complianceSchemaFormat,
    ComplianceSchema,
} from "../services/ComplianceService";
import ComplianceSchemaEditor from "../components/compliance/ComplianceSchemaEditor";
import { ROW_ACTIONS_STICKY_COLUMNS } from "../components/compliance/rowActions";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";
import { EXAMPLE_PIPELINE_RULE } from "../components/compliance/complianceSchemaRules";

// The routes behind the page's write actions (backend/backend/common/apiRoutes.py). Each action is
// offered only to a caller who may call its route; while the routes load, none is offered.
export const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
export const COMPLIANCE_SCHEMA_API_ROUTE = "/compliance/schemas/{schemaName}";
export const COMPLIANCE_SWEEP_API_ROUTE = "/compliance/sweep/{schemaName}";

/**
 * Width (px) of the Actions column: the Edit and Sweep inline links side by side (about 85 px of
 * labels and gap) with the cell padding. A width alone is a preference the table shrinks on a narrow
 * window (the labels then wrapped one letter per line at 768 px); the same minimum keeps it. See
 * ROW_ACTIONS_STICKY_COLUMNS.
 */
export const SCHEMA_ACTIONS_COLUMN_WIDTH = 150;

/** Shown on a schema row whose body is not a vams-rules-v1 document. */
export const LEGACY_SCHEMA_LABEL = "Legacy — cannot be bound or evaluated";

export const SCHEMA_TEMPLATES: Record<
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
            "Runs a workflow and checks its output against tolerances; also validates the asset's metadata and each model file's metadata",
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
                    metadataSchemaRef: { schemaName: "defaultAsset" },
                    checks: [
                        {
                            name: "required-fields-present",
                            validateRequired: true,
                            validateTypes: true,
                        },
                    ],
                },
                "model-file-metadata": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    scope: "files",
                    metadataSchemaRef: { schemaName: "modelFileMetadata" },
                    includePaths: ["\\.(glb|gltf|obj|stl|fbx)$"],
                    excludePaths: ["\\.previewFile\\."],
                    checks: [
                        {
                            name: "file-fields-present",
                            description:
                                "Every current model file carries the required fields of the GLOBAL modelFileMetadata file metadata schema, typed as declared. Create that schema (entity type fileMetadata) first; until it exists the rule records an error.",
                            validateRequired: true,
                            validateTypes: true,
                        },
                    ],
                },
            },
        },
    },
    engineering: {
        label: `Engineering ${Synonyms.Asset} Standard`,
        description: "Owner, classification and retention metadata plus a placed parent assembly",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "ownership-and-lifecycle": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: {
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
                "assembly-placement": {
                    ruleType: "metadata",
                    enforcement: "inform",
                    scope: "links",
                    linkDirection: "parents",
                    relationshipType: "parentChild",
                    metadataSchemaRef: {
                        schemaName: "defaultAssetLinks",
                    },
                    checks: [
                        {
                            name: "placement-recorded",
                            description:
                                "Every parent-assembly link records where the asset sits in the assembly.",
                            additionalRequiredFields: ["Translation"],
                        },
                    ],
                },
                "asset-naming": {
                    ruleType: "assetAttributes",
                    enforcement: "inform",
                    checks: [
                        {
                            name: "name-convention",
                            description:
                                "The asset name starts with a letter or digit and uses only letters, digits, underscores, dots and hyphens.",
                            field: "assetName",
                            matches: "^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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
        description:
            "Geometry metadata and a conversion through the built-in 3D pipeline; the filter names source formats only, because the pipeline writes .glb",
        body: {
            schemaFormat: "vams-rules-v1",
            rules: {
                "model-metadata": {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: {
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
                "model-files": {
                    ruleType: "fileStructure",
                    enforcement: "warn",
                    checks: [
                        {
                            name: "model-file-present",
                            description:
                                "At least one source model file is present, and no temporary or operating-system files are.",
                            requiredPaths: ["\\.(stl|obj|ply|gltf|glb|xyz)$"],
                            forbiddenPaths: ["\\.(tmp|bak)$", "(^|/)(\\.DS_Store|Thumbs\\.db)$"],
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
                        filter: ["*.stl", "*.obj", "*.ply", "*.gltf", "*.xyz"],
                    },
                    checks: [
                        {
                            name: "conversion-succeeds",
                            description:
                                "The model file converts to GLB. The filter lists source formats only: the pipeline writes a .glb, so admitting *.glb would select the pipeline's own output on re-evaluation. The pipeline writes no compliance-output document, so the check reads the execution's own success measurement.",
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
                "files-captured-in-a-version": {
                    ruleType: "versionCoverage",
                    enforcement: "warn",
                    checks: [
                        {
                            name: "current-files-versioned",
                            description:
                                "Every current file is recorded in an asset version, so a retained version reproduces the asset. Preview images are not checked. The rule fails after an upload until an asset version is created.",
                            excludePaths: ["\\.previewFile\\."],
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

/**
 * The flash a sweep raises: how many assets it evaluated, how many it skipped (assets in databases
 * the caller may not evaluate) and how many it did not reach (run it again for those).
 */
export const sweepMessage = (schemaName: string, result: any): string => {
    const evaluated = Array.isArray(result?.assetsTriggered) ? result.assetsTriggered.length : 0;
    const parts = [
        `Sweep of schema "${schemaName}": ${evaluated} ${
            evaluated === 1 ? Synonyms.asset : Synonyms.assets
        } evaluated`,
    ];
    if (result?.skipped) {
        parts.push(`${result.skipped} skipped (in a ${Synonyms.database} you may not evaluate)`);
    }
    if (result?.assetsRemaining) {
        parts.push(`${result.assetsRemaining} not reached yet; run the sweep again`);
    }
    return parts.join("; ") + ".";
};

export default function ComplianceSchemas() {
    const { can: canCallRoute } = useAllowedRoutes();
    const canCreate = canCallRoute("POST", COMPLIANCE_SCHEMAS_API_ROUTE);
    const canUpdate = canCallRoute("PUT", COMPLIANCE_SCHEMA_API_ROUTE);
    const canSweep = canCallRoute("POST", COMPLIANCE_SWEEP_API_ROUTE);
    usePageTitle("Compliance Schemas");

    const [schemas, setSchemas] = useState<ComplianceSchema[]>([]);
    const [loading, setLoading] = useState(false);
    const [filterText, setFilterText] = useState("");
    const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);
    // Sequence for flash message ids; two messages raised in the same millisecond stay distinct.
    const flashSequence = useRef(0);

    // Modal state
    const [modalVisible, setModalVisible] = useState(false);
    const [editingSchema, setEditingSchema] = useState<ComplianceSchema | null>(null);
    // A caller who may read a schema but not update it opens it read-only from its name.
    const viewOnly = !!editingSchema && !canUpdate;
    const [formName, setFormName] = useState("");
    const [formDescription, setFormDescription] = useState("");
    const [formBody, setFormBody] = useState(DEFAULT_TEMPLATE_BODY);
    const [selectedTemplate, setSelectedTemplate] = useState(DEFAULT_TEMPLATE_OPTION);
    const [formError, setFormError] = useState<string | null>(null);
    // The rule-name problem the editor's Visual Builder shows; saving is refused while it is set.
    const [rulesProblem, setRulesProblem] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const formErrorRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        loadSchemas();
    }, []);

    // The error sits at the top of the modal. A save the API refuses (a pattern that does not compile
    // or exceeds the backtracking budget) is usually made while the user is scrolled down at the rule,
    // so bring the message into view.
    useEffect(() => {
        if (formError) {
            formErrorRef.current?.scrollIntoView?.({ block: "nearest" });
        }
    }, [formError]);

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
        flashSequence.current += 1;
        const id = `flash-${flashSequence.current}`;
        setFlashMessages((prev) => [
            ...prev,
            {
                type,
                content,
                dismissible: true,
                dismissLabel: "Dismiss message",
                id,
                onDismiss: () => setFlashMessages((msgs) => msgs.filter((m) => m.id !== id)),
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

        // The builder's rule-name error is shown on the rule; saving now would drop or merge it.
        if (rulesProblem) {
            setFormError(`Fix the rule names before saving: ${rulesProblem}.`);
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
            addFlashMessage("info", sweepMessage(schemaName, result));
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
                        canCreate ? (
                            <Button variant="primary" onClick={openCreateModal}>
                                Create Schema
                            </Button>
                        ) : undefined
                    }
                >
                    Compliance Schemas
                </Header>

                <Flashbar items={flashMessages} />

                <Container>
                    <Table
                        wrapLines
                        stickyColumns={ROW_ACTIONS_STICKY_COLUMNS}
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
                                id: "schemaFormat",
                                header: "Format",
                                cell: (item) => {
                                    const format = complianceSchemaFormat(item);
                                    if (format === "legacy") {
                                        return (
                                            <StatusIndicator type="warning">
                                                {LEGACY_SCHEMA_LABEL}
                                            </StatusIndicator>
                                        );
                                    }
                                    return format;
                                },
                                width: 180,
                            },
                            {
                                id: "version",
                                header: "Version",
                                cell: (item) => item.version || 1,
                                width: 80,
                            },
                            ...(canUpdate || canSweep
                                ? [
                                      {
                                          id: "actions",
                                          header: "Actions",
                                          width: SCHEMA_ACTIONS_COLUMN_WIDTH,
                                          minWidth: SCHEMA_ACTIONS_COLUMN_WIDTH,
                                          cell: (item: ComplianceSchema) => {
                                              // A legacy schema cannot be updated or evaluated.
                                              const legacy =
                                                  complianceSchemaFormat(item) === "legacy";
                                              return (
                                                  <SpaceBetween direction="horizontal" size="s">
                                                      {canUpdate && (
                                                          <Button
                                                              variant="inline-link"
                                                              disabled={legacy}
                                                              onClick={() => openEditModal(item)}
                                                          >
                                                              Edit
                                                          </Button>
                                                      )}
                                                      {canSweep && (
                                                          <Button
                                                              variant="inline-link"
                                                              disabled={legacy}
                                                              onClick={() =>
                                                                  handleSweep(item.schemaName)
                                                              }
                                                          >
                                                              Sweep
                                                          </Button>
                                                      )}
                                                  </SpaceBetween>
                                              );
                                          },
                                      },
                                  ]
                                : []),
                        ]}
                    />
                </Container>

                {/* Create/Edit Modal */}
                <Modal
                    visible={modalVisible}
                    onDismiss={() => setModalVisible(false)}
                    header={
                        editingSchema ? (viewOnly ? "View Schema" : "Edit Schema") : "Create Schema"
                    }
                    size="large"
                    footer={
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={() => setModalVisible(false)}>
                                    {viewOnly ? "Close" : "Cancel"}
                                </Button>
                                {!viewOnly && (
                                    <Button variant="primary" loading={saving} onClick={handleSave}>
                                        {editingSchema ? "Update" : "Create"}
                                    </Button>
                                )}
                            </SpaceBetween>
                        </Box>
                    }
                >
                    <SpaceBetween size="m">
                        {formError && (
                            <div ref={formErrorRef} role="alert">
                                <Box color="text-status-error">{formError}</Box>
                            </div>
                        )}
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
                                disabled={viewOnly}
                                placeholder="Brief description of what this schema enforces"
                            />
                        </FormField>
                        <FormField
                            label="Schema Body"
                            description="A vams-rules-v1 rule set: pipeline, metadata, relationship, file structure, version coverage and asset attributes rules with an enforcement level each. Use the JSON editor for full control or the Visual Builder for guided editing."
                        >
                            <ComplianceSchemaEditor
                                value={formBody}
                                onChange={setFormBody}
                                readOnly={viewOnly}
                                onRulesProblemChange={setRulesProblem}
                            />
                        </FormField>
                    </SpaceBetween>
                </Modal>
            </SpaceBetween>
        </Box>
    );
}
