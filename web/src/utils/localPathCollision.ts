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

/** Why a file of a download is not written: another file of it is written at the same local path. */
export const SAME_LOCAL_PATH_REASON =
    "another file of this download is saved at the same local path";

/** Whether an asset-relative path has an empty segment once one leading "/" is read as the marker ("//x"). */
const hasEmptySegment = (path: string): boolean =>
    (path.startsWith("/") ? path : `/${path}`).includes("//");

/**
 * For each file of a download that keeps the asset's paths, in order: null when it is written, else the path
 * of the file written at its local path instead.
 *
 * A local folder cannot have an empty name, so "/x" and "//x" (the key with an empty segment right after the
 * asset's folder, #1977) are both written at "x", and the second write would replace the first. Per local path
 * the file is kept whose path has no empty segment, else the first listed; every other one is not written. The
 * same rule as `vamscli assets download` (`confined_local_paths`), decided before any file is written (#2084).
 */
export const localPathOwners = (paths: readonly string[]): Array<string | null> => {
    const owners = new Map<string, number>();
    paths.forEach((path, index) => {
        const local = localSegments(path).join("/");
        if (!local) return;
        const owner = owners.get(local);
        if (owner === undefined || (hasEmptySegment(paths[owner]) && !hasEmptySegment(path))) {
            owners.set(local, index);
        }
    });
    return paths.map((path, index) => {
        const owner = owners.get(localSegments(path).join("/"));
        return owner === undefined || owner === index ? null : paths[owner];
    });
};
