/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import WorkflowBuilder from "../features/orchestration/workflows/WorkflowBuilder";

const WorkflowBuilderPage: React.FC = () => {
    const { databaseId, workflowId } = useParams<{
        databaseId: string;
        workflowId?: string;
    }>();

    if (!databaseId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <p className="text-red-600 dark:text-red-400 text-xl">Missing Database ID</p>
                </div>
            </div>
        );
    }

    return (
        <WorkflowBuilder
            mode={workflowId ? "edit" : "create"}
            databaseId={databaseId}
            workflowId={workflowId}
        />
    );
};

export default WorkflowBuilderPage;
