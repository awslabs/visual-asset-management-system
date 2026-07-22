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
    onUpdate: (index: number, updated: SpecifiedPipelineRef) => void;
    onRemove: (index: number) => void;
}

const PipelineCard: React.FC<PipelineCardProps> = ({
    pipelineRef,
    index,
    pipelineOptions,
    templateOptions,
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
                                {pipelineOptions.map((p) => (
                                    <option
                                        key={`${p.databaseId}:${p.pipelineId}`}
                                        value={`${p.databaseId}:${p.pipelineId}`}
                                    >
                                        {p.pipelineName}
                                    </option>
                                ))}
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
                                Job Name (optional)
                            </label>
                            <input
                                id={`jobName-${index}`}
                                type="text"
                                value={pipelineRef.jobName || ""}
                                onChange={handleJobNameChange}
                                placeholder="Enter job name"
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                            />
                        </div>
                    </div>
                    <button
                        onClick={() => onRemove(index)}
                        aria-label="Remove pipeline"
                        className="text-text-secondary hover:text-gray-700 dark:hover:text-gray-200"
                    >
                        ×
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
                        return (
                            <PipelineCard
                                key={index}
                                pipelineRef={pipelineRef}
                                index={index}
                                pipelineOptions={pipelineOptions}
                                templateOptions={templateOptions}
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
