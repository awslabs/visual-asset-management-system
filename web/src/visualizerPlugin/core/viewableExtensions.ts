/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Can anything render this?" lookups, shared by every surface that offers a viewer entry point
 * (the file-search table and the asset file manager). Kept beside the registry rather than inside
 * one feature so neither feature has to import the other's utilities.
 */

import { PluginRegistry } from "./PluginRegistry";

/** Search rows carry "ply"; the registry stores ".ply". */
function normalizeExtension(ext: string): string {
    const lower = ext.toLowerCase();
    return lower.startsWith(".") ? lower : `.${lower}`;
}

/**
 * Answers are memoized per extension set, not recomputed per rendered row.
 * `getCompatibleViewers` walks, filters and SORTS every registered viewer on each call, and these
 * lookups run once per table cell — a page of results otherwise repeated that identical work for
 * every row on every render. The registered plugin set is fixed once `initialize()` has run, so an
 * answer cannot change afterwards.
 *
 * Only populated once the registry reports itself initialized: before that `getCompatibleViewers`
 * returns an empty list, and caching that would hide the entry point permanently.
 */
const cache = new Map<string, boolean>();

/** True when at least one enabled viewer can render the whole set of extensions. */
export function hasViewerForExtensions(extensions: string[], isMultiFile: boolean): boolean {
    const normalized = extensions.filter(Boolean).map(normalizeExtension);
    if (!normalized.length) return false;

    // Order must not change the answer, so the key is sorted.
    const key = `${isMultiFile ? "multi" : "single"}|${[...new Set(normalized)].sort().join(",")}`;
    const cached = cache.get(key);
    if (cached !== undefined) return cached;

    const registry = PluginRegistry.getInstance();
    const viewable = registry.getCompatibleViewers(normalized, isMultiFile, false).length > 0;

    if (registry.isInitialized?.()) {
        cache.set(key, viewable);
    }
    return viewable;
}

/** True if ANY enabled plugin can render this single extension. */
export function isViewableExtension(ext?: string): boolean {
    if (!ext) return false;
    return hasViewerForExtensions([ext], false);
}

/** True if one viewer can render EVERY supplied filename together (multi-file selection). */
export function areFilenamesViewableTogether(filenames: string[]): boolean {
    const exts = filenames
        .map((n) => (n || "").slice((n || "").lastIndexOf(".")))
        .filter((e) => e && e !== "." && e.includes("."));
    if (exts.length !== filenames.length) return false; // an extension-less file has no viewer
    return hasViewerForExtensions(exts, filenames.length > 1);
}

/** Extension of a filename, or undefined when it has none. */
export function extensionOfFilename(filename?: string): string | undefined {
    if (!filename) return undefined;
    const dot = filename.lastIndexOf(".");
    if (dot <= 0 || dot === filename.length - 1) return undefined;
    return filename.slice(dot);
}

/** Test seam: drops the memoized answers so a suite can change the registry between assertions. */
export function clearViewableExtensionCache(): void {
    cache.clear();
}
