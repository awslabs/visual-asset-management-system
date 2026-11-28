/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Pure helpers for the Robotics Data Viewer: which selections it opens, and the deep link that
 * hands their presigned URLs to the framed Lichtblick app (web/customInstalls/robotics/).
 *
 * A presigned URL is a bearer credential, so it travels only in the URL FRAGMENT of the iframe
 * `src`: a browser never sends a fragment, so the URL reaches no access log and no `Referer`
 * (visualizerPlugin/CLAUDE.md, "A Framed Viewer Must Not Receive a Signed URL in the Query
 * String"). The app's VAMS entrypoint moves the fragment into its own query string, where the
 * Lichtblick deep-link parser reads `ds` and every `ds.*` parameter with URLSearchParams.
 */

export const DEFAULT_BASE_PATH = "/viewers/robotics/index.html";

/** The whole-file download limit when `customParameters.maxWholeFileBytes` is unset: 1 GiB. */
export const DEFAULT_MAX_WHOLE_FILE_BYTES = 1024 * 1024 * 1024;

/**
 * Whether a ROS 2 split bag (several .db3 files) opens as one recording. Upstream's db3 reader
 * merges the files of a split bag; the robotics viewer tests open a two-file split bag.
 */
export const MULTI_FILE_DB3 = true;

/** The app's data source for a selection: range-read (`vams-remote`) or downloaded whole. */
export type RoboticsSource = "vams-remote" | "vams-download";

/** Extensions read with HTTP Range requests: MCAP and ROS 1 bag. */
export const RANGE_READ_EXTENSIONS = [".mcap", ".bag"] as const;
/** Extensions downloaded whole: ROS 2 bag (SQLite) and PX4 ULog (`.ulog` is an alias of `.ulg`). */
export const WHOLE_FILE_EXTENSIONS = [".db3", ".ulg", ".ulog"] as const;

/** Lower-case extension of a file key's last path segment, dot included ("" when none). */
export const fileExtension = (key: string): string => {
    const name = key.split("/").filter(Boolean).pop() ?? "";
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot).toLowerCase() : "";
};

/** The file name of a key: its last path segment. */
export const fileNameOf = (key: string): string => key.split("/").filter(Boolean).pop() ?? key;

/** The source that opens files with this extension, or undefined when the viewer does not. */
export const sourceForExtension = (extension: string): RoboticsSource | undefined => {
    const ext = extension.toLowerCase();
    if ((RANGE_READ_EXTENSIONS as readonly string[]).includes(ext)) return "vams-remote";
    if ((WHOLE_FILE_EXTENSIONS as readonly string[]).includes(ext)) return "vams-download";
    return undefined;
};

/** `.ulog` and `.ulg` are one format. */
const formatOf = (extension: string): string => (extension === ".ulog" ? ".ulg" : extension);

export type SelectionCheck =
    | { ok: true; source: RoboticsSource; extension: string }
    | { ok: false; message: string };

/**
 * Decides whether the viewer opens these files together, before any URL is requested. A selection
 * shares one format; a ROS 1 bag and a PX4 ULog open one file at a time, and so does a ROS 2 bag
 * unless MULTI_FILE_DB3 is set.
 */
export const checkSelection = (keys: string[], multiFileDb3 = MULTI_FILE_DB3): SelectionCheck => {
    if (keys.length === 0) {
        return { ok: false, message: "No file was selected." };
    }
    const extensions = keys.map(fileExtension);
    const unsupported = keys.filter((_key, i) => sourceForExtension(extensions[i]) === undefined);
    if (unsupported.length > 0) {
        return {
            ok: false,
            message: `The Robotics Data Viewer does not open ${unsupported
                .map(fileNameOf)
                .join(", ")}. It opens .mcap, .bag, .db3, .ulg and .ulog files.`,
        };
    }
    const formats = new Set(extensions.map(formatOf));
    if (formats.size > 1) {
        return {
            ok: false,
            message:
                "The selected files are of different types. Select files of one type: several " +
                ".mcap files, or several .db3 files of one ROS 2 bag.",
        };
    }
    const extension = extensions[0];
    const format = formatOf(extension);
    if (keys.length > 1) {
        if (format === ".bag") {
            return { ok: false, message: "A ROS 1 bag (.bag) opens one file at a time." };
        }
        if (format === ".ulg") {
            return { ok: false, message: "A PX4 ULog (.ulg, .ulog) opens one file at a time." };
        }
        if (format === ".db3" && !multiFileDb3) {
            return { ok: false, message: "A ROS 2 bag (.db3) opens one file at a time." };
        }
    }
    return { ok: true, source: sourceForExtension(extension) as RoboticsSource, extension };
};

