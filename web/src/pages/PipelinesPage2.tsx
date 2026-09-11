/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router-dom";
import PipelinesPage from "../features/orchestration/pipelines/PipelinesPage";
import { usePageTitle } from "../hooks/usePageTitle";

const PipelinesPage2: React.FC = () => {
    const { databaseId } = useParams<{ databaseId?: string }>();
    usePageTitle(databaseId, "Pipelines");
    return <PipelinesPage databaseId={databaseId} />;
};

export default PipelinesPage2;
