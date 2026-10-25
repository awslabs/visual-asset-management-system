/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMemo, useState } from "react";

/**
 * The toolbar search of an EDH list: the typed text, its setter, and the rows whose `toText`
 * contains it, case-insensitively and whitespace-trimmed. The count and the empty state stay on
 * the whole list, so callers keep reading `rows` for those.
 */
export function useTextFilter<T>(rows: T[], toText: (row: T) => string) {
    const [searchText, setSearchText] = useState("");
    const visibleRows = useMemo(() => {
        const query = searchText.trim().toLowerCase();
        return query ? rows.filter((row) => toText(row).toLowerCase().includes(query)) : rows;
    }, [rows, searchText, toText]);
    return { searchText, setSearchText, visibleRows };
}
