/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** A file whose local path is also a folder of another file of the same download. */
export interface FileFolderCollision {
    /** The file listed at a path another file's folder needs (as given, e.g. "/a.txt"). */
    file: string;
    /** A file in the folder of that name (as given, e.g. "/a.txt/b.txt"). */
    other: string;
}

/** The segments a path is written at in a local folder: empty and "." segments name no folder. */
const localSegments = (path: string): string[] =>
    (path || "").split("/").filter((segment) => segment !== "" && segment !== ".");

/**
 * The first file of a download that keeps the asset's paths whose local path is also a folder of
 * another file, or null when there is none.
 *
 * An asset can hold a file "/a.txt" and files under "/a.txt/" (two ordinary keys in Amazon S3), and an
 * asset converted from a single file holds "/model.glb" and "/model.glb/...". No local folder holds a file
 * and a folder of one name, so one of the two cannot be written. Paths are read as the download page
 * writes them: empty segments are dropped, and names are compared exactly. The same rule refuses such a
 * selection in Download as Zip and in `vamscli assets download` (#2070, #2074).
 */
export const fileFolderCollision = (paths: readonly string[]): FileFolderCollision | null => {
    const files = new Map<string, string>();
    for (const path of paths) {
        const local = localSegments(path).join("/");
        if (local && !files.has(local)) {
            files.set(local, path);
        }
    }
    for (const path of paths) {
        const segments = localSegments(path);
        for (let depth = 1; depth < segments.length; depth++) {
            const file = files.get(segments.slice(0, depth).join("/"));
            if (file !== undefined) {
                return { file, other: path };
            }
        }
    }
    return null;
};
