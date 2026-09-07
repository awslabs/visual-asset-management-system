/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import { useTemplates, useTemplateMutations, usePipeline } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import Breadcrumb from "../components/Breadcrumb";
import RefreshButton from "../components/RefreshButton";
import SearchInput from "../components/SearchInput";
import { btnPrimary } from "../components/controlStyles";
import { useToast, toastErrorMessage } from "../components/ToastProvider";

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
    const toast = useToast();
    const {
        data: templates = [],
        isLoading,
        error,
        refetch,
        isFetching,
    } = useTemplates(databaseId, pipelineId);
    const { deleteTemplate } = useTemplateMutations();
    const [searchText, setSearchText] = React.useState("");
    // Client-side filter over the loaded templates (a pipeline's template count is small and the API
    // returns them in one page), matching how the other boards' search behaves.
    const visibleTemplates = React.useMemo(() => {
        const needle = searchText.trim().toLowerCase();
        if (!needle) return templates;
        return templates.filter((t: any) =>
            [t.templateId, t.templateName, t.description, t.configFormat]
                .filter(Boolean)
                .some((v: string) => String(v).toLowerCase().includes(needle))
        );
    }, [templates, searchText]);
    const { can } = useAllowedRoutes();
    const { data: pipeline } = usePipeline(databaseId, pipelineId);
    const pipelineLabel = pipelineName || pipeline?.pipelineName || pipelineId;

    const base = `/databases/${databaseId}/pipelines/${pipelineId}/templates`;

    // Notices from a delete response, kept on the board after the toast expires. A trigger that
    // still names a deleted template leaves its workflow permanently failing at template
    // resolution, and a 5-second toast is not a record of that.
    const [deleteWarnings, setDeleteWarnings] = React.useState<string[]>([]);

    // Deleting a template is permanent — the backend also removes its offloaded config bodies and
    // tag schema, so there is no archived copy to restore.
    const handleDelete = async (templateId: string) => {
        if (
            !confirm(
                `Permanently delete template "${templateId}" and its stored config body? ` +
                    "This cannot be undone."
            )
        )
            return;
        try {
            const result: any = await deleteTemplate.mutateAsync({
                databaseId,
                pipelineId,
                templateId,
            });
            const warnings: string[] = Array.isArray(result?.warnings) ? result.warnings : [];
            setDeleteWarnings(warnings);
            if (warnings.length) {
                // The delete succeeded — the warnings name the triggers left pointing at a template
                // that is gone, which is the operator's next task rather than a failure of this one.
                toast.warning("Template deleted with warnings", {
                    description: `${templateId}: ${warnings.join(" ")}`,
                });
            } else {
                toast.success("Template deleted", { description: templateId });
            }
        } catch (err) {
            toast.error("Delete failed", {
                description: `${templateId}: ${toastErrorMessage(err)}`,
            });
        }
    };

    const canCreate = can("POST", "/database/{databaseId}/pipelines/{pipelineId}/templates");
    const canEdit = can(
        "PUT",
        "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}"
    );
    const canDelete = can(
        "DELETE",
        "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}"
    );

    return (
        <div className="orchestration-root orchestration-page space-y-6 bg-surface min-h-full">
            <div className="space-y-2">
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
                    <h1 className="text-text-primary">Templates</h1>
                    {canCreate && (
                        <button onClick={() => navigate(`${base}/create`)} className={btnPrimary}>
                            Create Template
                        </button>
                    )}
                </div>
            </div>

            {/* Search + refresh on one aligned row, matching the Pipelines/Workflows/Executions
                toolbars so the pages read the same way. */}
            <div className="flex items-center gap-2 flex-wrap">
                <SearchInput value={searchText} onChange={(e) => setSearchText(e.target.value)} />
                <RefreshButton onClick={() => refetch()} busy={isFetching} />
            </div>

            {/* Notices carried by a delete response, above the rows they qualify. Rendered only when
                the response supplied them, so the board is unchanged for a clean delete. */}
            {deleteWarnings.length > 0 && (
                <div
                    role="status"
                    className="p-3 rounded bg-yellow-100 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300"
                >
                    {deleteWarnings.map((warning) => (
                        <div key={warning}>{warning}</div>
                    ))}
                </div>
            )}

            {isLoading ? (
                <div className="text-text-primary">Loading templates...</div>
            ) : error ? (
                <div className="text-vams-error">Error loading templates</div>
            ) : (
                <div className="orch-outline border border-border-default rounded-lg bg-surface-container overflow-hidden">
                    {visibleTemplates.length === 0 ? (
                        <p className="text-text-secondary p-4">
                            {templates.length === 0
                                ? "No templates found"
                                : "No templates match the search"}
                        </p>
                    ) : (
                        visibleTemplates.map((template) => (
                            <div
                                key={template.templateId}
                                className="orch-outline border-b border-border-default last:border-0 px-4 py-1.5 flex justify-between items-center gap-4 hover:bg-surface-hover"
                            >
                                <div>
                                    <h3 className="font-bold text-text-primary flex items-center gap-2 leading-snug">
                                        {template.templateName}
                                        {template.isDefault && (
                                            <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                                                Default
                                            </span>
                                        )}
                                    </h3>
                                    {template.description && (
                                        <p className="text-sm text-text-secondary leading-snug">
                                            {template.description}
                                        </p>
                                    )}
                                    <p className="text-xs text-text-secondary leading-snug">
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
                                    {canDelete && (
                                        <button
                                            onClick={() => handleDelete(template.templateId)}
                                            className="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700"
                                        >
                                            Delete
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
