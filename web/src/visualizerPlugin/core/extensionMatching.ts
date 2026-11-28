/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Extension-compatibility rule for viewer plugins.
 *
 * Kept in its own module so it can be unit tested: `PluginRegistry` uses `import.meta.glob` for
 * lazy plugin loading, which Jest cannot parse, so the registry itself is unreachable from a test.
 */

/** The wildcard a viewer declares to accept any extension (the preview viewer). */
export const WILDCARD_EXTENSION = "*";

/**
 * The token a viewer lists in `supportedExtensions` to accept files that have NO extension
 * (README, LICENSE, Makefile, `.gitignore`). It never starts with ".", so no real extension can
 * collide with it, and it rides the same string-set matching as every extension: a mixed selection
 * is admitted only by a viewer that lists both the token and the other extensions. The wildcard
 * matches it too. Never shown to a user as-is — see {@link describeExtensionToken}.
 */
export const NO_EXTENSION = "(none)";

/** The last path segment of a filename or S3 key ("a/b/README" → "README"). */
export function fileBasename(filename: string): string {
    return filename.slice(filename.lastIndexOf("/") + 1);
}

/**
 * The extension of a filename, in its original case, or "" when it has none. The one rule every web
 * helper applies, the same split as Python's `os.path.splitext`:
 *  - only the last path segment is read, so a dot in a folder name is not an extension
 *  - leading dots do not start an extension: `.gitignore` and `.bashrc` have none, `.eslintrc.json`
 *    has `.json`, `.env.example` has `.example`
 *  - a trailing dot leaves no extension (`file.`)
 */
export function filenameExtension(filename?: string): string {
    if (!filename) return "";
    const base = fileBasename(filename);
    let firstNonDot = 0;
    while (firstNonDot < base.length && base[firstNonDot] === ".") firstNonDot++;
    const dot = base.lastIndexOf(".");
    if (dot < firstNonDot || dot === base.length - 1) return "";
    return base.slice(dot);
}

/**
 * The matching token for a file: its lowercase extension (".json"), or {@link NO_EXTENSION} when it
 * has none. Undefined when there is no file name at all (no name, or a key ending in "/", which names
 * a folder), so a folder is never matched as an extensionless file.
 */
export function extensionToken(filename?: string): string | undefined {
    if (!filename || !fileBasename(filename)) return undefined;
    return filenameExtension(filename).toLowerCase() || NO_EXTENSION;
}

/** User-facing form of a matching token: "no extension" for {@link NO_EXTENSION}, else the token. */
export function describeExtensionToken(token: string): string {
    return token === NO_EXTENSION ? "no extension" : token;
}

/**
 * True when the viewer can render EVERY extension in the selection.
 *
 * Every file must match, not merely one of them. Matching on "any" let a viewer through when it
 * supported only part of a mixed selection — a .glb selected alongside a .laz point cloud offered
 * the Three.js viewer, which cannot read the point cloud, so the viewer opened and then failed on a
 * file it was never able to handle. A viewer that covers only part of the set is excluded from it.
 *
 * An empty selection matches nothing: `Array.every` is vacuously true on an empty array, which would
 * otherwise report every viewer as compatible with no files at all.
 */
export function supportsAllExtensions(
    supportedExtensions: string[],
    fileExtensions: string[]
): boolean {
    if (!fileExtensions.length) {
        return false;
    }
    if (supportedExtensions.includes(WILDCARD_EXTENSION)) {
        return true;
    }
    return fileExtensions.every((ext) => supportedExtensions.includes(ext.toLowerCase()));
}
