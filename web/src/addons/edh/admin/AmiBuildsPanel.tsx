/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import { btnSecondary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useEdhAmiBuilds, useEdhAmiRecipeMutations } from "../api/amiRecipeQueries";
import ConfirmActionDialog, { controlNamed } from "./ConfirmActionDialog";
import { BusyButton } from "../../../features/orchestration/components/BusyControls";
import { useInFlight } from "../components/useInFlight";
import {
    useRemovedRowRef,
    useReturnFocus,
    type ReturnFocusTo,
} from "../../../features/orchestration/components/useReturnFocus";
import type { EdhAmiBuild, EdhAmiBuildStatus, EdhAmiRecipe } from "../types";

interface AmiBuildsPanelProps {
    recipe: EdhAmiRecipe;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    can: (method: string, pathTemplate: string) => boolean;
    /** Picks the element focus returns to as the panel closes; the opener without it. */
    returnFocusTo?: ReturnFocusTo;
}

/** The words shown for the build statuses the service stores. */
export const BUILD_STATUS_LABEL: Record<EdhAmiBuildStatus, string> = {
    QUEUED: "Queued",
    BUILDING: "Building",
    REGISTERING: "Registering",
    AVAILABLE: "Available",
    REGISTER_FAILED: "Registration failed",
    FAILED: "Failed",
    RETIRED: "Retired",
};

/** One chip colour per build status, the pairs the execution status badge uses. */
export const BUILD_STATUS_BADGE: Record<EdhAmiBuildStatus, string> = {
    QUEUED: "bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300",
    BUILDING: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
    REGISTERING: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
    AVAILABLE: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
    REGISTER_FAILED: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
    FAILED: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    RETIRED: "bg-surface-secondary text-text-secondary",
};

/** A build status as a chip; a status outside the map is shown as it is. */
export const BuildStatusBadge: React.FC<{ status: EdhAmiBuildStatus }> = ({ status }) => (
    <span
        className={`inline-flex items-center px-2 py-1 text-xs font-semibold rounded ${
            BUILD_STATUS_BADGE[status] || "bg-surface-secondary text-text-secondary"
        }`}
    >
        {BUILD_STATUS_LABEL[status] || status}
    </span>
);

// The rows shown before the first answer; one instance, so the sort memo's dependency holds.
const NO_BUILDS: EdhAmiBuild[] = [];

// The refresh button's accessible name, which a retire also returns focus to.
const REFRESH_LABEL = "Refresh builds";

const cell = "orch-outline border border-border-default px-3 py-2 text-sm align-top";

/** A stored timestamp in the reader's own locale; an absent one as a dash. */
const timestamp = (value: string | undefined) => (value ? new Date(value).toLocaleString() : "—");

/**
 * Why a retired or failed build still holds its AMI: the instances that use it, and whether a
 * retire is waiting for them. Nothing once the AMI is deregistered or for an available build.
 */
const KeptAmiNote: React.FC<{ build: EdhAmiBuild }> = ({ build }) => {
    const inUseBy = build.amiInUseBy ?? [];
    if (build.amiDeregisteredAt || build.status === "AVAILABLE") return null;
    if (!build.amiRemovalPending && inUseBy.length === 0) return null;
    const note = build.amiRemovalPending
        ? "AMI deregistered once no desktop uses it"
        : "AMI kept while in use";
    return (
        <p className="text-text-secondary text-xs mt-1">
            {inUseBy.length > 0 ? `${note}: ${inUseBy.join(", ")}` : note}
        </p>
    );
};

/**
 * A build's Retry registration button. It stays focused and reads "Retrying..." while its retry is
 * in flight, and the retry resolves once the builds are read again, so a retry EDH accepted lists
 * the build as Registering and the button leaves the list with focus on it; `onLeaveFocused` then
 * moves focus to a control the panel keeps. A retry the service refused keeps the button, and
 * focus stays on it.
 */
