/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import WorkflowsPage from "../features/orchestration/workflows/WorkflowsPage";

const WorkflowsPage2: React.FC = () => {
    const { databaseId } = useParams<{ databaseId?: string }>();
    return <WorkflowsPage databaseId={databaseId} />;
};

export default WorkflowsPage2;
