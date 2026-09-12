/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Can anything render this?" lookups, shared by every surface that offers a viewer entry point
 * (the file-search table, the asset file manager and the version lists). Kept beside the registry
 * rather than inside one feature so neither feature has to import the other's utilities.
 *
 * Two families of question, one per host surface: the VISUALIZE lookups (`hasViewerForExtensions`,
 * `areFilenamesViewableTogether`) only ever count viewers the visualize path offers — never a
 * compare-only one — and the COMPARE lookups (`areFilesComparableTogether`,
 * `isExtensionComparableAsVersions`) only count compare-capable viewers admitting the selection's
 * count and shape. A surface offers Visualize / Compare exactly when its lookup says so.
 */

import { PluginRegistry } from "./PluginRegistry";
import { CompareContext, CompareEntry, deriveCompareContext } from "./compareShape";
import { ViewerModeAvailability } from "./viewerSelection";

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

// ---------------------------------------------------------------------------------------------
// Compare path
// ---------------------------------------------------------------------------------------------

/** The parts of a compare candidate these lookups read: a name for the extension, plus identity. */
export type CompareCandidate = CompareEntry & { filename?: string };

/** Display name of a candidate: its filename, else the last key segment. */
const candidateName = (file: CompareCandidate): string =>
    file.filename || (file.key || "").split("/").pop() || "";

/** Cache key for a compare question: extension set + the shape the registry gates on. */
const compareKey = (normalized: string[], context: CompareContext): string =>
    `compare|${context.fileCount}|${context.shape}|${context.crossAsset ? "x" : "-"}|${[
        ...new Set(normalized),
    ]
        .sort()
        .join(",")}`;

/**
 * True when at least one compare-capable viewer admits the extensions under this selection shape.
 * Memoized like {@link hasViewerForExtensions}; the shape is part of the key because the same two
 * extensions may be comparable as versions of one file but not as two distinct files.
 */
export function hasCompareViewerForExtensions(
    extensions: string[],
    context: CompareContext
): boolean {
    const normalized = extensions.filter(Boolean).map(normalizeExtension);
    if (!normalized.length) return false;

    const key = compareKey(normalized, context);
    const cached = cache.get(key);
    if (cached !== undefined) return cached;

    const registry = PluginRegistry.getInstance();
    const comparable =
        registry.getCompatibleViewers(normalized, context.fileCount > 1, false, "compare", context)
            .length > 0;

    if (registry.isInitialized?.()) {
        cache.set(key, comparable);
    }
    return comparable;
}

/**
 * True when ONE compare viewer can diff every supplied entry together — the "Compare Selected" gate.
 * Reads the selection's real shape (N versions of one file vs N distinct files, same vs cross asset)
 * so a viewer that only diffs versions is not offered for two different files, and the file-count
 * window (`compareMode.minFiles`/`maxFiles`) is applied by the registry.
 */
export function areFilesComparableTogether(files: CompareCandidate[]): boolean {
    if (files.length === 0) return false;
    const exts = files.map((f) => extensionOfFilename(candidateName(f)));
    if (exts.some((e) => !e)) return false; // an extension-less file has no viewer
    return hasCompareViewerForExtensions(exts as string[], deriveCompareContext(files));
}

/**
 * True when some compare viewer can diff two versions of ONE file with this extension (same asset).
 * This is the gate for a per-row "Compare" action in a version list — the action is offered only for
 * a type a differ handles, and never for a `.png` or `.glb` no compare viewer accepts.
 */
export function isExtensionComparableAsVersions(ext?: string): boolean {
    if (!ext) return false;
    return hasCompareViewerForExtensions([ext], {
        fileCount: 2,
        shape: "same-file-versions",
        crossAsset: false,
    });
}

/**
 * Which modes the file viewer modal may offer for a selection. Not memoized: it is asked once per
 * modal open, not per table cell. Both modes read false until the registry has initialized.
 */
export function availableModesForFiles(files: CompareCandidate[]): ViewerModeAvailability {
    const exts = files.map((f) => extensionOfFilename(candidateName(f)));
    if (files.length === 0 || exts.some((e) => !e)) {
        return { visualize: false, compare: false };
    }
    const normalized = [...new Set((exts as string[]).map(normalizeExtension))];
    return PluginRegistry.getInstance().getAvailableModes(
        normalized,
        files.length > 1,
        deriveCompareContext(files)
    );
}

/** Test seam: drops the memoized answers so a suite can change the registry between assertions. */
export function clearViewableExtensionCache(): void {
    cache.clear();
}
