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
import { LONE_FILE_COMPARE_CONTEXT, ViewerModeAvailability } from "./viewerSelection";
import { extensionToken, filenameExtension, NO_EXTENSION } from "./extensionMatching";

/** Search rows carry "ply"; the registry stores ".ply". The no-extension token passes unchanged. */
function normalizeExtension(ext: string): string {
    if (ext === NO_EXTENSION) return ext;
    const lower = ext.toLowerCase();
    return lower.startsWith(".") ? lower : `.${lower}`;
}

/** Matching tokens for a list of names, or undefined when any entry names no file at all. */
function tokensOfFilenames(filenames: Array<string | undefined>): string[] | undefined {
    const tokens = filenames.map((name) => extensionToken(name));
    return tokens.some((matchToken) => !matchToken) ? undefined : (tokens as string[]);
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

/**
 * True if ANY enabled visualize viewer can render this one file, judged by its name: a file with no
 * extension (README, `.gitignore`) is matched through the no-extension token, so it is viewable when
 * a viewer lists that token. A key ending in "/" (a folder) is never viewable.
 */
export function isViewableFilename(filename?: string): boolean {
    const matchToken = extensionToken(filename);
    if (!matchToken) return false;
    return hasViewerForExtensions([matchToken], false);
}

/**
 * True if one viewer can render EVERY supplied filename together (multi-file selection). An
 * extensionless file contributes the no-extension token, so a mixed selection is admitted only by a
 * viewer that lists both that token and the other extensions.
 */
export function areFilenamesViewableTogether(filenames: string[]): boolean {
    if (!filenames.length) return false;
    const tokens = tokensOfFilenames(filenames);
    if (!tokens) return false;
    return hasViewerForExtensions(tokens, filenames.length > 1);
}

/**
 * Extension of a filename in its original case, or undefined when it has none. For DISPLAY and
 * format dispatch only: a viewer gate asks the filename-based lookups (`isViewableFilename`,
 * `isFilenameComparableAsVersions`, `areFilenamesViewableTogether`), which also match a file with no
 * extension. Same dotfile rule as the registry (`filenameExtension`).
 */
export function extensionOfFilename(filename?: string): string | undefined {
    return filenameExtension(filename) || undefined;
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
    const tokens = tokensOfFilenames(files.map(candidateName));
    if (!tokens) return false;
    return hasCompareViewerForExtensions(tokens, deriveCompareContext(files));
}

/**
 * True when some compare viewer can diff two versions of ONE file with this extension (same asset).
 * This is the gate for a per-row "Compare" action in a version list — the action is offered only for
 * a type a differ handles, and never for a `.png` or `.glb` no compare viewer accepts. It is the same
 * question `availableModesForFiles` asks for a lone file, so a version row's Compare and the
 * single-file modal's toggle are always offered together.
 */
export function isExtensionComparableAsVersions(ext?: string): boolean {
    if (!ext) return false;
    return hasCompareViewerForExtensions([ext], LONE_FILE_COMPARE_CONTEXT);
}

/**
 * {@link isExtensionComparableAsVersions} judged by the file's name, so a version row for a file with
 * no extension (README) gets Compare when a differ lists the no-extension token. The gate every
 * version list uses.
 */
export function isFilenameComparableAsVersions(filename?: string): boolean {
    const matchToken = extensionToken(filename);
    if (!matchToken) return false;
    return isExtensionComparableAsVersions(matchToken);
}

/**
 * Which modes the file viewer modal may offer for a selection. Not memoized: it is asked once per
 * modal open, not per table cell. Both modes read false until the registry has initialized.
 *
 * A LONE file reports `compare` when a differ diffs two versions of its type (the registry judges it
 * by `LONE_FILE_COMPARE_CONTEXT`): the host then seeds the pair with `seedCompareFromSingleFile`.
 * A lone `.png`/`.glb` gets Visualize only — this is compare-viewer admission, not an extension list.
 */
export function availableModesForFiles(files: CompareCandidate[]): ViewerModeAvailability {
    const tokens = files.length ? tokensOfFilenames(files.map(candidateName)) : undefined;
    if (!tokens) {
        return { visualize: false, compare: false };
    }
    const normalized = [...new Set(tokens.map(normalizeExtension))];
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
