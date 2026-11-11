/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";

const mediaQueries = (): ((query: string) => MediaQueryList) | null =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? (query) => window.matchMedia(query)
        : null;

/**
 * Whether the viewport is at least `px` wide, following resizes. Where the browser answers no media
 * query (a test environment), the viewport counts as wide.
 *
 * For a page that renders one of two layouts rather than both: a layout hidden with CSS alone still
 * mounts its controls, and a list with a menu per row would mount every menu twice.
 */
export function useMinWidth(px: number): boolean {
    const query = `(min-width: ${px}px)`;
    const [wide, setWide] = useState(() => mediaQueries()?.(query).matches ?? true);
    useEffect(() => {
        const match = mediaQueries();
        if (!match) return undefined;
        const list = match(query);
        const update = () => setWide(list.matches);
        update();
        if (typeof list.addEventListener === "function") {
            list.addEventListener("change", update);
            return () => list.removeEventListener("change", update);
        }
        // Older Safari offers only the deprecated listener form.
        list.addListener(update);
        return () => list.removeListener(update);
    }, [query]);
    return wide;
}
