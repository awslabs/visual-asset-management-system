/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import { useWorkflow } from "../features/orchestration/api/queries";
import TriggersEditor from "../features/orchestration/workflows/TriggersEditor";

const WorkflowTriggersPage: React.FC = () => {
    const { databaseId, workflowId } = useParams<{
        databaseId: string;
        workflowId: string;
    }>();

    const { data: workflow, isLoading } = useWorkflow(databaseId!, workflowId!);

    if (!databaseId || !workflowId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <p className="text-red-600 dark:text-red-400 text-xl">
                        Missing Database ID or Workflow ID
                    </p>
                </div>
            </div>
        );
    }

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div>Loading...</div>
            </div>
        );
    }

    if (!workflow) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <p className="text-red-600 dark:text-red-400 text-xl">Workflow not found</p>
                </div>
            </div>
        );
    }

    return (
        <TriggersEditor
            databaseId={databaseId}
            workflowId={workflowId}
            pipelineRefs={workflow.specifiedPipelines || []}
        />
    );
};

export default WorkflowTriggersPage;
