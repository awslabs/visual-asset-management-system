/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
import { TRIGGER_TYPES, triggerBaseTypeOf } from "../types";

/** The label for a trigger type, falling back to the raw type for one the UI does not know yet. */
export function typeLabel(baseType: string): string {
    return TRIGGER_TYPES.find((t) => t.type === baseType)?.label || baseType;
}

/** What the form holds while a trigger is being created or edited. */
export interface TriggerDraft {
    /** The trigger key being written. Empty triggerId means the first trigger of its type. */
    baseType: string;
    triggerId: string;
    /** The key this draft replaces, or "" when creating. Distinguishes an edit from an add. */
    editingKey: string;
    enabled: boolean;
    allow: string[];
    exclude: string[];
    defaultTemplateIds: Record<string, string>;
}

/** A trigger the workflow create flow could not write, with the server's reason. */
export interface PendingTrigger {
    draft: WorkflowTrigger;
    error: string;
}

export function emptyDraft(baseType: string): TriggerDraft {
    return {
        baseType,
        triggerId: "",
        editingKey: "",
        // Matches the backend trigger default, so a trigger created without touching the box fires.
        enabled: true,
        allow: [],
        exclude: [],
        defaultTemplateIds: {},
    };
}

export function draftFrom(trigger: WorkflowTrigger): TriggerDraft {
    const key = trigger.triggerType || "";
    return {
        baseType: triggerBaseTypeOf(trigger),
        triggerId: trigger.triggerId || key.split("#")[1] || "",
        editingKey: key,
        enabled: trigger.enabled ?? true,
        allow: trigger.inputFileFilters?.allow || [],
        exclude: trigger.inputFileFilters?.exclude || [],
        defaultTemplateIds: trigger.defaultTemplateIds || {},
    };
}

/** The key a draft writes to: the bare type, or "type#triggerId" for an additional trigger. */
export function draftKey(draft: TriggerDraft): string {
    const id = draft.triggerId.trim();
    return id ? `${draft.baseType}#${id}` : draft.baseType;
}

export const TRIGGER_ID_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

/** The client-side checks a draft must pass before it is written or kept. */
export function validateDraft(
    draft: TriggerDraft,
    existingKeys: string[]
): { triggerIdInvalid: boolean; keyCollides: boolean } {
    const id = draft.triggerId.trim();
    const triggerIdInvalid = !!id && !TRIGGER_ID_PATTERN.test(id);
    // A key already in use would REPLACE that trigger rather than add one, so adding under a taken
    // key is refused here instead of silently overwriting a sibling.
    const keyCollides = !draft.editingKey && existingKeys.includes(draftKey(draft));
    return { triggerIdInvalid, keyCollides };
}

/** The flat request shape a draft writes, keyed by the draft's own key. */
export function draftToTrigger(draft: TriggerDraft): WorkflowTrigger {
    return {
        triggerType: draftKey(draft),
        enabled: draft.enabled,
        inputFileFilters: { allow: draft.allow, exclude: draft.exclude },
        defaultTemplateIds: { ...draft.defaultTemplateIds },
    };
}

/**
 * Keeps only the default-template entries for pipelines still in the workflow — a stored entry for a
 * since-removed pipeline is invisible in the form but the backend validates every entry it receives.
 */
export function withCurrentPipelineTemplates(
    trigger: WorkflowTrigger,
    pipelineRefs: SpecifiedPipelineRef[]
): WorkflowTrigger {
    const currentKeys = new Set(
        pipelineRefs.map((item) => `${item.pipelineDatabaseId}:${item.pipelineId}`)
    );
    return {
        ...trigger,
        defaultTemplateIds: Object.fromEntries(
            Object.entries(trigger.defaultTemplateIds || {}).filter(([key]) => currentKeys.has(key))
        ),
    };
}
