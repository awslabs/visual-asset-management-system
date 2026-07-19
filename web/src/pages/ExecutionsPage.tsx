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
    const databaseId = searchParams.get("databaseId");

    const scope =
        workflowId && databaseId
            ? { kind: "workflow" as const, databaseId, workflowId }
            : { kind: "global" as const };

    return <ExecutionsBoard scope={scope} />;
};

export default ExecutionsPage;
