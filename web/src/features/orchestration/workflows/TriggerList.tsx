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

interface TriggerListProps {
    triggers: WorkflowTrigger[];
    isLoading: boolean;
    onAdd: (baseType: string) => void;
    onEdit: (trigger: WorkflowTrigger) => void;
    onDelete: (trigger: WorkflowTrigger) => void;
    /** Triggers that could not be written, shown as "Not saved" rows with a Retry action. */
    pending?: PendingTrigger[];
    onRetry?: (item: PendingTrigger) => void;
}

/**
 * The trigger list panel: one add button per configurable type, the stored (or drafted) rows, and
 * any rows a save could not write. Presentational — the owner decides what add/edit/delete/retry do.
 */
const TriggerList: React.FC<TriggerListProps> = ({
    triggers,
    isLoading,
    onAdd,
    onEdit,
    onDelete,
    pending = [],
    onRetry,
}) => {
    return (
        <div className="orch-outline border border-border-default rounded p-6 bg-surface-container">
            <div className="flex items-start justify-between mb-4">
                <h2 className="text-xl font-semibold text-text-primary">Triggers</h2>
                <div className="flex gap-2">
                    {/* One add button per configurable type, so a type added to TRIGGER_TYPES
                        appears here without touching this component. */}
                    {TRIGGER_TYPES.map((t) => (
                        <button
                            key={t.type}
                            type="button"
                            onClick={() => onAdd(t.type)}
                            title={t.description}
                            className={triggerBtnPrimary}
                        >
                            Add {t.label.toLowerCase()} trigger
                        </button>
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
                    <table className="orch-outline min-w-full border-collapse border border-border-default">
                        <thead className="bg-surface-secondary">
                            <tr>
                                <th className={`${triggerCell} text-left`}>Type</th>
                                <th className={`${triggerCell} text-left`}>Name</th>
                                <th className={`${triggerCell} text-left`}>Fires on</th>
                                <th className={`${triggerCell} text-left`}>Enabled</th>
                                <th className={`${triggerCell} text-left`}>Actions</th>
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
                                        <td className={triggerCell}>
                                            <div className="flex gap-2">
                                                <button
                                                    type="button"
                                                    onClick={() => onEdit(trigger)}
                                                    aria-label={`Edit trigger ${trigger.triggerType}`}
                                                    className={triggerBtnPrimary}
                                                >
                                                    Edit
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => onDelete(trigger)}
                                                    aria-label={`Delete trigger ${trigger.triggerType}`}
                                                    className={triggerBtnDanger}
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
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
                                <button
                                    type="button"
                                    onClick={() => onRetry?.(item)}
                                    aria-label={`Retry trigger ${item.draft.triggerType}`}
                                    className={triggerBtnPrimary}
                                >
                                    Retry
                                </button>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default TriggerList;
