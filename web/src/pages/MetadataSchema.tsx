/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
    AppLayout,
    ContentLayout,
    Header,
    SpaceBetween,
    Button,
    Container,
    Table,
    Box,
    TextFilter,
    Tabs,
    Badge,
    StatusIndicator,
    Flashbar,
    FlashbarProps,
} from "@cloudscape-design/components";
import DatabaseSelectorWithModal from "../components/selectors/DatabaseSelectorWithModal";
import { CreateEditSchemaModal } from "../components/metadataSchema/CreateEditSchemaModal";
import { DeleteSchemaModal } from "../components/metadataSchema/DeleteSchemaModal";
import {
    MetadataSchema,
    MetadataSchemaEntityType,
    ENTITY_TYPE_LABELS,
} from "../components/metadataSchema/types";
import {
    fetchMetadataSchemas,
    createMetadataSchema,
    updateMetadataSchema,
    deleteMetadataSchema,
} from "../services/metadataSchemaAPI";

// Export for backward compatibility with old metadata components
export interface SchemaContextData {
    schemas: any[];
    databaseId: string | null;
}

export default function MetadataSchemaPage() {
    const params = useParams();
    const navigate = useNavigate();

    // Get databaseId from params, but treat "create" as no database selected
    const databaseId =
        params.databaseId && params.databaseId !== "create" ? params.databaseId : undefined;

    // Debug: Log the params to understand what's being captured
    useEffect(() => {
        console.log("MetadataSchemaPage - params:", params);
        console.log("MetadataSchemaPage - databaseId:", databaseId);
    }, [params, databaseId]);

    // State
    const [databaseSelectModalOpen, setDatabaseSelectModalOpen] = useState(!databaseId);
    const [schemas, setSchemas] = useState<MetadataSchema[]>([]);
    const [filteredSchemas, setFilteredSchemas] = useState<MetadataSchema[]>([]);
    const [loading, setLoading] = useState(false);
    const [searchText, setSearchText] = useState("");
    const [selectedEntityType, setSelectedEntityType] = useState<MetadataSchemaEntityType | "all">(
        "all"
    );
    const [createEditModalVisible, setCreateEditModalVisible] = useState(false);
    const [editingSchema, setEditingSchema] = useState<MetadataSchema | null>(null);
    const [deleteModalVisible, setDeleteModalVisible] = useState(false);
    const [deletingSchema, setDeletingSchema] = useState<MetadataSchema | null>(null);
    const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);

    // Sync modal state with databaseId changes to handle same-page navigation
    useEffect(() => {
        setDatabaseSelectModalOpen(!databaseId);
    }, [databaseId]);

    // Fetch schemas when database changes
    useEffect(() => {
        if (databaseId) {
            loadSchemas();
        }
    }, [databaseId]);

    // Filter schemas when search or entity type changes
    useEffect(() => {
        filterSchemas();
    }, [schemas, searchText, selectedEntityType]);

    const loadSchemas = async () => {
        if (!databaseId) return;

        setLoading(true);
        try {
            const response = await fetchMetadataSchemas(databaseId);
            setSchemas(response.Items || []);
        } catch (error: any) {
            console.error("Error loading schemas:", error);
            addFlashMessage({
                type: "error",
                content: `Failed to load schemas: ${error.message || "Unknown error"}`,
                dismissible: true,
            });
        } finally {
            setLoading(false);
        }
    };

    const filterSchemas = () => {
        let filtered = [...schemas];

        // Filter by search text
        if (searchText) {
            const searchLower = searchText.toLowerCase();
            filtered = filtered.filter((schema) =>
                schema.schemaName.toLowerCase().includes(searchLower)
            );
        }

        // Filter by entity type
        if (selectedEntityType !== "all") {
            filtered = filtered.filter(
                (schema) => schema.metadataSchemaEntityType === selectedEntityType
            );
        }

        setFilteredSchemas(filtered);
    };

    const addFlashMessage = (message: FlashbarProps.MessageDefinition) => {
        const messageId = Date.now().toString();
        setFlashMessages((prev) => [
            ...prev,
            {
                ...message,
                id: messageId,
                onDismiss: () => {
                    setFlashMessages((current) => current.filter((msg) => msg.id !== messageId));
                },
            },
        ]);
    };

    const handleCreateSchema = async (schemaData: any) => {
        try {
            await createMetadataSchema(schemaData);
            addFlashMessage({
                type: "success",
                content: `Schema "${schemaData.schemaName}" created successfully`,
                dismissible: true,
            });
            await loadSchemas();
        } catch (error: any) {
            throw new Error(error.message || "Failed to create schema");
        }
    };

    const handleUpdateSchema = async (schemaData: any) => {
        try {
            await updateMetadataSchema(schemaData);
            addFlashMessage({
                type: "success",
                content: `Schema "${schemaData.schemaName}" updated successfully`,
                dismissible: true,
            });
            await loadSchemas();
        } catch (error: any) {
            throw new Error(error.message || "Failed to update schema");
        }
    };

    const handleDeleteSchema = async () => {
        if (!deletingSchema || !databaseId) return;

        try {
            await deleteMetadataSchema(databaseId, deletingSchema.metadataSchemaId, {
                confirmDelete: true,
            });
            addFlashMessage({
                type: "success",
                content: `Schema "${deletingSchema.schemaName}" deleted successfully`,
                dismissible: true,
            });
            await loadSchemas();
        } catch (error: any) {
            throw new Error(error.message || "Failed to delete schema");
        }
    };

    const openCreateModal = () => {
        setEditingSchema(null);
        setCreateEditModalVisible(true);
    };

    const openEditModal = (schema: MetadataSchema) => {
        // Normalize fields format if needed
        const normalizedSchema = {
            ...schema,
            fields: schema.fields?.fields
                ? schema.fields
                : { fields: Array.isArray(schema.fields) ? schema.fields : [] },
        };
        setEditingSchema(normalizedSchema as MetadataSchema);
        setCreateEditModalVisible(true);
    };

    const openDeleteModal = (schema: MetadataSchema) => {
        setDeletingSchema(schema);
        setDeleteModalVisible(true);
    };

    const getEntityTypeTabs = () => {
        const tabs = [
            {
                id: "all",
                label: "All",
                content: null,
            },
        ];

        Object.entries(ENTITY_TYPE_LABELS).forEach(([value, label]) => {
            const count = schemas.filter((s) => s.metadataSchemaEntityType === value).length;
            tabs.push({
                id: value,
                label: `${label} (${count})`,
                content: null,
            });
        });

        return tabs;
    };

    // Show database selector if no database selected
    if (!databaseId) {
        return (
            <DatabaseSelectorWithModal
                open={databaseSelectModalOpen}
                setOpen={setDatabaseSelectModalOpen}
                showGlobal={true}
                onSelectorChange={(event: any) => {
                    const id = event?.detail?.selectedOption?.value;
                    if (id) {
                        setDatabaseSelectModalOpen(false);
                        navigate(`/metadataschema/${id}`);
                    }
                }}
            />
        );
    }

    return (
        <>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <SpaceBetween size="l">
                    {/* Page Header */}
                    <Header
                        variant="h1"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button
                                    onClick={() => {
                                        setDatabaseSelectModalOpen(true);
                                    }}
                                >
                                    Change Database
                                </Button>
                                <Button variant="primary" onClick={openCreateModal}>
                                    Create Schema
                                </Button>
                            </SpaceBetween>
                        }
                        description={databaseId ? `Database: ${databaseId}` : undefined}
                    >
                        Metadata Schemas
                    </Header>

                    {/* Flash Messages */}
                    <Flashbar items={flashMessages} />

                    {/* Main Content */}
                    <Container>
                        <SpaceBetween size="m">
                            <Tabs
                                activeTabId={selectedEntityType}
                                onChange={({ detail }) =>
                                    setSelectedEntityType(
                                        detail.activeTabId as MetadataSchemaEntityType | "all"
                                    )
                                }
                                tabs={getEntityTypeTabs()}
                            />

                            <Table
                                loading={loading}
                                loadingText="Loading schemas..."
                                columnDefinitions={[
                                    {
                                        id: "schemaName",
                                        header: "Schema Name",
                                        cell: (item) => item.schemaName,
                                        sortingField: "schemaName",
                                    },
                                    {
                                        id: "entityType",
                                        header: "Entity Type",
                                        cell: (item) => (
                                            <Badge>
                                                {ENTITY_TYPE_LABELS[item.metadataSchemaEntityType]}
                                            </Badge>
                                        ),
                                        sortingField: "metadataSchemaEntityType",
                                    },
                                    {
                                        id: "fieldCount",
                                        header: "Fields",
                                        cell: (item) => {
                                            // Handle both response formats
                                            if (item.fields?.fields) {
                                                return item.fields.fields.length;
                                            } else if (Array.isArray(item.fields)) {
                                                return item.fields.length;
                                            }
                                            return 0;
                                        },
                                    },
                                    {
                                        id: "fileTypeRestriction",
                                        header: "File Type Restriction",
                                        cell: (item) =>
                                            item.fileKeyTypeRestriction || (
                                                <Box color="text-body-secondary">None</Box>
                                            ),
                                    },
                                    {
                                        id: "enabled",
                                        header: "Status",
                                        cell: (item) =>
                                            item.enabled ? (
                                                <StatusIndicator type="success">
                                                    Enabled
                                                </StatusIndicator>
                                            ) : (
                                                <StatusIndicator type="stopped">
                                                    Disabled
                                                </StatusIndicator>
                                            ),
                                    },
                                    {
                                        id: "actions",
                                        header: "Actions",
                                        cell: (item) => (
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Button
                                                    variant="inline-link"
                                                    onClick={() => openEditModal(item)}
                                                >
                                                    Edit
                                                </Button>
                                                <Button
                                                    variant="inline-link"
                                                    onClick={() => openDeleteModal(item)}
                                                >
                                                    Delete
                                                </Button>
                                            </SpaceBetween>
                                        ),
                                    },
                                ]}
                                items={filteredSchemas}
                                empty={
                                    <Box textAlign="center" color="inherit">
                                        <SpaceBetween size="m">
                                            <b>No schemas</b>
                                            <Button onClick={openCreateModal}>Create Schema</Button>
                                        </SpaceBetween>
                                    </Box>
                                }
                                filter={
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <TextFilter
                                            filteringText={searchText}
                                            filteringPlaceholder="Search schemas by name"
                                            onChange={({ detail }) =>
                                                setSearchText(detail.filteringText)
                                            }
                                        />
                                        <Button
                                            iconName="refresh"
                                            onClick={loadSchemas}
                                            loading={loading}
                                            ariaLabel="Refresh schemas"
                                        />
                                    </SpaceBetween>
                                }
                                header={
                                    <Header
                                        counter={
                                            filteredSchemas.length !== schemas.length
                                                ? `(${filteredSchemas.length}/${schemas.length})`
                                                : `(${schemas.length})`
                                        }
                                    >
                                        Schemas
                                    </Header>
                                }
                            />
                        </SpaceBetween>
                    </Container>
                </SpaceBetween>
            </Box>

            {/* Modals */}
            <DatabaseSelectorWithModal
                open={databaseSelectModalOpen}
                setOpen={setDatabaseSelectModalOpen}
                showGlobal={true}
                onSelectorChange={(event: any) => {
                    const id = event?.detail?.selectedOption?.value;
                    if (id) {
                        setDatabaseSelectModalOpen(false);
                        navigate(`/metadataschema/${id}`);
                    }
                }}
            />

            <CreateEditSchemaModal
                visible={createEditModalVisible}
                onDismiss={() => {
                    setCreateEditModalVisible(false);
                    setEditingSchema(null);
                }}
                onSubmit={editingSchema ? handleUpdateSchema : handleCreateSchema}
                editingSchema={editingSchema}
                databaseId={databaseId}
            />

            <DeleteSchemaModal
                visible={deleteModalVisible}
                onDismiss={() => {
                    setDeleteModalVisible(false);
                    setDeletingSchema(null);
                }}
                onConfirm={handleDeleteSchema}
                schema={deletingSchema}
            />
        </>
    );
}