const RetryRegistrationButton: React.FC<{
    busy: boolean;
    onRetry: () => void;
    onLeaveFocused: () => void;
}> = ({ busy, onRetry, onLeaveFocused }) => {
    const buttonRef = useRef<HTMLButtonElement>(null);
    const onLeaveFocusedRef = useRef(onLeaveFocused);
    onLeaveFocusedRef.current = onLeaveFocused;
    // A layout effect's cleanup runs before React takes the button off the page, so focus moves
    // on while the button still holds it, never by way of the page body.
    useLayoutEffect(() => {
        const button = buttonRef.current;
        return () => {
            if (button && button === document.activeElement) onLeaveFocusedRef.current();
        };
    }, []);
    return (
        <BusyButton
            ref={buttonRef}
            busy={busy}
            busyLabel="Retrying..."
            className={btnSecondary}
            onClick={onRetry}
        >
            Retry registration
        </BusyButton>
    );
};

/**
 * The builds of one AMI recipe, newest version first. A build whose registration into the EDH
 * catalog failed can be retried from here, and an available build can be retired — the two actions
 * EDH exposes once Image Builder has finished with a version.
 */
const AmiBuildsPanel: React.FC<AmiBuildsPanelProps> = ({
    recipe,
    open,
    onOpenChange,
    can,
    returnFocusTo,
}) => {
    const returnFocusRef = useReturnFocus(open, returnFocusTo);
    const toast = useToast();
    const { data, isLoading, error, refetch, isFetching } = useEdhAmiBuilds(recipe.recipeId, open);
    const { registerBuild, retireBuild } = useEdhAmiRecipeMutations();
    // One retry or retire per build at a time, the second click of a double-click included; the
    // claims also mark the Retry registration button of a build whose retry is in flight busy.
    const { inFlight, claim, release } = useInFlight();
    const [retireTarget, setRetireTarget] = useState<EdhAmiBuild | null>(null);
    // The panel's controls, where the retire confirmation finds the element it returns focus to.
    const panelRef = useRef<HTMLDivElement>(null);
    // The build the retire retired: its Retire button, which opened the confirmation, leaves the
    // list, so focus returns to the refresh button the panel keeps. The retire resolves once the
    // builds are read again, so that button is enabled by then.
    const retiredVersionRef = useRemovedRowRef<number>(retireTarget);

    // Templates are the addonApiRoutes.py path strings verbatim.
    const canRegister = can(
        "POST",
        "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register"
    );
    const canRetire = can("DELETE", "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}");

    const builds = data ?? NO_BUILDS;
    const rows = useMemo(() => [...builds].sort((a, b) => b.version - a.version), [builds]);

    const describe = (build: EdhAmiBuild) => `${recipe.displayName} v${build.version}`;

    const handleRegister = async (build: EdhAmiBuild) => {
        const key = String(build.version);
        if (!claim(key)) return;
        try {
            await registerBuild.mutateAsync({
                recipeId: recipe.recipeId,
                version: build.version,
            });
            toast.success("Registration retried", { description: describe(build) });
        } catch (err) {
            toast.error("Retry failed", {
                description: `${describe(build)}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release(key);
        }
    };

    const handleRetire = async () => {
        if (!retireTarget) return;
        const target = retireTarget;
        const key = String(target.version);
        if (!claim(key)) return;
        try {
            await retireBuild.mutateAsync({
                recipeId: recipe.recipeId,
                version: target.version,
            });
            retiredVersionRef.current = target.version;
            setRetireTarget(null);
            toast.success("Build retired", { description: describe(target) });
        } catch (err) {
            // Close the confirmation and report through a toast; the dialog has no error slot.
            setRetireTarget(null);
            toast.error("Retire failed", {
                description: `${describe(target)}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release(key);
        }
    };

    return (
        <Dialog
            open={open}
            onOpenChange={onOpenChange}
            title={`Builds of ${recipe.displayName}`}
            size="lg"
            returnFocusRef={returnFocusRef}
            footer={
                <button type="button" className={btnSecondary} onClick={() => onOpenChange(false)}>
                    Close
                </button>
            }
        >
            <div ref={panelRef} className="space-y-3">
                <div className="flex items-center gap-2">
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel={REFRESH_LABEL}
                    />
                    <span className="text-sm text-text-secondary">{rows.length} builds</span>
                </div>

                {isLoading ? (
                    <div className="p-6 text-center text-text-secondary">Loading builds...</div>
                ) : error ? (
                    <div className="p-4 text-vams-error">
                        Error loading builds: {toastErrorMessage(error)}
                    </div>
                ) : rows.length === 0 ? (
                    <div className="p-6 text-center text-text-secondary">
                        This recipe has not been built yet.
                    </div>
                ) : (
                    <table className="orch-outline min-w-full border-collapse border border-border-default">
                        <thead className="bg-surface-secondary">
                            <tr>
                                <th className={`${cell} text-left`}>Version</th>
                                <th className={`${cell} text-left`}>Status</th>
                                <th className={`${cell} text-left`}>AMI</th>
                                <th className={`${cell} text-left`}>EDH stack</th>
                                <th className={`${cell} text-left`}>Requested</th>
                                <th className={`${cell} text-left`}>Completed</th>
                                <th className={`${cell} text-left`}>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((build) => (
                                <tr key={build.version} className="hover:bg-surface-hover">
                                    <td className={`${cell} font-semibold text-text-primary`}>
                                        {build.version}
                                    </td>
                                    <td className={cell}>
                                        <BuildStatusBadge status={build.status} />
                                        {build.error && (
                                            <p className="text-vams-error text-xs mt-1">
                                                {build.error}
                                            </p>
                                        )}
                                        {build.retireWarning && (
                                            <p className="text-vams-error text-xs mt-1">
                                                {build.retireWarning}
                                            </p>
                                        )}
                                        {build.pruneWarning && (
                                            <p className="text-vams-error text-xs mt-1">
                                                {build.pruneWarning}
                                            </p>
                                        )}
                                        <KeptAmiNote build={build} />
                                    </td>
                                    <td className={`${cell} font-mono text-xs`}>
                                        {build.amiId || "—"}
                                    </td>
                                    <td className={cell}>{build.edhStackName || "—"}</td>
                                    <td className={cell}>{timestamp(build.requestedAt)}</td>
                                    <td className={cell}>{timestamp(build.completedAt)}</td>
                                    <td className={cell}>
                                        <div className="flex items-center gap-2">
                                            {canRegister && build.status === "REGISTER_FAILED" && (
                                                <RetryRegistrationButton
                                                    busy={inFlight.has(String(build.version))}
                                                    onRetry={() => handleRegister(build)}
                                                    // The refresh button, where a retire also
                                                    // returns focus.
                                                    onLeaveFocused={() =>
                                                        controlNamed(
                                                            panelRef.current,
                                                            REFRESH_LABEL
                                                        )?.focus()
                                                    }
                                                />
                                            )}
                                            {canRetire && build.status === "AVAILABLE" && (
                                                <button
                                                    type="button"
                                                    className={btnSecondary}
                                                    onClick={() => setRetireTarget(build)}
                                                >
                                                    Retire
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {retireTarget && (
                <ConfirmActionDialog
                    open
                    title={`Retire ${describe(retireTarget)}`}
                    confirmLabel="Retire"
                    busy={retireBuild.isPending}
                    busyLabel="Retiring..."
                    onCancel={() => setRetireTarget(null)}
                    onConfirm={handleRetire}
                    returnFocusTo={(opener) =>
                        retiredVersionRef.current === retireTarget.version
                            ? controlNamed(panelRef.current, REFRESH_LABEL)
                            : opener
                    }
                    message={
                        <p>
                            Retire <strong>{describe(retireTarget)}</strong>? The software stack is
                            removed from the EDH catalog, so no new session can be launched from it.
                            VAMS deregisters the AMI once no desktop uses it, so existing desktops
                            can still be started.
                        </p>
                    }
                />
            )}
        </Dialog>
    );
};

export default AmiBuildsPanel;
