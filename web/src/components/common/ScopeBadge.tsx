/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Badge from "@cloudscape-design/components/badge";

interface ScopeBadgeProps {
    databaseId?: string | null;
}

/**
 * Shows a tag/tag-type's scope: "🌐 GLOBAL" when the databaseId is absent or the
 * "GLOBAL" sentinel, otherwise "🏢 {databaseId}".
 */
const ScopeBadge: React.FC<ScopeBadgeProps> = ({ databaseId }) => {
    const isGlobal = !databaseId || databaseId === "GLOBAL";
    return (
        <Badge color={isGlobal ? "blue" : "green"}>
            {isGlobal ? (
                "🌐 GLOBAL"
            ) : (
                <span>
                    🏢 <span>{databaseId}</span>
                </span>
            )}
        </Badge>
    );
};

export default ScopeBadge;
