/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { createContext, useCallback, useMemo, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import SearchInput from "../../../features/orchestration/components/SearchInput";
import { btnPrimary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useEdhAmiRecipes, useEdhAmiRecipeMutations } from "../api/amiRecipeQueries";
import { useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import { osFamilyLabel } from "../labels";
import { baseOsLabel } from "./RegisterImageDialog";
import AmiRecipeFormDialog from "./AmiRecipeFormDialog";
import AmiBuildsPanel, { BuildStatusBadge } from "./AmiBuildsPanel";
import ConfirmActionDialog from "./ConfirmActionDialog";
import type {
    EdhAmiRecipe,
    EdhAmiRecipeKind,
    EdhAmiRecipeRequest,
    EdhAmiRecipeSource,
} from "../types";

interface AmiRecipesTabProps {
    can: (method: string, pathTemplate: string) => boolean;
}

const KIND_LABEL: Record<EdhAmiRecipeKind, string> = { vdi: "VDI", simulation: "Simulation" };
// One chip colour per kind, the pairs the images list uses for its own kinds.
const KIND_BADGE: Record<EdhAmiRecipeKind, string> = {
    vdi: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    simulation: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
};

const SOURCE_LABEL: Record<EdhAmiRecipeSource, string> = { config: "Config", admin: "Admin" };

// The rows shown before the first answer; one instance, so the search memo's dependency holds.
const NO_RECIPES: EdhAmiRecipe[] = [];

// What the recipe form is open on: a new recipe, a recipe itself (read-only when config-sourced), or
// a new recipe prefilled from the one being duplicated.
type FormTarget =
    | { mode: "new" }
    | { mode: "edit"; recipe: EdhAmiRecipe }
    | { mode: "duplicate"; recipe: EdhAmiRecipe };

const FORM_TOASTS: Record<FormTarget["mode"], { done: string; failed: string }> = {
    new: { done: "Recipe created", failed: "Create failed" },
    edit: { done: "Recipe updated", failed: "Update failed" },
    duplicate: { done: "Recipe duplicated", failed: "Duplicate failed" },
};

// The recipes with a Build now request in flight, read by the row menus.
const BuildsInFlight = createContext<ReadonlySet<string>>(new Set());
const BUILD_IN_FLIGHT_HINT = "A build request for this recipe is in progress.";

/** The OS column: the family, and for a VDI recipe the base OS the software stack is built for. */
const recipeOsLabel = (recipe: Pick<EdhAmiRecipe, "kind" | "osFamily" | "baseOs">) =>
    recipe.kind === "vdi"
        ? `${osFamilyLabel(recipe.osFamily)} (${baseOsLabel(recipe.baseOs)})`
        : osFamilyLabel(recipe.osFamily);

/** Whether the stored recipe has moved on since the newest build was taken from it. */
const isNewerThanLatestBuild = (recipe: EdhAmiRecipe) =>
    !!recipe.latestBuild && recipe.latestBuild.contentHash !== recipe.contentHash;

const searchableText = (recipe: EdhAmiRecipe) =>
    [
        recipe.displayName,
        recipe.recipeId,
        KIND_LABEL[recipe.kind],
        SOURCE_LABEL[recipe.source],
        recipeOsLabel(recipe),
        recipe.stackName,
        recipe.latestBuild?.edhStackName,
        recipe.latestBuild?.amiId,
        recipe.description,
    ]
        .filter(Boolean)
        .join(" ");

/**
 * The AMI recipes an administrator defines and the builds they produce. A recipe that came from the
 * deployment configuration is listed and buildable but only readable — it is changed in the
 * configuration and redeployed. Every other recipe is created, edited and deleted from here, and any
 * recipe can be duplicated into a new one that keeps its bundled files.
 */
const AmiRecipesTab: React.FC<AmiRecipesTabProps> = ({ can }) => {
    const toast = useToast();
    const { data, isLoading, error, refetch, isFetching } = useEdhAmiRecipes();
    const { create, update, remove, startBuild } = useEdhAmiRecipeMutations();
    const { claim, release } = useInFlight();
    const [formTarget, setFormTarget] = useState<FormTarget | null>(null);
    const [buildsFor, setBuildsFor] = useState<EdhAmiRecipe | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<EdhAmiRecipe | null>(null);
    // The recipes with a Build now request in flight: the ref guards the handler, the state renders
    // the menus.
    const buildsInFlightRef = useRef(new Set<string>());
    const [buildsInFlight, setBuildsInFlight] = useState<ReadonlySet<string>>(() => new Set());
    const rows = data ?? NO_RECIPES;
    // The toolbar owns the search box, so the table's own is off.
    const { searchText, setSearchText, visibleRows } = useTextFilter(rows, searchableText);

    // Templates are the addonApiRoutes.py path strings verbatim.
    const canCreate = can("POST", "/addon/edh/admin/ami-recipes");
    const canUpdate = can("PUT", "/addon/edh/admin/ami-recipes/{recipeId}");
    const canDelete = can("DELETE", "/addon/edh/admin/ami-recipes/{recipeId}");
    const canListBuilds = can("GET", "/addon/edh/admin/ami-recipes/{recipeId}/builds");
    const canStartBuild = can("POST", "/addon/edh/admin/ami-recipes/{recipeId}/builds");

    const handleBuildNow = async (recipe: EdhAmiRecipe) => {
        // A second Build now on the recipe before the first answers sends nothing.
        if (buildsInFlightRef.current.has(recipe.recipeId)) return;
        buildsInFlightRef.current.add(recipe.recipeId);
        setBuildsInFlight(new Set(buildsInFlightRef.current));
        try {
            const build = await startBuild.mutateAsync(recipe.recipeId);
            const description = `${recipe.displayName} v${build?.version}`;
            // The service starts or queues a build, or hands back the one building this content.
            if (build?.outcome === "in-progress") {
                toast.info("Build already in progress", { description });
            } else {
                toast.success(build?.outcome === "queued" ? "Build queued" : "Build started", {
                    description,
                });
            }
        } catch (err) {
            toast.error("Build failed to start", {
                description: `${recipe.displayName}: ${toastErrorMessage(err)}`,
            });
        } finally {
            buildsInFlightRef.current.delete(recipe.recipeId);
            setBuildsInFlight(new Set(buildsInFlightRef.current));
        }
    };

    // The row handler closes over the mutation and the toast, both fresh on many renders; the menu
    // reads it through a ref so the column definitions can stay stable across a poll. A fresh
    // `cell` function is a new element type, which remounts the cell and closes an open menu.
    const handlersRef = useRef({ buildNow: handleBuildNow });
    handlersRef.current = { buildNow: handleBuildNow };
    const onBuildNow = useCallback((recipe: EdhAmiRecipe) => {
        void handlersRef.current.buildNow(recipe);
    }, []);

    const handleSubmit = async (body: EdhAmiRecipeRequest) => {
        const target = formTarget;
        if (!target || !claim("form")) return;
        const toasts = FORM_TOASTS[target.mode];
        try {
            if (target.mode === "edit") {
                await update.mutateAsync({ recipeId: target.recipe.recipeId, body });
            } else {
                await create.mutateAsync(body);
            }
            setFormTarget(null);
            toast.success(toasts.done, { description: body.displayName });
        } catch (err) {
            toast.error(toasts.failed, {
                description: `${body.displayName}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release("form");
        }
    };

    const handleDelete = async () => {
        if (!deleteTarget) return;
        const target = deleteTarget;
        if (!claim(target.recipeId)) return;
        try {
            await remove.mutateAsync(target.recipeId);
            setDeleteTarget(null);
            toast.success("Recipe deleted", { description: target.displayName });
        } catch (err) {
            // Close the confirmation and report through a toast; the dialog has no error slot.
            setDeleteTarget(null);
            toast.error("Delete failed", {
                description: `${target.displayName}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release(target.recipeId);
        }
    };

    // Stable across renders for the reason above; the cell reaches only the state setters and the
    // ref-backed build handler, none of which change identity, and reads the in-flight builds from
    // context.
    const columns = useMemo<ColumnDef<EdhAmiRecipe>[]>(() => {
        const rowActions = (recipe: EdhAmiRecipe, building: boolean) => {
            const admin = recipe.source === "admin";
            const items: ContextMenuItem[] = [
                {
                    label: "Build now",
                    onSelect: () => onBuildNow(recipe),
                    hidden: !canStartBuild,
                    disabled: !recipe.enabled || building,
                    hint: building ? BUILD_IN_FLIGHT_HINT : undefined,
                },
                {
                    label: "Builds",
                    onSelect: () => setBuildsFor(recipe),
                    hidden: !canListBuilds,
                },
                {
                    label: "View",
                    onSelect: () => setFormTarget({ mode: "edit", recipe }),
                    hidden: admin,
                },
                {
                    label: "Edit",
                    onSelect: () => setFormTarget({ mode: "edit", recipe }),
                    hidden: !admin || !canUpdate,
                },
                {
                    label: "Duplicate",
                    onSelect: () => setFormTarget({ mode: "duplicate", recipe }),
                    hidden: !canCreate,
                },
                {
                    label: "Delete",
                    danger: true,
                    onSelect: () => setDeleteTarget(recipe),
                    hidden: !admin || !canDelete,
                },
            ];
            if (items.every((item) => item.hidden)) return null;
            return (
                <ContextMenu
                    items={items}
                    trigger={
                        <button
                            aria-label={`Actions for ${recipe.displayName}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        >
                            ⋮
                        </button>
                    }
                />
            );
        };
        return [
            {
                accessorKey: "kind",
                header: "Kind",
                cell: ({ row }) => (
                    <span
                        className={`px-2 py-1 text-xs font-semibold rounded ${
                            KIND_BADGE[row.original.kind]
                        }`}
                    >
                        {KIND_LABEL[row.original.kind]}
                    </span>
                ),
            },
            {
                accessorKey: "displayName",
                header: "Recipe",
                cell: ({ row }) => (
                    <div className="space-y-0.5">
                        <span className="block font-semibold text-text-primary">
                            {row.original.displayName}
                        </span>
                        <span className="block font-mono text-xs text-text-secondary">
                            {row.original.recipeId}
                        </span>
                    </div>
                ),
            },
            {
                accessorKey: "source",
                header: "Source",
                cell: ({ row }) => (
                    <span className="text-sm text-text-secondary">
                        {SOURCE_LABEL[row.original.source]}
                    </span>
                ),
            },
            {
                accessorKey: "osFamily",
                header: "OS",
                cell: ({ row }) => recipeOsLabel(row.original),
            },
            {
                id: "latestBuild",
                header: "Latest build",
                cell: ({ row }) => {
                    const build = row.original.latestBuild;
                    return (
                        <div className="space-y-1">
                            {build ? (
                                <>
                                    <BuildStatusBadge status={build.status} />
                                    <span className="block text-xs text-text-secondary">
                                        Version {build.version}
                                    </span>
                                </>
                            ) : (
                                <span className="text-text-secondary">Never built</span>
                            )}
                            {isNewerThanLatestBuild(row.original) && (
                                <span className="block text-xs text-amber-700 dark:text-amber-400">
                                    Recipe is newer than latest build
                                </span>
                            )}
                        </div>
                    );
                },
            },
            {
                accessorKey: "stackName",
                header: "EDH stack",
                cell: ({ row }) => (
                    <span className="text-sm">
                        {row.original.latestBuild?.edhStackName || row.original.stackName}
                    </span>
                ),
            },
            {
                accessorKey: "enabled",
                header: "Enabled",
                cell: ({ row }) => (row.original.enabled ? "Yes" : "No"),
            },
            // A display column: the table renders the cell as given and offers no sort on it.
            {
                id: "actions",
                header: "Actions",
                cell: ({ row }) => (
                    <BuildsInFlight.Consumer>
                        {(inFlight) =>
                            rowActions(row.original, inFlight.has(row.original.recipeId))
                        }
                    </BuildsInFlight.Consumer>
                ),
            } as ColumnDef<EdhAmiRecipe>,
        ];
    }, [canStartBuild, canListBuilds, canUpdate, canCreate, canDelete, onBuildNow]);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        ariaLabel="Search AMI recipes"
                    />
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel="Refresh AMI recipes"
                    />
                    <span className="text-sm text-text-secondary">{rows.length} recipes</span>
                </div>
                {canCreate && (
                    <button className={btnPrimary} onClick={() => setFormTarget({ mode: "new" })}>
                        New recipe
                    </button>
                )}
            </div>

            {isLoading ? (
                <div className="p-8 text-center text-text-secondary">Loading AMI recipes...</div>
            ) : error ? (
                <div className="p-4 text-vams-error">
                    Error loading AMI recipes: {toastErrorMessage(error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">
                    No AMI recipes are defined.
                </div>
            ) : (
                <BuildsInFlight.Provider value={buildsInFlight}>
                    <DataTable
                        columns={columns}
                        rows={visibleRows}
                        ariaLabel="EDH AMI recipes"
                        getRowId={(recipe) => recipe.recipeId}
                        filtering={false}
                    />
                </BuildsInFlight.Provider>
            )}

            {formTarget && (
                <AmiRecipeFormDialog
                    key={
                        formTarget.mode === "new"
                            ? "new"
                            : `${formTarget.mode}:${formTarget.recipe.recipeId}`
                    }
                    open
                    busy={create.isPending || update.isPending}
                    initial={formTarget.mode === "edit" ? formTarget.recipe : undefined}
                    duplicateSource={
                        formTarget.mode === "duplicate" ? formTarget.recipe : undefined
                    }
                    onOpenChange={(isOpen) => !isOpen && setFormTarget(null)}
                    onSubmit={handleSubmit}
                />
            )}

            {buildsFor && (
                <AmiBuildsPanel
                    recipe={buildsFor}
                    open
                    onOpenChange={(isOpen) => !isOpen && setBuildsFor(null)}
                    can={can}
                />
            )}

            {deleteTarget && (
                <ConfirmActionDialog
                    open
                    title={`Delete ${deleteTarget.displayName}`}
                    confirmLabel="Delete"
                    busy={remove.isPending}
                    onCancel={() => setDeleteTarget(null)}
                    onConfirm={handleDelete}
                    message={
                        <p>
                            Delete the recipe <strong>{deleteTarget.displayName}</strong> and its
                            build history? The AMIs it has already built and their EDH software
                            stacks are left in place, and running sessions are not affected.
                        </p>
                    }
                />
            )}
        </div>
    );
};

export default AmiRecipesTab;
