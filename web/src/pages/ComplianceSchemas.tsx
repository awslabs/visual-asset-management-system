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
    RadioGroup,
    SelectProps,
} from "@cloudscape-design/components";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { usePageTitle } from "../hooks/usePageTitle";
import Synonyms from "../synonyms";
import {
    fetchComplianceSchemaListing,
    itemAllows,
    createComplianceSchema,
    updateComplianceSchema,
    sweepSchema,
    complianceSchemaFormat,
    ComplianceSchema,
} from "../services/ComplianceService";
import ComplianceSchemaEditor from "../components/compliance/ComplianceSchemaEditor";
import ScopeBadge from "../components/common/ScopeBadge";
import { fetchAllDatabases } from "../services/APIService";
import { ROW_ACTIONS_STICKY_COLUMNS, VAMS_PAGE_PADDING } from "../components/compliance/rowActions";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";
import { EXAMPLE_PIPELINE_RULE } from "../components/compliance/complianceSchemaRules";

// The routes behind the page's write actions (backend/backend/common/apiRoutes.py). Each action is
// offered only to a caller who may call its route; while the routes load, none is offered.
export const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
export const COMPLIANCE_SCHEMA_API_ROUTE = "/compliance/schemas/{schemaName}";
export const COMPLIANCE_SWEEP_API_ROUTE = "/compliance/sweep/{schemaName}";
// The database list behind the "one database" choice of a schema's availability.
export const DATABASE_LIST_API_ROUTE = "/database";

/** A schema's scope: the database it is available to, GLOBAL when it records none. */
export const GLOBAL_SCOPE = "GLOBAL";
export const schemaScope = (schema: ComplianceSchema): string => schema.databaseId || GLOBAL_SCOPE;
const ALL_SCOPES_OPTION: SelectProps.Option = { value: "*", label: "Any availability" };

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
 * the caller may not evaluate, and archived assets) and how many it did not reach (run it again for
 * those).
 */
export const sweepMessage = (schemaName: string, result: any): string => {
    const evaluated = Array.isArray(result?.assetsTriggered) ? result.assetsTriggered.length : 0;
    const parts = [
        `Sweep of schema "${schemaName}": ${evaluated} ${
            evaluated === 1 ? Synonyms.asset : Synonyms.assets
        } evaluated`,
    ];
    // `skipped` totals both reasons; an older service reports authorization only, as `skipped`.
    const notAuthorized =
        result?.skippedNotAuthorized ??
        (result?.skippedArchived === undefined ? result?.skipped : 0);
    if (notAuthorized) {
        parts.push(`${notAuthorized} skipped (you may not evaluate or read them)`);
    }
    if (result?.skippedArchived) {
        parts.push(`${result.skippedArchived} archived, not evaluated`);
    }
    if (result?.assetsRemaining) {
        parts.push(`${result.assetsRemaining} not reached yet; run the sweep again`);
    }
    return parts.join("; ") + ".";
};

