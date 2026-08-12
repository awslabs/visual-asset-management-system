/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import TemplateEditor from "../features/orchestration/pipelines/TemplateEditor";

/** A pipeline's templates list (full page). Create/edit open their own wizard pages. */
const TemplateListPage: React.FC = () => {
    const { databaseId, pipelineId } = useParams<{ databaseId: string; pipelineId: string }>();
    if (!databaseId || !pipelineId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <p className="text-vams-error text-xl">Missing database or pipeline ID</p>
            </div>
        );
    }
    return <TemplateEditor databaseId={databaseId} pipelineId={pipelineId} />;
};

export default TemplateListPage;
