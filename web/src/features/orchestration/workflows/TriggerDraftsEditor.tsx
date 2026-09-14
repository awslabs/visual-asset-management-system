/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";
import TriggerForm from "./TriggerForm";
import TriggerList from "./TriggerList";
import { draftFrom, draftToTrigger, emptyDraft, validateDraft } from "./triggerDraft";
import type { TriggerDraft } from "./triggerDraft";

interface TriggerDraftsEditorProps {
    drafts: WorkflowTrigger[];
    onChange: (drafts: WorkflowTrigger[]) => void;
    pipelineRefs: SpecifiedPipelineRef[];
}

/**
 * Triggers for a workflow that does not exist yet. Same list and form as the live editor, but every
 * action edits the caller's draft list — nothing is written until the workflow itself is created,
 * which is when the create flow PUTs each draft against the new id. Default templates are kept as
 * chosen; the create flow narrows them to the pipelines still in the workflow at save time, since a
 * pipeline may be removed after a trigger was drafted.
 */
const TriggerDraftsEditor: React.FC<TriggerDraftsEditorProps> = ({
    drafts,
    onChange,
    pipelineRefs,
}) => {
    const [draft, setDraft] = useState<TriggerDraft | null>(null);
    const existingKeys = drafts.map((t) => t.triggerType);

    const handleSave = () => {
        if (!draft) return;
        const { triggerIdInvalid, keyCollides } = validateDraft(draft, existingKeys);
        if (triggerIdInvalid || keyCollides) return;
        const next = draftToTrigger(draft);
        onChange(
            draft.editingKey
                ? drafts.map((t) => (t.triggerType === draft.editingKey ? next : t))
                : [...drafts, next]
        );
        setDraft(null);
    };

    if (draft) {
        return (
            <TriggerForm
                draft={draft}
                onChange={setDraft}
                onSave={handleSave}
                onCancel={() => setDraft(null)}
                isSaving={false}
                saveError={null}
                pipelineRefs={pipelineRefs}
                existingKeys={existingKeys}
            />
        );
    }

    return (
        <div className="space-y-3">
            <p className="text-sm text-text-secondary">
                Triggers are written once the workflow is created. Each one can be changed
                afterwards on the workflow&apos;s Triggers step.
            </p>
            <TriggerList
                triggers={drafts}
                isLoading={false}
                onAdd={(baseType) => setDraft(emptyDraft(baseType))}
                onEdit={(trigger) => setDraft(draftFrom(trigger))}
                onDelete={(trigger) =>
                    onChange(drafts.filter((t) => t.triggerType !== trigger.triggerType))
                }
            />
        </div>
    );
};

export default TriggerDraftsEditor;
