/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect } from "react";
import config from "../config";

const APP_TITLE_PREFIX = config.APP_TITLE_PREFIX;

/**
 * Sets the document title for the current page.
 * Pass title segments that will be joined with " - " after "VAMS".
 * Falsy segments are filtered out (useful for data that hasn't loaded yet).
 *
 * Examples:
 *   usePageTitle("Databases")                    → "VAMS - Databases"
 *   usePageTitle(databaseId, asset?.assetName)    → "VAMS - myDb - My Asset"
 *   usePageTitle()                                → "VAMS"
 */
export function usePageTitle(...parts: (string | undefined | null)[]) {
    const filtered = parts.filter(Boolean) as string[];
    const title =
        filtered.length > 0 ? `${APP_TITLE_PREFIX} - ${filtered.join(" - ")}` : APP_TITLE_PREFIX;

    useEffect(() => {
        document.title = title;
    }, [title]);
}
