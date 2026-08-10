/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    DragEndEvent,
} from "@dnd-kit/core";
import {
    arrayMove,
    SortableContext,
    sortableKeyboardCoordinates,
    useSortable,
    verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { SpecifiedPipelineRef, Pipeline, Template } from "../types";
import { JOB_NAME_PATTERN } from "./workflowValidation";
import InfoTooltip from "../components/InfoTooltip";

/**
 * What the job name actually controls, per workflowAsl.to_asl_pipeline_dict: it becomes the ASL state
 * name AND the `{jobName}` segment of every output path this step writes, defaulting to the pipeline
 * id when left blank. It is validated with the shared id validator, so it takes a literal value only —
 * NOT template tags (see SpecifiedPipelineInput.validate_ids).
 */
const JOB_NAME_HELP = (
    <>
        <p className="mb-1">
            A label for this step. It names the step in the workflow&apos;s state machine and
            becomes a folder in the output path:
        </p>
        <p className="mb-1 font-mono text-[11px] break-all">
            pipelines/&#123;pipeline&#125;/<span className="underline">&#123;jobName&#125;</span>
            /output/&#123;executionId&#125;/files/
        </p>
        <p className="mb-1">
            <strong>Leave this blank unless you need it.</strong> Blank uses the pipeline&apos;s own
            id, which already keeps each step&apos;s output in its own folder.
        </p>
        <p className="mb-1">
            Set it when the pipeline id alone would not identify the step: a general-purpose
            pipeline used here for a narrower purpose (
            <span className="font-mono">convert-for-web</span>), an opaque pipeline id (
            <span className="font-mono">pl-7f3a91</span>), or output that downstream tooling locates
            by S3 prefix and needs a stable, meaningful folder.
        </p>
        <p className="mb-1">
            3–63 characters: letters, numbers, hyphens and underscores only. It is a fixed label,
            not a template —{" "}
            <strong>
                tags such as <span className="font-mono">&#123;&#123;jobName&#125;&#125;</span> are
                not substituted here
            </strong>{" "}
            and are rejected, because this name is written into the state machine when the workflow
            is deployed rather than resolved per run. To vary the output path per run, use the
            workflow&apos;s output path prefix, which does support tags.
        </p>
        <p className="mb-1">
            Note the two are different things: this field is a fixed label, while the{" "}
            <span className="font-mono">&#123;&#123;jobName&#125;&#125;</span> tag — valid in output
            path prefixes and template bodies — resolves to the workflow&apos;s generated job name
            for the run.
        </p>
        <p>
            <strong>This is part of the output path, not a display label.</strong> Changing it on an
            existing workflow changes where subsequent output is written; output already written
            stays put, so the workflow&apos;s history ends up split across the old and new folders.
            Treat a change here as a storage-layout change.
        </p>
    </>
);

export function moveItem<T>(list: T[], from: number, to: number): T[] {
    const newList = [...list];
    const [item] = newList.splice(from, 1);
    newList.splice(to, 0, item);
    return newList;
}

interface PipelineCardProps {
    pipelineRef: SpecifiedPipelineRef;
    index: number;
    pipelineOptions: Pipeline[];
    templateOptions: Template[];
    /** `databaseId:pipelineId` keys already chosen by the OTHER cards. */
    takenPipelineKeys: Set<string>;
    /** Lower-cased job names already used by an EARLIER card. */
    takenJobNames: Set<string>;
    onUpdate: (index: number, updated: SpecifiedPipelineRef) => void;
    onRemove: (index: number) => void;
}

const PipelineCard: React.FC<PipelineCardProps> = ({
    pipelineRef,
    index,
    pipelineOptions,
    templateOptions,
    takenPipelineKeys,
    takenJobNames,
    onUpdate,
    onRemove,
}) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
        id: index.toString(),
    });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
    };

    const selectedPipeline = pipelineOptions.find(
        (p) =>
            p.pipelineId === pipelineRef.pipelineId &&
            p.databaseId === pipelineRef.pipelineDatabaseId
    );

    const handlePipelineChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const compositeKey = e.target.value;
        const [databaseId, pipelineId] = compositeKey.split(":");
        onUpdate(index, {
            ...pipelineRef,
            pipelineId,
            pipelineDatabaseId: databaseId,
            defaultTemplateId: undefined,
        });
    };

    const handleTemplateChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        onUpdate(index, {
            ...pipelineRef,
            defaultTemplateId: e.target.value || undefined,
        });
    };

    const handleJobNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        onUpdate(index, {
            ...pipelineRef,
            jobName: e.target.value || undefined,
        });
    };

    const jobNameInvalid = !!pipelineRef.jobName && !JOB_NAME_PATTERN.test(pipelineRef.jobName);
    // A repeat of an earlier card's job name collapses both steps into one state machine state, so
    // one of the pipelines never runs. Compared case-insensitively: the name is also an output-path
    // segment, where two casings read as the same step.
    const jobNameDuplicate =
        !!pipelineRef.jobName && takenJobNames.has(pipelineRef.jobName.toLowerCase());

    return (
        <div ref={setNodeRef} style={style}>
            <div className="border border-border-default rounded p-4 mb-2 bg-surface-container">
                <div className="flex items-start gap-3">
                    <div
                        {...attributes}
                        {...listeners}
                        className="cursor-grab pt-2 text-text-secondary"
                    >
                        ☰
                    </div>
                    <div className="flex-1 space-y-3">
                        <div>
                            <label
                                htmlFor={`pipeline-${index}`}
                                className="block text-sm font-medium mb-1 text-text-primary"
                            >
                                Pipeline
                            </label>
                            <select
                                id={`pipeline-${index}`}
                                value={
                                    selectedPipeline
                                        ? `${selectedPipeline.databaseId}:${selectedPipeline.pipelineId}`
                                        : ""
                                }
                                onChange={handlePipelineChange}
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            >
                                <option value="">Select a pipeline</option>
                                {/* Archived pipelines are listed so an existing reference to one
                                    still resolves to a named card, but cannot be newly chosen.
                                    A pipeline another card already uses is listed the same way: the
                                    backend keys each step's parameters, resolved config and filtered
                                    inputs by pipeline id, so a second reference to one overwrites
                                    the first and only one of the two steps runs. */}
                                {pipelineOptions.map((p) => {
                                    const key = `${p.databaseId}:${p.pipelineId}`;
                                    const alreadyUsed = takenPipelineKeys.has(key);
                                    return (
                                        <option
                                            key={key}
                                            value={key}
                                            disabled={p.archived === true || alreadyUsed}
                                        >
                                            {p.archived === true
                                                ? `${p.pipelineName} (archived)`
                                                : alreadyUsed
                                                ? `${p.pipelineName} (already in this workflow)`
                                                : p.pipelineName}
                                        </option>
                                    );
                                })}
                            </select>
                        </div>
                        {templateOptions.length > 0 && (
                            <div>
                                <label
                                    htmlFor={`template-${index}`}
                                    className="block text-sm font-medium mb-1 text-text-primary"
                                >
                                    Default Template (optional)
                                </label>
                                <select
                                    id={`template-${index}`}
                                    value={pipelineRef.defaultTemplateId || ""}
                                    onChange={handleTemplateChange}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                >
                                    <option value="">Select a template</option>
                                    {templateOptions.map((t) => (
                                        <option key={t.templateId} value={t.templateId}>
                                            {t.templateName}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}
                        <div>
                            <label
                                htmlFor={`jobName-${index}`}
                                className="block text-sm font-medium mb-1 text-text-primary"
                            >
                                Job Name (optional){" "}
                                <InfoTooltip text={JOB_NAME_HELP} label="About the job name" />
                            </label>
                            {/* The job name becomes a Step Functions state name and an S3
                                output-path segment, so it is limited to the id character set. */}
                            <input
                                id={`jobName-${index}`}
                                type="text"
                                value={pipelineRef.jobName || ""}
                                onChange={handleJobNameChange}
                                placeholder="Enter job name"
                                maxLength={63}
                                pattern="[-_a-zA-Z0-9]{3,63}"
                                title="Letters, numbers, hyphens, and underscores only (3-63)"
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            />
                            {jobNameInvalid && (
                                <p className="mt-1 text-sm text-vams-error">
                                    Letters, numbers, hyphens, and underscores only (3-63).
                                </p>
                            )}
                            {!jobNameInvalid && jobNameDuplicate && (
                                <p className="mt-1 text-sm text-vams-error">
                                    Another step already uses this job name. Each step needs its
                                    own, or the two steps become one and only one pipeline runs.
                                </p>
                            )}
                        </div>
                    </div>
                    {/* A labelled button rather than a bare glyph: a muted "×" with no button chrome
                        reads as decoration, so the only way to undo a mis-added step looked like
                        leaving the wizard and starting over. type="button" keeps it from submitting
                        should this list ever be placed inside a form.

                        Destructive colouring at REST, not only on hover: this is the one control in
                        the list that discards work, and in the secondary text colour it read as
                        another neutral affordance among the step's labels. */}
                    <button
                        type="button"
                        onClick={() => onRemove(index)}
                        aria-label={`Remove step ${index + 1}`}
                        title="Remove this step from the workflow"
                        className="flex shrink-0 items-center gap-1 rounded border border-vams-error/60 px-2 py-1 text-sm text-vams-error hover:border-vams-error hover:bg-vams-error/10 focus:outline-none focus:ring-2 focus:ring-vams-error/40"
                    >
                        <span aria-hidden="true">×</span>
                        Remove
                    </button>
                </div>
            </div>
        </div>
    );
};

interface PipelineOrderListProps {
    value: SpecifiedPipelineRef[];
    pipelineOptions: Pipeline[];
    templatesByPipeline: Record<string, Template[]>;
    onChange: (refs: SpecifiedPipelineRef[]) => void;
}

const PipelineOrderList: React.FC<PipelineOrderListProps> = ({
    value,
    pipelineOptions,
    templatesByPipeline,
    onChange,
}) => {
    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, {
            coordinateGetter: sortableKeyboardCoordinates,
        })
    );

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;

        if (over && active.id !== over.id) {
            const oldIndex = parseInt(active.id.toString());
            const newIndex = parseInt(over.id.toString());

            const newRefs = moveItem(value, oldIndex, newIndex);
            onChange(newRefs);
        }
    };

    const handleUpdate = (index: number, updated: SpecifiedPipelineRef) => {
        const newRefs = [...value];
        newRefs[index] = updated;
        onChange(newRefs);
    };

    const handleRemove = (index: number) => {
        const newRefs = value.filter((_, i) => i !== index);
        onChange(newRefs);
    };

    const handleAdd = () => {
        onChange([...value, { pipelineId: "", pipelineDatabaseId: "" }]);
    };

    if (value.length === 0) {
        return (
            <div className="space-y-4">
                <div className="text-center py-8">
                    <div className="space-y-2">
                        <div className="font-semibold text-text-primary">No pipelines added</div>
                        <div className="text-text-secondary">
                            Add a pipeline to start building your workflow
                        </div>
                        <button
                            onClick={handleAdd}
                            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                        >
                            Add Pipeline
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
            >
                <SortableContext
                    items={value.map((_, i) => i.toString())}
                    strategy={verticalListSortingStrategy}
                >
                    {value.map((pipelineRef, index) => {
                        const compositeKey = `${pipelineRef.pipelineDatabaseId}:${pipelineRef.pipelineId}`;
                        const templateOptions = templatesByPipeline[compositeKey] || [];
                        // Excludes this card's own selection, so its current value stays selectable.
                        const takenPipelineKeys = new Set(
                            value
                                .filter((r, i) => i !== index && r.pipelineId)
                                .map((r) => `${r.pipelineDatabaseId}:${r.pipelineId}`)
                        );
                        // Only EARLIER cards, so the collision is reported on the second one rather
                        // than on both — a name is not wrong until it repeats.
                        const takenJobNames = new Set(
                            value
                                .slice(0, index)
                                .map((r) => (r.jobName || "").toLowerCase())
                                .filter(Boolean)
                        );
                        return (
                            <PipelineCard
                                key={index}
                                pipelineRef={pipelineRef}
                                index={index}
                                pipelineOptions={pipelineOptions}
                                templateOptions={templateOptions}
                                takenPipelineKeys={takenPipelineKeys}
                                takenJobNames={takenJobNames}
                                onUpdate={handleUpdate}
                                onRemove={handleRemove}
                            />
                        );
                    })}
                </SortableContext>
            </DndContext>
            <button
                onClick={handleAdd}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
                Add Pipeline
            </button>
        </div>
    );
};

export default PipelineOrderList;
