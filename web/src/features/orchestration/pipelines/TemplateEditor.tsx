/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import { useTemplates, useTemplateMutations, usePipeline } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import Breadcrumb from "../components/Breadcrumb";
import { btnPrimary } from "../components/controlStyles";

interface TemplateEditorProps {
    databaseId: string;
    pipelineId: string;
    /** Optional human-readable pipeline name for the breadcrumb/heading. */
    pipelineName?: string;
}

/**
 * Full-page list of a pipeline's templates. Create/edit open their own wizard pages
 * (TemplateForm) rather than a modal.
 */
const TemplateEditor: React.FC<TemplateEditorProps> = ({
    databaseId,
    pipelineId,
    pipelineName,
}) => {
    const navigate = useNavigate();
    const { data: templates = [], isLoading, error } = useTemplates(databaseId, pipelineId);
    const { archiveTemplate } = useTemplateMutations();
    const { can } = useAllowedRoutes();
    const { data: pipeline } = usePipeline(databaseId, pipelineId);
    const pipelineLabel = pipelineName || pipeline?.pipelineName || pipelineId;

    const base = `/databases/${databaseId}/pipelines/${pipelineId}/templates`;

    const handleArchive = async (templateId: string) => {
        if (!confirm("Are you sure you want to archive this template?")) return;
        try {
            await archiveTemplate.mutateAsync({ databaseId, pipelineId, templateId });
        } catch (err: any) {
            alert(`Failed to archive template: ${err.message}`);
        }
    };

    const canCreate = can("POST", "/database/{databaseId}/pipelines/{pipelineId}/templates");
    const canEdit = can(
        "PUT",
        "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}"
    );
    const canArchive = can(
        "DELETE",
        "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}"
    );

    return (
        <div className="orchestration-root p-6 space-y-6 bg-surface min-h-full">
            <div className="space-y-1">
                <Breadcrumb
                    items={[
                        { label: "Pipelines", to: `/databases/${databaseId}/pipelines` },
                        {
                            label: pipelineLabel,
                            to: `/databases/${databaseId}/pipelines/${pipelineId}`,
                        },
                        { label: "Templates" },
                    ]}
                />
                <div className="flex justify-between items-center">
                    <h1 className="text-2xl font-semibold text-text-primary">Templates</h1>
                    {canCreate && (
                        <button onClick={() => navigate(`${base}/create`)} className={btnPrimary}>
                            Create Template
                        </button>
                    )}
                </div>
            </div>

            {isLoading ? (
                <div className="text-text-primary">Loading templates...</div>
            ) : error ? (
                <div className="text-vams-error">Error loading templates</div>
            ) : (
                <div className="space-y-2">
                    {templates.length === 0 ? (
                        <p className="text-text-secondary">No templates found</p>
                    ) : (
                        templates.map((template) => (
                            <div
                                key={template.templateId}
                                className="border border-border-default rounded-lg bg-surface-container p-4 flex justify-between items-start"
                            >
                                <div>
                                    <h3 className="font-medium text-text-primary flex items-center gap-2">
                                        {template.templateName}
                                        {template.isDefault && (
                                            <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                                                Default
                                            </span>
                                        )}
                                    </h3>
                                    {template.description && (
                                        <p className="text-sm text-text-secondary mt-1">
                                            {template.description}
                                        </p>
                                    )}
                                    <p className="text-xs text-text-secondary mt-1">
                                        Format: {template.configFormat}
                                    </p>
                                </div>
                                <div className="flex gap-2">
                                    {canEdit && (
                                        <button
                                            onClick={() =>
                                                navigate(`${base}/${template.templateId}`)
                                            }
                                            className="px-3 py-1 text-sm bg-gray-600 text-white rounded hover:bg-gray-700"
                                        >
                                            Edit
                                        </button>
                                    )}
                                    {canArchive && (
                                        <button
                                            onClick={() => handleArchive(template.templateId)}
                                            className="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700"
                                        >
                                            Archive
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )}
        </div>
    );
};

export default TemplateEditor;
