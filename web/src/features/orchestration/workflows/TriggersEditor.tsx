/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTriggers } from "../api/queries";
import { setTrigger, deleteTrigger } from "../api/workflows";
import { invalidateTriggerQueries } from "../api/triggerCache";
import Dialog from "../components/Dialog";
import { useRemovedRowRef } from "../components/useReturnFocus";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import TriggerForm from "./TriggerForm";
import TriggerList from "./TriggerList";
import {
    TRIGGER_ROUTE,
    draftFrom,
    draftKey,
    draftToTrigger,
    emptyDraft,
    typeLabel,
    validateDraft,
    withCurrentPipelineTemplates,
} from "./triggerDraft";
import type { PendingTrigger, TriggerDraft } from "./triggerDraft";
import { triggerBtnDanger, triggerBtnSecondary } from "./triggerStyles";

export type { PendingTrigger } from "./triggerDraft";

interface TriggersEditorProps {
    databaseId: string;
    workflowId: string;
    pipelineRefs: SpecifiedPipelineRef[];
    /**
     * Triggers the create flow could not write, owned by the caller. They are listed as "Not saved"
     * rows with a Retry action, and the list is handed back through `onPendingTriggersChange` as
     * drafts are written here or turn up stored.
     */
    pendingTriggers?: PendingTrigger[];
    onPendingTriggersChange?: (remaining: PendingTrigger[]) => void;
    /**
     * Asks for the first unsaved pending draft to open in the form with its server message. The
     * request is acknowledged through `onPendingOpened`, so the owner decides how many times a
     * hand-off opens a form, whatever remounts this editor goes through.
     */
    openFirstPending?: boolean;
    onPendingOpened?: () => void;
    /**
     * True for a system workflow: triggers can be switched on and off but not added, deleted, or
     * given other filters or templates (the backend answers 400 to all of those).
     */
    systemLocked?: boolean;
}

const NO_TRIGGERS: WorkflowTrigger[] = [];
const NO_PENDING: PendingTrigger[] = [];

/**
 * A workflow's triggers.
 *
 * A workflow may carry SEVERAL triggers of one type, each with its own input-file filters and default
 * templates, so an upload can run the workflow once per matching trigger — the same workflow reacting
 * differently to different uploads. The editor is therefore a list keyed by trigger key rather than a
 * single form, and the types it offers come from TRIGGER_TYPES: adding a type there surfaces it here
 * with no change to this component. `fileUpload` is the only type implemented today.
 *
 * The first trigger of a type is keyed by the bare type and an additional one carries an id suffix,
 * which is what lets a trigger created before multiple triggers existed keep working untouched.
 *
 * Every write goes to the trigger endpoint of a stored workflow; the list and form themselves are
 * shared with the create flow's draft editor.
 */
