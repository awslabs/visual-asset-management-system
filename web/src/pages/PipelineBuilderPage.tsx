/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import PipelineForm from "../features/orchestration/pipelines/PipelineForm";
import { usePipeline } from "../features/orchestration/api/queries";

/**
 * Full-page create/edit Pipeline wizard (mirrors the workflow builder page). Create mode renders an
 * empty form; edit mode fetches the pipeline first, then renders the form seeded with it.
 */
const PipelineBuilderPage: React.FC = () => {
    const { databaseId, pipelineId } = useParams<{ databaseId: string; pipelineId?: string }>();
    const navigate = useNavigate();
    const isEdit = !!pipelineId;

    const { data: pipeline, isLoading, isError } = usePipeline(databaseId || "", pipelineId || "");

    if (!databaseId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <p className="text-vams-error text-xl">Missing Database ID</p>
            </div>
        );
    }

    const done = () => navigate(`/databases/${databaseId}/pipelines`);

    if (isEdit && isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400 mb-3" />
                    <p className="text-text-secondary">Loading pipeline…</p>
                </div>
            </div>
        );
    }

    if (isEdit && (isError || !pipeline)) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <p className="text-vams-error text-xl">Pipeline not found</p>
            </div>
        );
    }

    return (
        <PipelineForm
            variant="page"
            mode={isEdit ? "edit" : "create"}
            databaseId={databaseId}
            initial={isEdit ? pipeline : undefined}
            onDone={done}
        />
    );
};

export default PipelineBuilderPage;
