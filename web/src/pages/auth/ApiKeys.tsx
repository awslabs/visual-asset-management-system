/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextContent from "@cloudscape-design/components/text-content";
import TextFilter from "@cloudscape-design/components/text-filter";
import Grid from "@cloudscape-design/components/grid";
import Modal from "@cloudscape-design/components/modal";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import {
    fetchApiKeys,
    deleteApiKey,
    fetchUserApiKeys,
    deleteUserApiKey,
    fetchAllowedApiRoutes,
} from "../../services/APIService";
import { appCache } from "../../services/appCache";
import {
    isApiRouteAllowed,
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../../common/constants/authRoutes";
import { USER_API_KEY_MAX_EXPIRATION_DAYS } from "../../common/constants/apiKeys";
import CreateApiKey from "./CreateApiKey";
import UpdateApiKey from "./UpdateApiKey";
import { usePageTitle } from "../../hooks/usePageTitle";

export type ApiKeyManagementMode = "admin" | "user";

export default function ApiKeys() {
    usePageTitle("API Keys");

    // Available modes, resolved asynchronously from the allowed-API-routes
    // list. Admin mode: GET /auth/api-keys allowed. User mode:
    // GET /auth/user/api-keys allowed. The localStorage cache is preferred,
    // but when it is empty/expired (e.g. this page loads before the login
    // flow's fetch completes) the list is fetched directly rather than
    // guessing -- guessing admin for a self-service-only user would render
    // the wrong form and call the wrong API.
    const [modesResolved, setModesResolved] = useState(false);
    const [showAdminMode, setShowAdminMode] = useState(false);
    const [showUserMode, setShowUserMode] = useState(false);
    const showModeToggle = showAdminMode && showUserMode;

    const [mode, setMode] = useState<ApiKeyManagementMode>("admin");
    const [reload, setReload] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        const resolveModes = async () => {
            let admin = isApiRouteAllowed("/auth/api-keys", "GET");
            let user = isApiRouteAllowed("/auth/user/api-keys", "GET");
            if (admin === null || user === null) {
                // Cache unavailable -- fetch the allowed routes directly
                try {
                    const result = await fetchAllowedApiRoutes();
                    if (result[0] === true && result[1]?.routes) {
                        appCache.setItemWithExpiry(
                            ALLOWED_API_ROUTES_CACHE_KEY,
                            result[1],
                            ALLOWED_API_ROUTES_CACHE_TTL_MILLIS
                        );
                        admin = isApiRouteAllowed("/auth/api-keys", "GET");
                        user = isApiRouteAllowed("/auth/user/api-keys", "GET");
                    }
                } catch (err) {
                    console.log("Error fetching allowed API routes:", err);
                }
            }
            if (cancelled) return;
            // Backwards compatibility: when access is still undeterminable
            // (e.g. older backend without the allowed-routes endpoint),
            // default to admin mode as before.
            const adminMode = admin !== false;
            const userMode = user === true;
            setShowAdminMode(adminMode);
            setShowUserMode(userMode);
            setMode(adminMode ? "admin" : "user");
            setModesResolved(true);
            setReload(true);
        };
        resolveModes();
        return () => {
            cancelled = true;
        };
    }, []);
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
            const result = mode === "user" ? await fetchUserApiKeys() : await fetchApiKeys();
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
    }, [mode]);

    useEffect(() => {
        if (reload && modesResolved) {
            loadData();
        }
    }, [reload, modesResolved, loadData]);

    // Reload when switching between admin and user modes
    useEffect(() => {
        if (modesResolved) {
            setReload(true);
        }
    }, [mode, modesResolved]);

    const handleDelete = async () => {
        if (selectedItems.length !== 1) return;
        setDeleteInProgress(true);
        try {
            const deleteFn = mode === "user" ? deleteUserApiKey : deleteApiKey;
            const result = await deleteFn({ apiKeyId: selectedItems[0].apiKeyId });
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
            cell: (item: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {item.description || "-"}
                </span>
            ),
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
                {showModeToggle && (
                    <Grid gridDefinition={[{ colspan: 12 }]}>
                        <Box padding={{ bottom: "s" }}>
                            <SegmentedControl
                                selectedId={mode}
                                onChange={({ detail }) =>
                                    setMode(detail.selectedId as ApiKeyManagementMode)
                                }
                                label="API key management mode"
                                options={[
                                    { text: "All Keys (Admin)", id: "admin" },
                                    { text: "My Keys", id: "user" },
                                ]}
                            />
                        </Box>
                    </Grid>
                )}
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
                            <div
                                style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}
                            >
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
                                description={
                                    mode === "user"
                                        ? `Your own API keys. Keys require an expiration date no more than ${USER_API_KEY_MAX_EXPIRATION_DAYS} days from creation.`
                                        : "All API keys across users."
                                }
                                actions={
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button
                                            disabled={!modesResolved || selectedItems.length !== 1}
                                            onClick={() => setEditOpen(true)}
                                        >
                                            Edit
                                        </Button>
                                        <Button
                                            disabled={!modesResolved || selectedItems.length !== 1}
                                            onClick={() => setDeleteConfirmOpen(true)}
                                        >
                                            Delete
                                        </Button>
                                        <Button
                                            variant="primary"
                                            disabled={!modesResolved}
                                            onClick={() => setCreateOpen(true)}
                                            data-testid="create-api-key-button"
                                        >
                                            Create API Key
                                        </Button>
                                    </SpaceBetween>
                                }
                            >
                                {mode === "user" ? "My API Keys" : "API Keys"}
                            </Header>
                        }
                        empty={
                            <Box textAlign="center" color="inherit">
                                <b>No API keys</b>
                                <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                                    No API keys have been created yet.
                                </Box>
                                <Button
                                    disabled={!modesResolved}
                                    onClick={() => setCreateOpen(true)}
                                >
                                    Create API Key
                                </Button>
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

            <CreateApiKey
                open={createOpen}
                setOpen={setCreateOpen}
                setReload={setReload}
                userMode={mode === "user"}
            />

            {selectedItems.length === 1 && (
                <UpdateApiKey
                    open={editOpen}
                    setOpen={setEditOpen}
                    setReload={setReload}
                    apiKey={selectedItems[0]}
                    userMode={mode === "user"}
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