const TriggersEditor: React.FC<TriggersEditorProps> = ({
    databaseId,
    workflowId,
    pipelineRefs,
    pendingTriggers = NO_PENDING,
    onPendingTriggersChange,
    openFirstPending = false,
    onPendingOpened,
    systemLocked = false,
}) => {
    const toast = useToast();
    const queryClient = useQueryClient();
    const { data: triggers = NO_TRIGGERS, isLoading } = useTriggers(databaseId, workflowId);
    // The route check comes first: a trigger action the role may not call is hidden, on a system
    // workflow as on any other; only a callable one is then graded on the record.
    const { can } = useAllowedRoutes();
    const canSet = can("PUT", TRIGGER_ROUTE);
    const canDelete = can("DELETE", TRIGGER_ROUTE);

    const [draft, setDraft] = useState<TriggerDraft | null>(null);
    const [pendingDelete, setPendingDelete] = useState<WorkflowTrigger | null>(null);
    const deletedKeyRef = useRemovedRowRef<string>(pendingDelete);
    // Save error shown inline on the form (e.g. a 400 triggerTemplateErrors rejection, or the
    // duplicate-templates / per-asset-concurrency rejections the backend applies to an extra trigger).
    const [saveError, setSaveError] = useState<string | null>(null);
    // The pending draft the open form came from, so a successful write retires that row whatever
    // key the draft is saved under — it may have been renamed to clear the rejection.
    const pendingSourceRef = useRef<PendingTrigger | null>(null);
    // A save closes the form; focus held inside it moves to the saved trigger's Edit button, or,
    // while the re-read that lists a trigger just added is in flight, to the Add button of its type.
    const listRef = useRef<HTMLDivElement>(null);
    const savedKeyRef = useRef<string | null>(null);
    // Set by a save as it closes the form; a form closed without one leaves focus as it was.
    useEffect(() => {
        if (draft) savedKeyRef.current = null;
    }, [draft]);
    const focusAfterSave = () => {
        const key = savedKeyRef.current;
        if (!key) return null;
        const buttons = Array.from(listRef.current?.querySelectorAll("button") ?? []);
        const addLabel = `Add ${typeLabel(key.split("#")[0]).toLowerCase()} trigger`;
        return (
            buttons.find((button) => button.getAttribute("aria-label") === `Edit trigger ${key}`) ??
            buttons.find((button) => button.textContent?.trim() === addLabel)
        );
    };

    // A pending draft whose key is now among the stored triggers has been written, here or elsewhere.
    const isStored = (item: PendingTrigger) =>
        triggers.some((t: WorkflowTrigger) => t.triggerType === item.draft.triggerType);

    // Opened as an add: the row does not exist server-side, so the name stays editable and the
    // collision check runs against what is stored.
    const openPending = (item: PendingTrigger) => {
        pendingSourceRef.current = item;
        setDraft({ ...draftFrom(item.draft), editingKey: "" });
        setSaveError(item.error);
    };

    // The first unsaved pending draft opens in the form with its server message, so the correction
    // and the re-PUT happen where the rejection is explained; the rest wait as "Not saved" rows.
    // Nothing opens until the workflow id is known, since the form's Save writes to it, nor until
    // the stored list has loaded, since a draft that turns out to be stored has nothing to fix.
    useEffect(() => {
        if (!openFirstPending || !workflowId || isLoading) return;
        const first = pendingTriggers.find((item) => !isStored(item));
        if (first) openPending(first);
        onPendingOpened?.();
        // isStored/openPending read `triggers`, which is a dependency; the callbacks are the owner's.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [openFirstPending, workflowId, isLoading, pendingTriggers, triggers, onPendingOpened]);

    // Retire the pending drafts that are stored once the list is known, so the owner's list is
    // what is genuinely still unsaved.
    useEffect(() => {
        if (isLoading || !onPendingTriggersChange) return;
        const remaining = pendingTriggers.filter((item) => !isStored(item));
        if (remaining.length !== pendingTriggers.length) onPendingTriggersChange(remaining);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isLoading, pendingTriggers, triggers, onPendingTriggersChange]);

    const invalidate = () => invalidateTriggerQueries(queryClient, databaseId, workflowId);

    const setTriggerMutation = useMutation({
        // The service returns a [ok, data] tuple; throwing on a falsy `ok` is what routes the
        // failure into onError. An async function is used directly rather than wrapping it in a
        // `new Promise(async ...)` executor, where a throw before the reject would be swallowed.
        mutationFn: async ({ key, body }: { key: string; body: WorkflowTrigger }) => {
            const [ok, data] = await setTrigger(databaseId, workflowId, key, body);
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to set trigger");
            return data;
        },
        onSuccess: (_data, variables) => {
            invalidate();
            setSaveError(null);
            savedKeyRef.current = variables.key;
            setDraft(null);
            // The written draft leaves the pending list: the one the form was opened from, and any
            // other pending draft whose key was just taken.
            const source = pendingSourceRef.current;
            pendingSourceRef.current = null;
            const remaining = pendingTriggers.filter(
                (item) => item !== source && item.draft.triggerType !== variables.key
            );
            if (remaining.length !== pendingTriggers.length) onPendingTriggersChange?.(remaining);
            // The form closes on success, so the toast is the only confirmation the trigger saved.
            toast.success("Trigger saved", { description: variables.key });
        },
        onError: (err) => {
            // Kept inline as well: a rejection names the offending template or the conflicting
            // sibling trigger, which belongs next to the fields that produced it.
            const message = toastErrorMessage(err, "Failed to set trigger");
            setSaveError(message);
            toast.error("Save failed", { description: message });
        },
    });

    const deleteTriggerMutation = useMutation({
        mutationFn: async (key: string) => {
            const [ok, data] = await deleteTrigger(databaseId, workflowId, key);
            if (!ok) throw new Error(typeof data === "string" ? data : "Failed to delete trigger");
            return data;
        },
        onSuccess: (_data, key) => {
            invalidate();
            toast.success("Trigger deleted", { description: key });
        },
        onError: (err) => {
            // The confirm dialog has already closed by this point, so a toast is the only place the
            // failure can be reported — without it a rejected delete looked like a success.
            toast.error("Delete failed", {
                description: toastErrorMessage(err, "Failed to delete trigger"),
            });
        },
    });

    const existingKeys = triggers.map((t: WorkflowTrigger) => t.triggerType);

    const handleSave = () => {
        if (!draft || !workflowId) return;
        const { triggerIdInvalid, keyCollides } = validateDraft(draft, existingKeys);
        if (triggerIdInvalid || keyCollides) return;
        setSaveError(null);
        const key = draftKey(draft);
        const stored = systemLocked
            ? triggers.find((t: WorkflowTrigger) => t.triggerType === draft.editingKey)
            : undefined;
        if (stored) {
            // Only `enabled` may change: the stored filters and templates are sent back verbatim,
            // which is what the backend's value-based lock compares them against.
            setTriggerMutation.mutate({
                key,
                body: {
                    triggerType: key,
                    enabled: draft.enabled,
                    inputFileFilters: stored.inputFileFilters || { allow: [], exclude: [] },
                    defaultTemplateIds: stored.defaultTemplateIds || {},
                },
            });
            return;
        }
        const body = withCurrentPipelineTemplates(draftToTrigger(draft), pipelineRefs);
        setTriggerMutation.mutate({ key, body });
    };

    const confirmDelete = () => {
        if (pendingDelete) {
            deletedKeyRef.current = pendingDelete.triggerType;
            deleteTriggerMutation.mutate(pendingDelete.triggerType);
        }
        setPendingDelete(null);
    };

    // The delete confirmation returns focus to the Delete button that opened it, unless it deleted
    // the trigger: that row leaves the list, so focus goes to the Add button of the trigger's type,
    // or, when the role may not add, to another trigger's row, or, when it deleted the only trigger,
    // to the list itself, which stays on the page.
    const focusAfterDelete = () => {
        const key = deletedKeyRef.current;
        if (!key) return null;
        const buttons = Array.from(listRef.current?.querySelectorAll("button") ?? []);
        const addLabel = `Add ${typeLabel(key.split("#")[0]).toLowerCase()} trigger`;
        return (
            buttons.find((button) => button.textContent?.trim() === addLabel) ??
            buttons.find((button) => {
                const name = button.getAttribute("aria-label") || "";
                return /^(Edit|Delete) trigger /.test(name) && !name.endsWith(` trigger ${key}`);
            }) ??
            listRef.current
        );
    };

    const visiblePending = pendingTriggers.filter((item) => !isStored(item));

    if (draft) {
        return (
            <TriggerForm
                draft={draft}
                onChange={setDraft}
                onSave={handleSave}
                onCancel={() => {
                    pendingSourceRef.current = null;
                    setDraft(null);
                    setSaveError(null);
                }}
                isSaving={setTriggerMutation.isPending}
                saveError={saveError}
                pipelineRefs={pipelineRefs}
                existingKeys={existingKeys}
                locked={systemLocked}
                focusAfterSave={focusAfterSave}
            />
        );
    }

    return (
        <>
            {/* Focusable from script only: it takes focus when a delete leaves no control in it. */}
            <div ref={listRef} role="group" aria-label="Triggers" tabIndex={-1}>
                <TriggerList
                    triggers={triggers}
                    isLoading={isLoading}
                    locked={systemLocked}
                    canSet={canSet}
                    canDelete={canDelete}
                    onAdd={(baseType) => {
                        pendingSourceRef.current = null;
                        setSaveError(null);
                        setDraft(emptyDraft(baseType));
                    }}
                    onEdit={(trigger) => {
                        pendingSourceRef.current = null;
                        setSaveError(null);
                        setDraft(draftFrom(trigger));
                    }}
                    onDelete={setPendingDelete}
                    pending={visiblePending}
                    onRetry={openPending}
                />
            </div>

            <Dialog
                open={!!pendingDelete}
                onOpenChange={(open) => !open && setPendingDelete(null)}
                title="Confirm Delete"
                returnFocusTo={focusAfterDelete}
                footer={
                    <>
                        <button
                            type="button"
                            onClick={() => setPendingDelete(null)}
                            className={triggerBtnSecondary}
                        >
                            Cancel
                        </button>
                        <button type="button" onClick={confirmDelete} className={triggerBtnDanger}>
                            Delete
                        </button>
                    </>
                }
            >
                {/* Names the trigger: with several of one type, "this trigger" would not say which. */}
                <p>Delete the trigger {pendingDelete?.triggerType}?</p>
            </Dialog>
        </>
    );
};

export default TriggersEditor;
