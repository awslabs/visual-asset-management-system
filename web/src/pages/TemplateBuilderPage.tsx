/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import TemplateForm, {
    TemplateFormEditLoader,
} from "../features/orchestration/pipelines/TemplateForm";

/** Full-page create/edit Template wizard. */
const TemplateBuilderPage: React.FC = () => {
    const { databaseId, pipelineId, templateId } = useParams<{
        databaseId: string;
        pipelineId: string;
        templateId?: string;
    }>();

    if (!databaseId || !pipelineId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <p className="text-vams-error text-xl">Missing database or pipeline ID</p>
            </div>
        );
    }

    if (templateId) {
        return (
            <TemplateFormEditLoader
                databaseId={databaseId}
                pipelineId={pipelineId}
                templateId={templateId}
            />
        );
    }

    return <TemplateForm mode="create" databaseId={databaseId} pipelineId={pipelineId} />;
};

export default TemplateBuilderPage;
