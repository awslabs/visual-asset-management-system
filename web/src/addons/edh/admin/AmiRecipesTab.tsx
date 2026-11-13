/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { createContext, useMemo, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import RowActionsButton from "../../../features/orchestration/components/RowActionsButton";
import SearchInput from "../../../features/orchestration/components/SearchInput";
import { btnPrimary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useEdhAmiRecipes, useEdhAmiRecipeMutations } from "../api/amiRecipeQueries";
import { countText, useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import { useRemovedRowRef } from "../../../features/orchestration/components/useReturnFocus";
import NoMatches, { noMatchesText } from "../components/NoMatches";
import { osFamilyLabel } from "../labels";
import { baseOsLabel } from "./RegisterImageDialog";
import AmiRecipeFormDialog from "./AmiRecipeFormDialog";
import AmiBuildsPanel, { BuildStatusBadge } from "./AmiBuildsPanel";
import ConfirmActionDialog, {
    controlNamed,
    rowActionsFor,
    rowActionsTrigger,
} from "./ConfirmActionDialog";
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

// The build statuses before a build ends: one in them is still running.
const RUNNING_BUILD_STATUSES: ReadonlySet<string> = new Set(["QUEUED", "BUILDING", "REGISTERING"]);

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

/**
 * The build instance as the Build now confirmation names it: `(<type>)`, or for a recipe with
 * several build instance types `of one of these types (<type>, <type>)`.
 */
const buildInstanceText = (recipe: Pick<EdhAmiRecipe, "buildInstanceType">) => {
    const types = Array.isArray(recipe.buildInstanceType)
        ? recipe.buildInstanceType
        : [recipe.buildInstanceType];
    return types.length > 1 ? `of one of these types (${types.join(", ")})` : `(${types[0]})`;
};

// The search box's accessible name, which a delete also returns focus to.
const SEARCH_LABEL = "Search AMI recipes";

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
    // The recipe both Build now paths ask to confirm a build of; the build starts from the dialog.
    const [buildTarget, setBuildTarget] = useState<EdhAmiRecipe | null>(null);
    // The recipes with a Build now request in flight: the ref guards the handler, the state renders
    // the menus.
    const buildsInFlightRef = useRef(new Set<string>());
    const [buildsInFlight, setBuildsInFlight] = useState<ReadonlySet<string>>(() => new Set());
    // The tab's controls, where a confirmation finds the element it returns focus to.
    const panelRef = useRef<HTMLDivElement>(null);
    // The recipe the delete removed: its row leaves the list, so its confirmation returns focus to
    // the search box rather than to the row's trigger.
    const deletedRecipeRef = useRemovedRowRef<string>(deleteTarget);
    // The form the last submit saved: a recipe it created joins the list, so the form returns focus
    // to the search box rather than to the control that opened it.
    const savedFormRef = useRef<FormTarget | null>(null);
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

    const confirmBuild = async () => {
        const recipe = buildTarget;
        // A second Start build before the first answers neither sends nor closes the dialog.
        if (!recipe || buildsInFlightRef.current.has(recipe.recipeId)) return;
        await handleBuildNow(recipe);
        // The dialog closes once the request is answered, unless it has been opened on another recipe.
        setBuildTarget((current) => (current?.recipeId === recipe.recipeId ? null : current));
    };

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
            savedFormRef.current = target;
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
            deletedRecipeRef.current = target.recipeId;
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

    // Stable across a poll: a fresh `cell` function is a new element type, which remounts the cell and
    // closes an open menu. The cells reach only state setters, which never change identity, and read
    // the in-flight builds from context.
    const columns = useMemo<ColumnDef<EdhAmiRecipe>[]>(() => {
        const rowActions = (recipe: EdhAmiRecipe, building: boolean) => {
            const admin = recipe.source === "admin";
            const items: ContextMenuItem[] = [
                {
                    label: "Build now",
                    onSelect: () => setBuildTarget(recipe),
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
                        <RowActionsButton
                            {...rowActionsFor(recipe.recipeId)}
                            aria-label={`Actions for ${recipe.displayName}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        />
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
                                <span className="flex items-center gap-2 flex-wrap text-xs">
                                    <span className="text-amber-700 dark:text-amber-400">
                                        Recipe is newer than latest build
                                    </span>
                                    <BuildsInFlight.Consumer>
                                        {(inFlight) =>
                                            canStartBuild &&
                                            row.original.enabled &&
                                            !inFlight.has(row.original.recipeId) &&
                                            !RUNNING_BUILD_STATUSES.has(
                                                row.original.latestBuild?.status ?? ""
                                            ) && (
                                                <button
                                                    type="button"
                                                    aria-label={`Build now: ${row.original.displayName}`}
                                                    onClick={() => setBuildTarget(row.original)}
                                                    className="bg-transparent border-0 p-0 font-semibold text-blue-600 dark:text-blue-400 underline cursor-pointer rounded hover:no-underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                >
                                                    Build now
                                                </button>
                                            )
                                        }
                                    </BuildsInFlight.Consumer>
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
    }, [canStartBuild, canListBuilds, canUpdate, canCreate, canDelete]);

    return (
        <div ref={panelRef} className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        ariaLabel={SEARCH_LABEL}
                        placeholder="Search recipes"
                    />
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel="Refresh AMI recipes"
                    />
                    <span className="text-sm text-text-secondary">
                        {countText(visibleRows.length, rows.length, "recipes")}
                    </span>
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
                    {visibleRows.length === 0 && (
                        <NoMatches>{noMatchesText("recipes", searchText)}</NoMatches>
                    )}
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
                    // New recipe gets focus back, a form opened from a row's menu returns to the
                    // row's actions, and a recipe the form created goes to the search box.
                    returnFocusTo={(opener) =>
                        savedFormRef.current === formTarget && formTarget.mode !== "edit"
                            ? controlNamed(panelRef.current, SEARCH_LABEL)
                            : formTarget.mode === "new"
                            ? opener
                            : rowActionsTrigger(panelRef.current, formTarget.recipe.recipeId)
                    }
                />
            )}

            {buildsFor && (
                <AmiBuildsPanel
                    recipe={buildsFor}
                    open
                    onOpenChange={(isOpen) => !isOpen && setBuildsFor(null)}
                    can={can}
                    returnFocusTo={() => rowActionsTrigger(panelRef.current, buildsFor.recipeId)}
                />
            )}

            {buildTarget && (
                <ConfirmActionDialog
                    open
                    title={`Build ${buildTarget.displayName}`}
                    confirmLabel="Start build"
                    // A build replaces nothing until it succeeds, so it confirms in the affirmative blue.
                    destructive={false}
                    busy={buildsInFlight.has(buildTarget.recipeId)}
                    busyLabel="Starting build..."
                    onCancel={() => setBuildTarget(null)}
                    onConfirm={() => void confirmBuild()}
                    // The button beside the marker while it is still there; the menu's item closes
                    // with the menu, and the button leaves while the build is requested, so focus
                    // then goes to the row's actions.
                    returnFocusTo={(opener) =>
                        opener ?? rowActionsTrigger(panelRef.current, buildTarget.recipeId)
                    }
                    message={
                        <div className="space-y-2">
                            <p>
                                Start a build of the recipe{" "}
                                <strong>{buildTarget.displayName}</strong>? EC2 Image Builder runs
                                it on an Amazon EC2 build instance {buildInstanceText(buildTarget)},
                                which is billed while the build runs and terminated when it ends. A
                                build still running after the deployment&apos;s build timeout (3
                                hours by default) fails.
                            </p>
                            <p>
                                A build that succeeds is registered in the EDH catalog as the
                                recipe&apos;s next version and retires the recipe&apos;s other
                                available builds; desktops launched from them can still be started.
                            </p>
                        </div>
                    }
                />
            )}

            {deleteTarget && (
                <ConfirmActionDialog
                    open
                    title={`Delete ${deleteTarget.displayName}`}
                    confirmLabel="Delete"
                    busy={remove.isPending}
                    busyLabel="Deleting..."
                    onCancel={() => setDeleteTarget(null)}
                    onConfirm={handleDelete}
                    returnFocusTo={() =>
                        deletedRecipeRef.current === deleteTarget.recipeId
                            ? controlNamed(panelRef.current, SEARCH_LABEL)
                            : rowActionsTrigger(panelRef.current, deleteTarget.recipeId)
                    }
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
