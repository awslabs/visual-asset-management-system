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
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import TriggerForm from "./TriggerForm";
import TriggerList from "./TriggerList";
import {
    draftFrom,
    draftKey,
    draftToTrigger,
    emptyDraft,
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
}) => {
    const toast = useToast();
    const queryClient = useQueryClient();
    const { data: triggers = NO_TRIGGERS, isLoading } = useTriggers(databaseId, workflowId);

    const [draft, setDraft] = useState<TriggerDraft | null>(null);
    const [pendingDelete, setPendingDelete] = useState<WorkflowTrigger | null>(null);
    // Save error shown inline on the form (e.g. a 400 triggerTemplateErrors rejection, or the
    // duplicate-templates / per-asset-concurrency rejections the backend applies to an extra trigger).
    const [saveError, setSaveError] = useState<string | null>(null);
    // The pending draft the open form came from, so a successful write retires that row whatever
    // key the draft is saved under — it may have been renamed to clear the rejection.
    const pendingSourceRef = useRef<PendingTrigger | null>(null);

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
        const body = withCurrentPipelineTemplates(draftToTrigger(draft), pipelineRefs);
        setTriggerMutation.mutate({ key: draftKey(draft), body });
    };

    const confirmDelete = () => {
        if (pendingDelete) deleteTriggerMutation.mutate(pendingDelete.triggerType);
        setPendingDelete(null);
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
            />
        );
    }

    return (
        <>
            <TriggerList
                triggers={triggers}
                isLoading={isLoading}
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

            <Dialog
                open={!!pendingDelete}
                onOpenChange={(open) => !open && setPendingDelete(null)}
                title="Confirm Delete"
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
