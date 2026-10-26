/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers that decide which dependency paths found inside a USD file the Needle USD viewer requests
 * from the asset stream route.
 *
 * A .usdz file is a zip package: the layers, textures and other files it references are stored inside
 * it, and the USD runtime reads them from the package. A reference whose path names a package entry
 * is therefore never a separate file of the asset.
 */

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_DIRECTORY_SIGNATURE = 0x02014b50;
const EOCD_MIN_LENGTH = 22;
const CENTRAL_DIRECTORY_HEADER_LENGTH = 46;
const MAX_ZIP_COMMENT_LENGTH = 0xffff;

/** Normalizes a path written inside a USD file for comparison with a package entry name. */
export const normalizePackagedPath = (path: string): string => {
    let normalized = path.replace(/@/g, "").trim();
    while (normalized.startsWith("./")) {
        normalized = normalized.substring(2);
    }
    return normalized.replace(/\/+/g, "/").replace(/^\//, "");
};

/** Whether the bytes start with a zip local file header ("PK\x03\x04"). */
export const isZipPackage = (content: ArrayBuffer): boolean => {
    if (content.byteLength < 4) return false;
    const head = new Uint8Array(content, 0, 4);
    return head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04;
};

/**
 * Lists the file entries of a zip package from its central directory, normalized with
 * normalizePackagedPath. Returns null when the bytes are not a zip archive the reader understands
 * (no end-of-central-directory record, a Zip64 archive, or an inconsistent directory).
 */
export const listZipEntryNames = (content: ArrayBuffer): Set<string> | null => {
    const length = content.byteLength;
    if (length < EOCD_MIN_LENGTH) return null;
    const view = new DataView(content);

    // The end-of-central-directory record is last, followed only by an optional archive comment.
    const lowest = Math.max(0, length - EOCD_MIN_LENGTH - MAX_ZIP_COMMENT_LENGTH);
    let eocd = -1;
    for (let position = length - EOCD_MIN_LENGTH; position >= lowest; position--) {
        if (view.getUint32(position, true) === EOCD_SIGNATURE) {
            eocd = position;
            break;
        }
    }
    if (eocd < 0) return null;

    const entryCount = view.getUint16(eocd + 10, true);
    const directorySize = view.getUint32(eocd + 12, true);
    const directoryOffset = view.getUint32(eocd + 16, true);
    if (entryCount === 0xffff || directoryOffset === 0xffffffff) return null;
    if (directoryOffset + directorySize > eocd) return null;

    const decoder = new TextDecoder("utf-8");
    const names = new Set<string>();
    let position = directoryOffset;
    for (let index = 0; index < entryCount; index++) {
        if (position + CENTRAL_DIRECTORY_HEADER_LENGTH > eocd) return null;
        if (view.getUint32(position, true) !== CENTRAL_DIRECTORY_SIGNATURE) return null;
        const nameLength = view.getUint16(position + 28, true);
        const extraLength = view.getUint16(position + 30, true);
        const commentLength = view.getUint16(position + 32, true);
        const nameStart = position + CENTRAL_DIRECTORY_HEADER_LENGTH;
        if (nameStart + nameLength > eocd) return null;
        const name = decoder.decode(new Uint8Array(content, nameStart, nameLength));
        if (name.length > 0 && !name.endsWith("/")) {
            names.add(normalizePackagedPath(name));
        }
        position = nameStart + nameLength + extraLength + commentLength;
    }
    return names;
};

/**
 * Whether a dependency path, resolved against the asset root, can name a file of the asset.
 *
 * Rejects paths with a backslash (a Windows path, or free text a binary scan picked up from a
 * comment), a leading drive letter or URI scheme (C:, omniverse:, file:), a ".." segment left after
 * resolution, or a control character. Such a path refers to a location on the machine or server that
 * wrote the USD file, not to a file of the asset, so the viewer does not request it.
 */
export const isRequestableDependencyPath = (path: string): boolean => {
    if (!path) return false;
    if (path.includes("\\")) return false;
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(path)) return false;
    for (let index = 0; index < path.length; index++) {
        const code = path.charCodeAt(index);
        if (code < 0x20 || code === 0x7f) return false;
    }
    return !path.split("/").some((segment) => segment === "..");
};