/** `customParameters.maxWholeFileBytes` when it is a positive number, else the 1 GiB default. */
export const maxWholeFileBytes = (customParameters?: Record<string, any>): number => {
    const value = Number(customParameters?.maxWholeFileBytes);
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : DEFAULT_MAX_WHOLE_FILE_BYTES;
};

/** "1 GiB", "1.5 GiB", "600 MiB", "12 B": as the framed app's VAMS overlay writes a size. */
export const formatBytes = (bytes: number): string => {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    const rounded = unit === 0 || value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
    return `${rounded} ${units[unit] ?? "B"}`;
};

const LIMIT_REASON = "limit for files opened by whole download";

/** A selected file's name and, when the file list carries it, its size in bytes. */
export interface SizedFile {
    name: string;
    size?: number;
}

/**
 * The refusal for a whole-file selection whose sizes are all known and together exceed
 * `maxBytes`, or null (within the limit, or a size is unknown). The wording is the framed app's
 * refusal by Content-Length (overlay/vamsSourceSafety.ts), so the user reads the same message
 * whichever side refuses; the app still refuses a selection whose sizes the host does not know.
 */
export const wholeFileOverLimitMessage = (files: SizedFile[], maxBytes: number): string | null => {
    const sizes = files.map((file) => file.size);
    if (
        files.length === 0 ||
        !sizes.every((size) => typeof size === "number" && Number.isFinite(size) && size >= 0)
    ) {
        return null;
    }
    const total = (sizes as number[]).reduce((sum, size) => sum + size, 0);
    if (total <= maxBytes) {
        return null;
    }
    if (files.length === 1) {
        return `${files[0].name || "The file"} is ${formatBytes(
            total
        )}, larger than the ${formatBytes(maxBytes)} ${LIMIT_REASON}.`;
    }
    const listed = files
        .map((file) => `${file.name} ${formatBytes(file.size as number)}`)
        .join(", ");
    return `The selected files are ${formatBytes(
        total
    )} together (${listed}), larger than the ${formatBytes(maxBytes)} ${LIMIT_REASON}.`;
};

/** `customParameters.basePath` when set, else the default hosting path of the built app. */
export const roboticsBasePath = (customParameters?: Record<string, any>): string => {
    const value = customParameters?.basePath;
    return typeof value === "string" && value.length > 0 ? value : DEFAULT_BASE_PATH;
};

/**
 * The iframe `src` that opens `urls` (in order) with `source`. Every value is encoded once with
 * encodeURIComponent and placed after `#`; nothing is added to `basePath`'s query.
 */
export const buildDeepLink = (
    basePath: string,
    source: RoboticsSource,
    urls: string[],
    maxBytes: number = DEFAULT_MAX_WHOLE_FILE_BYTES
): string => {
    const params = [`ds=${encodeURIComponent(source)}`];
    if (source === "vams-download") {
        params.push(`ds.maxBytes=${encodeURIComponent(String(maxBytes))}`);
    }
    for (const url of urls) {
        params.push(`ds.url=${encodeURIComponent(url)}`);
    }
    return `${basePath}#${params.join("&")}`;
};

/**
 * Reads a deep link the way the framed app does: the fragment becomes the query string and is
 * parsed with URLSearchParams. Used by the tests to prove the round trip.
 */
export const parseDeepLink = (
    src: string
): { basePath: string; source: string | null; urls: string[]; maxBytes: string | null } => {
    const hash = src.indexOf("#");
    const basePath = hash === -1 ? src : src.slice(0, hash);
    const params = new URLSearchParams(hash === -1 ? "" : src.slice(hash + 1));
    return {
        basePath,
        source: params.get("ds"),
        urls: params.getAll("ds.url"),
        maxBytes: params.get("ds.maxBytes"),
    };
};
