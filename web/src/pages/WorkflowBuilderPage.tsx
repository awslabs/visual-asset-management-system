/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import WorkflowBuilder from "../features/orchestration/workflows/WorkflowBuilder";
import { usePageTitle } from "../hooks/usePageTitle";

const WorkflowBuilderPage: React.FC = () => {
    const { databaseId, workflowId } = useParams<{
        databaseId: string;
        workflowId?: string;
    }>();

    usePageTitle(databaseId, "Workflows", workflowId ? "Edit Workflow" : "Create Workflow");

    if (!databaseId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <p role="alert" className="text-vams-error text-xl">
                        Missing Database ID
                    </p>
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