export default function ComplianceSchemas() {
    const { can: canCallRoute } = useAllowedRoutes();
    const canCreateRoute = canCallRoute("POST", COMPLIANCE_SCHEMAS_API_ROUTE);
    const canUpdate = canCallRoute("PUT", COMPLIANCE_SCHEMA_API_ROUTE);
    const canSweep = canCallRoute("POST", COMPLIANCE_SWEEP_API_ROUTE);
    const canListDatabases = canCallRoute("GET", DATABASE_LIST_API_ROUTE);
    usePageTitle("Compliance Schemas");

    const [schemas, setSchemas] = useState<ComplianceSchema[]>([]);
    // The scopes the caller may create a schema in, as the service computes them; undefined when
    // the response predates them (the route check alone then gates creation).
    const [createScopes, setCreateScopes] = useState<string[] | undefined>(undefined);
    const canCreate = canCreateRoute && (createScopes === undefined || createScopes.length > 0);
    const [loading, setLoading] = useState(false);
    const [filterText, setFilterText] = useState("");
    const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);
    // Sequence for flash message ids; two messages raised in the same millisecond stay distinct.
    const flashSequence = useRef(0);
    // The NextToken of each schema's last sweep while assets remained (see handleSweep).
    const sweepTokens = useRef<Record<string, string>>({});

    // Modal state
    const [modalVisible, setModalVisible] = useState(false);
    const [editingSchema, setEditingSchema] = useState<ComplianceSchema | null>(null);
    // A caller who may read a schema but not update it (the route, or this schema in its scope)
    // opens it read-only from its name.
    const viewOnly = !!editingSchema && !(canUpdate && itemAllows(editingSchema, "edit"));
    const [formName, setFormName] = useState("");
    const [formDescription, setFormDescription] = useState("");
    // Where the schema can be bound: GLOBAL (every database) or one database (only there).
    const [formDatabaseId, setFormDatabaseId] = useState<string>(GLOBAL_SCOPE);
    const [databaseOptions, setDatabaseOptions] = useState<SelectProps.Option[]>([]);
    const [scopeFilter, setScopeFilter] = useState<SelectProps.Option>(ALL_SCOPES_OPTION);
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
        const [success, result] = await fetchComplianceSchemaListing();
        if (success && typeof result !== "string") {
            setSchemas(result.schemas);
            setCreateScopes(result.createScopes);
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
        // GLOBAL when the caller may create there, otherwise the first database it may create in.
        setFormDatabaseId(
            createScopes === undefined || createScopes.includes(GLOBAL_SCOPE)
                ? GLOBAL_SCOPE
                : createScopes.find((scope) => scope !== GLOBAL_SCOPE) || GLOBAL_SCOPE
        );
        setSelectedTemplate(DEFAULT_TEMPLATE_OPTION);
        setFormBody(DEFAULT_TEMPLATE_BODY);
        setFormError(null);
        setModalVisible(true);
    };

    useEffect(() => {
        // The service names the databases the caller may create in; no listing is needed.
        if (!canListDatabases || createScopes !== undefined) return;
        fetchAllDatabases().then((databases) => {
            if (Array.isArray(databases)) {
                setDatabaseOptions(
                    databases
                        .map((d: any) => d.databaseId)
                        .filter((id: any) => typeof id === "string" && id !== GLOBAL_SCOPE)
                        .sort()
                        .map((id: string) => ({ value: id, label: id }))
                );
            }
        });
    }, [canListDatabases, createScopes]);

    // The scopes the form offers: those the caller may create (and move a schema) in, plus the
    // edited schema's own scope; without `createScopes`, GLOBAL and every listed database.
    const scopeAllowed = (scope: string) =>
        createScopes === undefined ||
        createScopes.includes(scope) ||
        (!!editingSchema && schemaScope(editingSchema) === scope);
    const formDatabaseOptions: SelectProps.Option[] =
        createScopes === undefined
            ? databaseOptions
            : Array.from(
                  new Set([
                      ...createScopes.filter((scope) => scope !== GLOBAL_SCOPE),
                      ...(editingSchema && schemaScope(editingSchema) !== GLOBAL_SCOPE
                          ? [schemaScope(editingSchema)]
                          : []),
                  ])
              )
                  .sort()
                  .map((id) => ({ value: id, label: id }));

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
        setFormDatabaseId(schema.databaseId || GLOBAL_SCOPE);
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
        if (!formDatabaseId) {
            setFormError(`Choose the ${Synonyms.database} the schema is available to`);
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
                databaseId: formDatabaseId,
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
                databaseId: formDatabaseId,
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
        // A sweep that left assets unreached is continued by the next Sweep of the same schema.
        const [success, result] = await sweepSchema(schemaName, sweepTokens.current[schemaName]);
        if (success) {
            if (result?.NextToken) {
                sweepTokens.current[schemaName] = result.NextToken;
            } else {
                delete sweepTokens.current[schemaName];
            }
        }
        if (success) {
            addFlashMessage("info", sweepMessage(schemaName, result));
        } else {
            addFlashMessage("error", typeof result === "string" ? result : "Sweep failed");
        }
    };

    const filteredSchemas = schemas.filter(
        (s) =>
            (s.schemaName.toLowerCase().includes(filterText.toLowerCase()) ||
                (s.description || "").toLowerCase().includes(filterText.toLowerCase())) &&
            (scopeFilter.value === ALL_SCOPES_OPTION.value || schemaScope(s) === scopeFilter.value)
    );
    // The filter offers GLOBAL and every database a listed schema is available to.
    const scopeFilterOptions: SelectProps.Option[] = [
        ALL_SCOPES_OPTION,
        { value: GLOBAL_SCOPE, label: "All databases (GLOBAL)" },
        ...Array.from(new Set(schemas.map(schemaScope).filter((d) => d !== GLOBAL_SCOPE)))
            .sort()
            .map((d) => ({ value: d, label: d })),
    ];

    return (
        <Box padding={VAMS_PAGE_PADDING}>
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
                                {filterText || scopeFilter.value !== ALL_SCOPES_OPTION.value
                                    ? "No compliance schemas match the filters."
                                    : "No compliance schemas registered. Create one to get started."}
                            </Box>
                        }
                        filter={
                            <SpaceBetween direction="horizontal" size="xs">
                                <TextFilter
                                    filteringText={filterText}
                                    filteringPlaceholder="Find schemas"
                                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                                />
                                <Select
                                    ariaLabel="Filter by availability"
                                    selectedOption={scopeFilter}
                                    options={scopeFilterOptions}
                                    onChange={({ detail }) => setScopeFilter(detail.selectedOption)}
                                />
                            </SpaceBetween>
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
                                id: "databaseId",
                                header: "Available to",
                                cell: (item) => <ScopeBadge databaseId={schemaScope(item)} />,
                                sortingField: "databaseId",
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
                                                      {canUpdate && itemAllows(item, "edit") && (
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
                        <FormField
                            label="Available to"
                            description={`Where the schema can be bound. A GLOBAL schema is offered in every ${Synonyms.database}; a schema for one ${Synonyms.database} is offered, and can be bound, only there. Its rules may name metadata schemas, workflows and pipelines of GLOBAL or of that ${Synonyms.database} only.`}
                        >
                            <SpaceBetween size="xs">
                                <RadioGroup
                                    ariaLabel="Available to"
                                    value={formDatabaseId === GLOBAL_SCOPE ? GLOBAL_SCOPE : "one"}
                                    onChange={({ detail }) =>
                                        setFormDatabaseId(
                                            detail.value === GLOBAL_SCOPE ? GLOBAL_SCOPE : ""
                                        )
                                    }
                                    items={[
                                        {
                                            value: GLOBAL_SCOPE,
                                            label: "All databases (GLOBAL)",
                                            disabled: viewOnly || !scopeAllowed(GLOBAL_SCOPE),
                                        },
                                        {
                                            value: "one",
                                            label: `One ${Synonyms.database}`,
                                            disabled: viewOnly || formDatabaseOptions.length === 0,
                                        },
                                    ]}
                                />
                                {formDatabaseId !== GLOBAL_SCOPE && (
                                    <Select
                                        ariaLabel={`${Synonyms.Database} the schema is available to`}
                                        selectedOption={
                                            formDatabaseId
                                                ? { value: formDatabaseId, label: formDatabaseId }
                                                : null
                                        }
                                        options={formDatabaseOptions}
                                        filteringType="auto"
                                        placeholder={`Choose a ${Synonyms.database}`}
                                        disabled={viewOnly}
                                        onChange={({ detail }) =>
                                            setFormDatabaseId(
                                                (detail.selectedOption.value as string) || ""
                                            )
                                        }
                                    />
                                )}
                            </SpaceBetween>
                        </FormField>
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
                                schemaDatabaseId={formDatabaseId || GLOBAL_SCOPE}
                            />
                        </FormField>
                    </SpaceBetween>
                </Modal>
            </SpaceBetween>
        </Box>
    );
}
