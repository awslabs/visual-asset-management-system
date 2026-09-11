/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import ExecutionDetailPage from "../features/orchestration/executions/ExecutionDetailPage";
import { usePageTitle } from "../hooks/usePageTitle";

const ExecutionDetail: React.FC = () => {
    const { executionId } = useParams<{ executionId: string }>();

    usePageTitle("Execution", executionId);

    if (!executionId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <p role="alert" className="text-vams-error text-xl">
                        Missing Execution ID
                    </p>
                </div>
            </div>
        );
    }

    return <ExecutionDetailPage executionId={executionId} />;
};

export default ExecutionDetail;
