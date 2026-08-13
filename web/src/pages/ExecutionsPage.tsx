/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useSearchParams } from "react-router-dom";
import ExecutionsBoard from "../features/orchestration/executions/ExecutionsBoard";

const ExecutionsPage: React.FC = () => {
    const [searchParams] = useSearchParams();
    const workflowId = searchParams.get("workflowId");
    const workflowDatabaseId = searchParams.get("workflowDatabaseId");

    const scope =
        workflowId && workflowDatabaseId
            ? { kind: "workflow" as const, databaseId: workflowDatabaseId, workflowId }
            : { kind: "global" as const };

    return <ExecutionsBoard scope={scope} />;
};

export default ExecutionsPage;
