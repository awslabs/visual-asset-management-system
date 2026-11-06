/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import EdhAdminPage from "../admin/EdhAdminPage";
import { usePageTitle } from "../../../hooks/usePageTitle";

const EdhAdminRoutePage: React.FC = () => {
    usePageTitle("Workstation Admin");
    return <EdhAdminPage />;
};

export default EdhAdminRoutePage;
