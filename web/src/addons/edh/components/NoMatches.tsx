/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/** `No <things> match "<text>".`, for a search whose trimmed text matches none of a list's rows. */
export const noMatchesText = (things: string, searchText: string) =>
    `No ${things} match "${searchText.trim()}".`;

/**
 * The line an EDH list shows when it has rows but its toolbar search or filters match none of them:
 * beneath the table, whose header row stays so a sort survives the search, and in place of the
 * cards. A status, so a screen reader announces it as the search empties the list. A list with no
 * rows at all keeps its own empty state.
 */
const NoMatches: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <div role="status" className="p-8 text-center text-text-secondary">
        {children}
    </div>
);

export default NoMatches;
