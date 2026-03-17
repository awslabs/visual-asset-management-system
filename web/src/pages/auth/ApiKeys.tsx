/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextContent from "@cloudscape-design/components/text-content";
import TextFilter from "@cloudscape-design/components/text-filter";
import Grid from "@cloudscape-design/components/grid";
import Modal from "@cloudscape-design/components/modal";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { fetchApiKeys, deleteApiKey } from "../../services/APIService";
import CreateApiKey from "./CreateApiKey";
import UpdateApiKey from "./UpdateApiKey";

export default function ApiKeys() {
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);
    const [createOpen, setCreateOpen] = useState(false);
    const [editOpen, setEditOpen] = useState(false);
    const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
    const [deleteInProgress, setDeleteInProgress] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [filterText, setFilterText] = useState("");

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        setSelectedItems([]);
        try {
            const result = await fetchApiKeys();
            if (result === false || (Array.isArray(result) && result[0] === false)) {
                const errorMsg = Array.isArray(result) ? result[1] : "Failed to fetch API keys";
                setError(errorMsg);
                setAllItems([]);
            } else {
                const items = Array.isArray(result) ? result : result?.Items || [];
                setAllItems(items);
            }
        } catch (err: any) {
            console.log(err);
            setError(err?.message || "Unknown error");
            setAllItems([]);
        } finally {
            setLoading(false);
            setReload(false);
        }
    }, []);

    useEffect(() => {
        if (reload) {
            loadData();
        }
    }, [reload, loadData]);

    const handleDelete = async () => {
        if (selectedItems.length !== 1) return;
        setDeleteInProgress(true);
        try {
            const result = await deleteApiKey({ apiKeyId: selectedItems[0].apiKeyId });
            if (result && result[0] === true) {
                setDeleteConfirmOpen(false);
                setSelectedItems([]);
                setReload(true);
            } else {
                const errorMsg = result && result[1] ? result[1] : "Failed to delete API key";
                setError(errorMsg);
                setDeleteConfirmOpen(false);
            }
        } catch (err: any) {
            console.log(err);
            setError(err?.message || "Failed to delete API key");
            setDeleteConfirmOpen(false);
        } finally {
            setDeleteInProgress(false);
        }
    };

    const filteredItems = filterText
        ? allItems.filter((item: any) => {
              const search = filterText.toLowerCase();
              return (
                  (item.apiKeyName && item.apiKeyName.toLowerCase().includes(search)) ||
                  (item.userId && item.userId.toLowerCase().includes(search)) ||
                  (item.description && item.description.toLowerCase().includes(search))
              );
          })
        : allItems;

    const columnDefinitions = [
        {
            id: "name",
            header: "Name",
            cell: (item: any) => item.apiKeyName || "-",
            sortingField: "apiKeyName",
        },
        {
            id: "apiKeyId",
            header: "Key ID",
            cell: (item: any) => item.apiKeyId || "-",
            sortingField: "apiKeyId",
        },
        {
            id: "description",
            header: "Description",
            cell: (item: any) => item.description || "-",
            sortingField: "description",
        },
        {
            id: "userId",
            header: "User ID",
            cell: (item: any) => item.userId || "-",
            sortingField: "userId",
        },
        {
            id: "createdBy",
            header: "Created By",
            cell: (item: any) => item.createdBy || "-",
            sortingField: "createdBy",
        },
        {
            id: "createdAt",
            header: "Created At",
            cell: (item: any) => {
                if (!item.createdAt) return "-";
                const date = new Date(item.createdAt);
                return date.toLocaleString();
            },
            sortingField: "createdAt",
        },
        {
            id: "expiresAt",
            header: "Expires At",
            cell: (item: any) => {
                if (!item.expiresAt) return "Never";
                const date = new Date(item.expiresAt);
                return date.toLocaleString();
            },
            sortingField: "expiresAt",
        },
        {
            id: "active",
            header: "Active",
            cell: (item: any) => {
                const isActive = item.isActive === "true";
                return (
                    <StatusIndicator type={isActive ? "success" : "stopped"}>
                        {isActive ? "Active" : "Inactive"}
                    </StatusIndicator>
                );
            },
            sortingField: "isActive",
        },
    ];

    return (
        <>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: 12 }]}>
                    <div>
                        <TextContent>
                            <h1>API Key Management</h1>
                        </TextContent>
                    </div>
                </Grid>
                <Grid gridDefinition={[{ colspan: 12 }]}>
                    <Table
                        loading={loading}
                        loadingText="Loading API keys..."
                        items={filteredItems}
                        columnDefinitions={columnDefinitions}
                        selectionType="single"
                        selectedItems={selectedItems}
                        onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
                        sortingDisabled={false}
                        filter={
                            <div style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
                                <TextFilter
                                    filteringText={filterText}
                                    filteringAriaLabel="Filter API keys"
                                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                                />
                                <Button
                                    iconName="refresh"
                                    variant="icon"
                                    onClick={() => setReload(true)}
                                    loading={loading}
                                    ariaLabel="Refresh data"
                                />
                            </div>
                        }
                        header={
                            <Header
                                counter={
                                    filterText
                                        ? `(${filteredItems.length}/${allItems.length})`
                                        : `(${allItems.length})`
                                }
                                actions={
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button
                                            disabled={selectedItems.length !== 1}
                                            onClick={() => setEditOpen(true)}
                                        >
                                            Edit
                                        </Button>
                                        <Button
                                            disabled={selectedItems.length !== 1}
                                            onClick={() => setDeleteConfirmOpen(true)}
                                        >
                                            Delete
                                        </Button>
                                        <Button
                                            variant="primary"
                                            onClick={() => setCreateOpen(true)}
                                            data-testid="create-api-key-button"
                                        >
                                            Create API Key
                                        </Button>
                                    </SpaceBetween>
                                }
                            >
                                API Keys
                            </Header>
                        }
                        empty={
                            <Box textAlign="center" color="inherit">
                                <b>No API keys</b>
                                <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                                    No API keys have been created yet.
                                </Box>
                                <Button onClick={() => setCreateOpen(true)}>Create API Key</Button>
                            </Box>
                        }
                    />
                </Grid>
                {error && (
                    <Box padding={{ top: "s" }} color="text-status-error">
                        <TextContent>
                            <p>Error: {error}</p>
                        </TextContent>
                    </Box>
                )}
            </Box>

            <CreateApiKey open={createOpen} setOpen={setCreateOpen} setReload={setReload} />

            {selectedItems.length === 1 && (
                <UpdateApiKey
                    open={editOpen}
                    setOpen={setEditOpen}
                    setReload={setReload}
                    apiKey={selectedItems[0]}
                />
            )}

            <Modal
                visible={deleteConfirmOpen}
                onDismiss={() => setDeleteConfirmOpen(false)}
                header="Delete API Key"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button variant="link" onClick={() => setDeleteConfirmOpen(false)}>
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={handleDelete}
                                disabled={deleteInProgress}
                                data-testid="confirm-delete-api-key-button"
                            >
                                {deleteInProgress ? "Deleting..." : "Delete"}
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <Box>
                    Are you sure you want to delete the API key{" "}
                    <strong>{selectedItems[0]?.apiKeyName}</strong>? This action cannot be undone.
                </Box>
            </Modal>
        </>
    );
}
