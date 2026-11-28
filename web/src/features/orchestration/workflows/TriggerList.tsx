/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { WorkflowTrigger } from "../types";
import { TRIGGER_TYPES, triggerBaseTypeOf } from "../types";
import { typeLabel } from "./triggerDraft";
import type { PendingTrigger } from "./triggerDraft";
import { triggerBtnDanger, triggerBtnPrimary, triggerCell } from "./triggerStyles";
import { DisabledReasonButton } from "../components/DisabledReason";
import { SYSTEM_RECORD_REASONS } from "../permissions/systemRecordReasons";

interface TriggerListProps {
    triggers: WorkflowTrigger[];
    isLoading: boolean;
    onAdd: (baseType: string) => void;
    onEdit: (trigger: WorkflowTrigger) => void;
    onDelete: (trigger: WorkflowTrigger) => void;
    /** Triggers that could not be written, shown as "Not saved" rows with a Retry action. */
    pending?: PendingTrigger[];
    onRetry?: (item: PendingTrigger) => void;
    /**
     * True for a system workflow: its triggers can be edited (to switch them on and off) but not
     * added or deleted, so those actions are disabled with the reason.
     */
    locked?: boolean;
    /**
     * Whether the role may call the route behind Add, Edit and Retry (a trigger PUT), and behind
     * Delete. An action the role may not call is hidden, whether or not the workflow is locked.
     */
    canSet?: boolean;
    canDelete?: boolean;
}

/**
 * The trigger list panel: one add button per configurable type, the stored (or drafted) rows, and
 * any rows a save could not write. Presentational — the owner decides what add/edit/delete/retry do
 * and which of them the role may call.
 */
const TriggerList: React.FC<TriggerListProps> = ({
    triggers,
    isLoading,
    onAdd,
    onEdit,
    onDelete,
    pending = [],
    onRetry,
    locked = false,
    canSet = true,
    canDelete = true,
}) => {
    const addReason = locked ? SYSTEM_RECORD_REASONS.addTrigger : undefined;
    const deleteReason = locked ? SYSTEM_RECORD_REASONS.deleteTrigger : undefined;
    const hasRowActions = canSet || canDelete;
    return (
        <div className="orch-outline border border-border-default rounded p-4 sm:p-6 bg-surface-container">
            <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
                <h2 className="text-xl font-semibold text-text-primary">Triggers</h2>
                <div className="flex flex-wrap gap-2">
                    {/* One add button per configurable type, so a type added to TRIGGER_TYPES
                        appears here without touching this component. */}
                    {canSet &&
                        TRIGGER_TYPES.map((t) => (
                            <DisabledReasonButton
                                key={t.type}
                                onClick={() => onAdd(t.type)}
                                // The reason replaces the type description while the button is
                                // disabled, so one tooltip says why rather than two competing.
                                title={addReason ? undefined : t.description}
                                disabledReason={addReason}
                                className={triggerBtnPrimary}
                            >
                                Add {t.label.toLowerCase()} trigger
                            </DisabledReasonButton>
                        ))}
                </div>
            </div>

            {isLoading ? (
                // An in-flight list is not an empty one: the query defaults to [], so rendering the
                // empty state while it loads tells the reader this workflow has no trigger when it
                // may well have several.
                <div className="text-center py-8 text-text-secondary">Loading triggers...</div>
            ) : triggers.length === 0 && pending.length === 0 ? (
                <div className="text-center py-8">
                    <div className="font-semibold text-text-primary">No triggers configured</div>
                    <p className="mt-1 text-text-secondary">
                        This workflow runs only when it is started explicitly.
                    </p>
                </div>
            ) : (
                triggers.length > 0 && (
                    // The table scrolls inside its own box, so on a phone-width page the Actions
                    // column is reached by scrolling the table rather than the whole page. When no
                    // row holds a button the box takes focus itself, so a keyboard user can still
                    // scroll it; otherwise focusing a row action scrolls it into view.
                    <div
                        className="overflow-x-auto"
                        data-testid="trigger-table-scroll"
                        role="region"
                        aria-label="Triggers"
                        tabIndex={hasRowActions ? undefined : 0}
                    >
                        <table className="orch-outline min-w-full border-collapse border border-border-default">
                            <thead className="bg-surface-secondary">
                                <tr>
                                    <th className={`${triggerCell} text-left`}>Type</th>
                                    <th className={`${triggerCell} text-left`}>Name</th>
                                    <th className={`${triggerCell} text-left`}>Fires on</th>
                                    <th className={`${triggerCell} text-left`}>Enabled</th>
                                    {hasRowActions && (
                                        <th className={`${triggerCell} text-left`}>Actions</th>
                                    )}
                                </tr>
                            </thead>
                            <tbody>
                                {triggers.map((trigger) => {
                                    const allow = trigger.inputFileFilters?.allow || [];
                                    const triggerId =
                                        trigger.triggerId ||
                                        (trigger.triggerType || "").split("#")[1] ||
                                        "";
                                    return (
                                        <tr
                                            key={trigger.triggerType}
                                            className="hover:bg-surface-hover"
                                        >
                                            <td className={triggerCell}>
                                                {typeLabel(triggerBaseTypeOf(trigger))}
                                            </td>
                                            <td className={triggerCell}>
                                                {triggerId || (
                                                    <span className="text-text-secondary">
                                                        (first of type)
                                                    </span>
                                                )}
                                            </td>
                                            <td className={triggerCell}>
                                                {allow.length ? (
                                                    allow.join(", ")
                                                ) : (
                                                    <span className="text-text-secondary">
                                                        any uploaded file
                                                    </span>
                                                )}
                                            </td>
                                            <td className={triggerCell}>
                                                {trigger.enabled ? "Yes" : "No"}
                                            </td>
                                            {hasRowActions && (
                                                <td className={triggerCell}>
                                                    <div className="flex gap-2">
                                                        {canSet && (
                                                            <button
                                                                type="button"
                                                                onClick={() => onEdit(trigger)}
                                                                aria-label={`Edit trigger ${trigger.triggerType}`}
                                                                className={triggerBtnPrimary}
                                                            >
                                                                Edit
                                                            </button>
                                                        )}
                                                        {canDelete && (
                                                            <DisabledReasonButton
                                                                onClick={() => onDelete(trigger)}
                                                                aria-label={`Delete trigger ${trigger.triggerType}`}
                                                                disabledReason={deleteReason}
                                                                className={triggerBtnDanger}
                                                            >
                                                                Delete
                                                            </DisabledReasonButton>
                                                        )}
                                                    </div>
                                                </td>
                                            )}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )
            )}

            {pending.length > 0 && (
                <div className="mt-4 space-y-2">
                    <h3 className="text-sm font-semibold text-text-primary">Not saved</h3>
                    <ul className="space-y-2">
                        {pending.map((item) => (
                            <li
                                key={item.draft.triggerType}
                                className="orch-outline border border-border-default rounded p-3 flex items-start justify-between gap-4"
                            >
                                <div className="min-w-0">
                                    <div className="font-medium text-text-primary">
                                        {item.draft.triggerType}
                                    </div>
                                    <div className="text-sm text-red-700 dark:text-red-400 whitespace-pre-line">
                                        {item.error}
                                    </div>
                                </div>
                                {canSet && (
                                    <button
                                        type="button"
                                        onClick={() => onRetry?.(item)}
                                        aria-label={`Retry trigger ${item.draft.triggerType}`}
                                        className={triggerBtnPrimary}
                                    >
                                        Retry
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default TriggerList;
