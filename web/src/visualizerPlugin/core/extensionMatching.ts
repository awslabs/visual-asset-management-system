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
