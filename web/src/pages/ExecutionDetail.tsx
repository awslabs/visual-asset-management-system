/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import ExecutionDetailPage from "../features/orchestration/executions/ExecutionDetailPage";

const ExecutionDetail: React.FC = () => {
    const { executionId } = useParams<{ executionId: string }>();

    if (!executionId) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-center">
                    <p className="text-red-600 dark:text-red-400 text-xl">Missing Execution ID</p>
                </div>
            </div>
        );
    }

    return <ExecutionDetailPage executionId={executionId} />;
};

export default ExecutionDetail;
