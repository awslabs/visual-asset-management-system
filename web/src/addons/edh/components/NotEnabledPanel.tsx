/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/**
 * The notice both EDH pages render when the add-on is not deployed: a deployment fact, in the neutral
 * tone of an unavailable view rather than the warning tone reserved for caveats about data on screen.
 */
const NotEnabledPanel: React.FC = () => (
    <div
        role="status"
        className="p-4 rounded bg-surface-container border border-border-default text-text-primary"
    >
        EDH Workstations is not enabled in this deployment. Ask a VAMS administrator to enable the
        add-on.
    </div>
);

export default NotEnabledPanel;
