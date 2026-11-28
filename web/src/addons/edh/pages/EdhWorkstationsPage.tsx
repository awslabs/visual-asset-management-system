/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import WorkstationsPage from "../workstations/WorkstationsPage";
import { usePageTitle } from "../../../hooks/usePageTitle";

const EdhWorkstationsPage: React.FC = () => {
    usePageTitle("Workstations");
    return <WorkstationsPage />;
};

export default EdhWorkstationsPage;
