/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMemo, useState } from "react";

/**
 * The toolbar search of an EDH list: the typed text, its setter, and the rows whose `toText`
 * contains it, case-insensitively and whitespace-trimmed. The empty state stays on the whole list,
 * so callers keep reading `rows` for it; a list with rows of which `visibleRows` keeps none shows
 * `NoMatches` instead, and the count beside the search reads `countText`.
 */
export function useTextFilter<T>(rows: T[], toText: (row: T) => string) {
    const [searchText, setSearchText] = useState("");
    const visibleRows = useMemo(() => {
        const query = searchText.trim().toLowerCase();
        return query ? rows.filter((row) => toText(row).toLowerCase().includes(query)) : rows;
    }, [rows, searchText, toText]);
    return { searchText, setSearchText, visibleRows };
}

/**
 * The count beside an EDH list's search: `<total> <things>`, or `<shown> of <total> <things>` while
 * the search or filters hide some of the rows.
 */
export const countText = (shown: number, total: number, things: string) =>
    shown === total ? `${total} ${things}` : `${shown} of ${total} ${things}`;
