/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import { useWorkflow } from "../features/orchestration/api/queries";
import TriggersEditor from "../features/orchestration/workflows/TriggersEditor";
import { usePageTitle } from "../hooks/usePageTitle";

const WorkflowTriggersPage: React.FC = () => {
    const { databaseId, workflowId } = useParams<{
        databaseId: string;
        workflowId: string;
    }>();

    const { data: workflow, isLoading } = useWorkflow(databaseId!, workflowId!);

    usePageTitle(databaseId, "Workflows", workflow?.workflowName || workflowId, "Triggers");

    if (!databaseId || !workflowId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <p role="alert" className="text-vams-error text-xl">
                        Missing Database ID or Workflow ID
                    </p>
                </div>
            </div>
        );
    }

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                {/* A status region: this state swaps in place for the editor or the error below, and
                    nothing else on the page says which. */}
                <div role="status" className="text-text-secondary">
                    Loading workflow…
                </div>
            </div>
        );
    }

    if (!workflow) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <p role="alert" className="text-vams-error text-xl">
                        Workflow not found
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="orchestration-root px-6 pb-6 pt-4 bg-surface min-h-full">
            <TriggersEditor
                databaseId={databaseId}
                workflowId={workflowId}
                pipelineRefs={workflow.specifiedPipelines || []}
            />
        </div>
    );
};

export default WorkflowTriggersPage;
