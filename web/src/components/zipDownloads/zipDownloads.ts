/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../../services/appCache";
import { featuresEnabled, normalizeFeaturesEnabled } from "../../common/constants/featuresEnabled";

/**
 * Download as Zip: shared types, route templates and pure helpers for the create modal, the asset
 * page's "Download Queue" tab and the global "My Download Queue" page. Kept free of React so every rule here (selection
 * shapes, archive names, polling) is checked on its own.
 */

export type ZipStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
export type ZipArchiveFormat = "zip" | "tar" | "tar.gz";
export type ZipCompression = "store" | "deflate";

export interface ZipSelectionEntry {
    type: "file" | "folder" | "asset";
    relativePath: string;
    versionId?: string;
}

export interface ZipDownloadJob {
    requestId: string;
    databaseId: string;
    assetId: string;
    assetName?: string;
    status: ZipStatus;
    archiveFormat: ZipArchiveFormat | string;
    compression: ZipCompression | string;
    archiveFileName: string;
    archiveSizeBytes?: number | null;
    selectionSummary?: { files?: number; folders?: number; wholeAsset?: boolean };
    selectionPreview?: ZipSelectionEntry[];
    assetVersionId?: string | null;
    progress?: { filesDone?: number; filesTotal?: number; bytesDone?: number; bytesTotal?: number };
    errorMessage?: string | null;
    cancelledBy?: string | null;
    createdAt: string;
    startedAt?: string | null;
    completedAt?: string | null;
    expiresAt?: string;
    /** All-users list only: the requester's user id and the API's decisions for the caller. */
    requestedBy?: string;
    isOwn?: boolean;
    canDownload?: boolean;
    canRemove?: boolean;
}

/** API route templates the controls are gated on through useAllowedRoutes().can(). */
export const ZIP_ROUTES = {
    asset: "/database/{databaseId}/assets/{assetId}/download/zip",
    request: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
    url: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url",
    global: "/download/zip",
    allUsers: "/database/{databaseId}/assets/{assetId}/download/zip-all-users",
} as const;

/** Web route of the global "My Download Queue" page (the route keeps its original name). */
export const ZIP_DOWNLOADS_PAGE_PATH = "/zipdownloads";

/**
 * User-facing names of the queue. The queue lists download jobs; archive (zip) jobs are the only
 * kind today, so the names do not say "zip".
 */
export const DOWNLOAD_QUEUE_PAGE_TITLE = "My Download Queue";
export const DOWNLOAD_QUEUE_TAB_LABEL = "Download Queue";

/** The API's 404 body while app.zipDownloads is disabled. */
export const ZIP_DISABLED_MESSAGE = "Zip downloads are not enabled for this deployment";

/** Polling cadence while a non-terminal job is on screen. */
export const ZIP_POLL_INTERVAL_MS = 5000;

/** How many jobs one list call asks for. */
export const ZIP_PAGE_SIZE = 100;

const TERMINAL: ReadonlySet<string> = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export const isTerminalStatus = (status?: string): boolean => !!status && TERMINAL.has(status);

/** Whether this deployment has Download as Zip (the ZIPDOWNLOADS feature switch). */
export function isZipDownloadsEnabled(config: any = appCache.getItem("config")): boolean {
    return normalizeFeaturesEnabled(config?.featuresEnabled).includes(featuresEnabled.ZIPDOWNLOADS);
}

/**
 * The polling delay for a list of jobs, or null to stop: poll only while a job is still queued or
 * running, the surface is the one the user is looking at, and the browser tab is visible.
 */
export function zipRefetchInterval(
    jobs: ZipDownloadJob[],
    active: boolean,
    documentVisible: boolean
): number | null {
    if (!active || !documentVisible) return null;
    return jobs.some((job) => !isTerminalStatus(job.status)) ? ZIP_POLL_INTERVAL_MS : null;
}

/**
 * Merge a freshly fetched first page into the rows on screen: rows on the page replace their old
 * copies, new rows go on top, and rows from later pages stay where they are (newest first).
 */
export function mergeZipJobs(current: ZipDownloadJob[], fresh: ZipDownloadJob[]): ZipDownloadJob[] {
    const freshIds = new Set(fresh.map((job) => job.requestId));
    const rest = current.filter((job) => !freshIds.has(job.requestId));
    return [...fresh, ...rest].sort((a, b) =>
        a.requestId < b.requestId ? 1 : a.requestId > b.requestId ? -1 : 0
    );
}

/**
 * The Download Queue search: the jobs whose asset ID, asset name, request ID, archive name or
 * requester (all-users view) contains the text, ignoring case. It filters the rows already loaded;
 * blank text keeps them all.
 */
export function filterZipJobs(jobs: ZipDownloadJob[], text: string): ZipDownloadJob[] {
    const needle = text.trim().toLowerCase();
    if (!needle) return jobs;
    return jobs.filter((job) =>
        [job.assetId, job.assetName, job.requestId, job.archiveFileName, job.requestedBy].some(
            (value) => !!value && value.toLowerCase().includes(needle)
        )
    );
}

// ---------------------------------------------------------------------------------------------
// Selection: a file-manager selection expressed as the create request's `selection` entries
// ---------------------------------------------------------------------------------------------

export interface ZipSelectableItem {
    relativePath?: string;
    keyPrefix?: string;
    level?: number;
    isFolder?: boolean;
    subTree?: unknown[];
    isArchived?: boolean;
    isPermanentlyDeleted?: boolean;
}

/** Most selection entries one create request may carry (the API's MAX_SELECTION_ENTRIES). */
export const ZIP_MAX_SELECTION_ENTRIES = 1000;

export interface ZipSelectionResult {
    entries: ZipSelectionEntry[];
    files: number;
    folders: number;
    wholeAsset: boolean;
    /**
     * Asset-relative paths of selected items that are archived or permanently deleted. The API refuses
     * the whole job for such an item, so the modal refuses it too and names them.
     */
    refused: string[];
}

const isFolderItem = (item: ZipSelectableItem): boolean =>
    item.isFolder !== undefined
        ? item.isFolder
        : (item.subTree?.length ?? 0) > 0 || !!item.keyPrefix?.endsWith("/");

const isAssetRoot = (item: ZipSelectableItem): boolean =>
    (item.relativePath === "/" || item.relativePath === "") && (item.level ?? 0) === 0;

/** Asset-relative path with exactly one leading slash. */
const toFileKey = (path?: string): string => `/${(path || "").replace(/^\/+/, "")}`;

/** A folder path keeps exactly one trailing slash. */
const toFolderKey = (path?: string): string => `${toFileKey(path).replace(/\/+$/, "")}/`;

/**
 * Selection entries for the items the user picked. The asset root stands for the whole asset and
 * then is the only entry; a folder ends with '/', and an item inside a selected folder is covered by
 * that folder's entry. No per-file version is sent: "Current files" archives what is live now, and an
 * asset-version pin is sent once for the whole request. An archived or deleted item is not sent but
 * listed in `refused`: the whole job is refused for it, as the API refuses it.
 */
export function buildZipSelection(items: ZipSelectableItem[]): ZipSelectionResult {
    const refused: string[] = [];
    const usable: ZipSelectableItem[] = [];
    for (const item of items) {
        if (item.isArchived || item.isPermanentlyDeleted) {
            const path = isFolderItem(item)
                ? toFolderKey(item.relativePath)
                : toFileKey(item.relativePath);
            if (!refused.includes(path)) refused.push(path);
        } else {
            usable.push(item);
        }
    }
    if (usable.some(isAssetRoot)) {
        return {
            entries: [{ type: "asset", relativePath: "/" }],
            files: 0,
            folders: 0,
            wholeAsset: true,
            refused,
        };
    }
    const keyed = new Map<string, boolean>();
    for (const item of usable) {
        const folder = isFolderItem(item);
        const relativePath = folder ? toFolderKey(item.relativePath) : toFileKey(item.relativePath);
        if (relativePath !== "/" && !keyed.has(relativePath)) keyed.set(relativePath, folder);
    }
    const selectedFolders = Array.from(keyed.entries())
        .filter(([, folder]) => folder)
        .map(([path]) => path);
    const coveredByFolder = (path: string): boolean =>
        selectedFolders.some((folder) => folder !== path && path.startsWith(folder));
    const entries: ZipSelectionEntry[] = [];
    let files = 0;
    let folders = 0;
    keyed.forEach((folder, relativePath) => {
        if (coveredByFolder(relativePath)) return;
        entries.push({ type: folder ? "folder" : "file", relativePath });
        if (folder) folders += 1;
        else files += 1;
    });
    return { entries, files, folders, wholeAsset: false, refused };
}

/** "3 files and 1 folder", "the whole asset" style summary of a selection. */
export function describeSelection(
    summary: { files?: number; folders?: number; wholeAsset?: boolean } | undefined,
    wholeAssetLabel: string
): string {
    if (!summary) return "";
    if (summary.wholeAsset) return wholeAssetLabel;
    const parts: string[] = [];
    const files = summary.files ?? 0;
    const folders = summary.folders ?? 0;
    if (files) parts.push(`${files} file${files === 1 ? "" : "s"}`);
    if (folders) parts.push(`${folders} folder${folders === 1 ? "" : "s"}`);
    return parts.join(" and ") || "Nothing selected";
}

// ---------------------------------------------------------------------------------------------
// Archive names
// ---------------------------------------------------------------------------------------------

export const ARCHIVE_EXTENSIONS: Record<ZipArchiveFormat, string> = {
    zip: ".zip",
    tar: ".tar",
    "tar.gz": ".tar.gz",
};

const ALL_EXTENSIONS = [".tar.gz", ".zip", ".tar"];

/** Characters the API keeps in an archive name; anything else becomes '_' (as the API does). */
const sanitizeNamePart = (name: string): string =>
    name
        .replace(/[^A-Za-z0-9 ._()-]/g, "_")
        .replace(/^[ .]+|[ .]+$/g, "")
        .slice(0, 180);

const pad = (n: number): string => `${n}`.padStart(2, "0");

/** Default archive name: `<asset name>-<YYYYMMDD-HHMMSS><ext>` in UTC. */
export function defaultArchiveName(
    assetName: string | undefined,
    format: ZipArchiveFormat,
    now: Date = new Date()
): string {
    const stamp =
        `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}-` +
        `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;
    const base = sanitizeNamePart(assetName || "") || "download";
    return `${base}-${stamp}${ARCHIVE_EXTENSIONS[format]}`;
}

/** Swap the archive extension of a name when the user changes the format. */
export function withArchiveExtension(name: string, format: ZipArchiveFormat): string {
    const lower = name.toLowerCase();
    const current = ALL_EXTENSIONS.find((ext) => lower.endsWith(ext));
    const stem = current ? name.slice(0, name.length - current.length) : name;
    return `${stem}${ARCHIVE_EXTENSIONS[format]}`;
}

/** Why an archive name cannot be sent, or undefined when it is acceptable. */
export function archiveNameError(name: string): string | undefined {
    const trimmed = name.trim();
    if (!trimmed) return undefined; // the API names it
    if (trimmed.length > 200) return "Use at most 200 characters.";
    if (/[/\\]/.test(trimmed)) return "The name cannot contain slashes.";
    // eslint-disable-next-line no-control-regex
    if (/[\u0000-\u001f\u007f]/.test(trimmed)) return "The name cannot contain control characters.";
    return undefined;
}

// ---------------------------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------------------------

/** Percentage done for a running job (bytes when known, else files), or null when unknown. */
export function progressPercent(job: ZipDownloadJob): number | null {
    const p = job.progress || {};
    if (p.bytesTotal && p.bytesTotal > 0) {
        return Math.min(100, Math.floor(((p.bytesDone ?? 0) / p.bytesTotal) * 100));
    }
    if (p.filesTotal && p.filesTotal > 0) {
        return Math.min(100, Math.floor(((p.filesDone ?? 0) / p.filesTotal) * 100));
    }
    return null;
}

/** Human-readable byte size ("1.50 GB"), or "-" when unknown. */
export function formatBytes(size?: number | null): string {
    if (size === undefined || size === null || !Number.isFinite(size) || size < 0) return "-";
    if (size === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
    return `${(size / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

/** A timestamp in the user's locale, or "-". */
export function formatTimestamp(value?: string | null): string {
    if (!value) return "-";
    const at = Date.parse(value);
    return Number.isFinite(at) ? new Date(at).toLocaleString() : value;
}

/** "in 6 days", "in 3 hours", "expired" for the job's expiry. */
export function expiresIn(expiresAt: string | undefined, now: number = Date.now()): string {
    if (!expiresAt) return "-";
    const at = Date.parse(expiresAt);
    if (!Number.isFinite(at)) return "-";
    const ms = at - now;
    if (ms <= 0) return "Expired";
    const hours = Math.floor(ms / 3_600_000);
    if (hours >= 48) return `in ${Math.floor(hours / 24)} days`;
    if (hours >= 1) return `in ${hours} hour${hours === 1 ? "" : "s"}`;
    const minutes = Math.max(1, Math.floor(ms / 60_000));
    return `in ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

export const STATUS_LABELS: Record<ZipStatus, string> = {
    QUEUED: "Queued",
    RUNNING: "Preparing",
    SUCCEEDED: "Ready",
    FAILED: "Failed",
    CANCELLED: "Cancelled",
};

export const STATUS_INDICATOR: Record<
    ZipStatus,
    "pending" | "in-progress" | "success" | "error" | "stopped"
> = {
    QUEUED: "pending",
    RUNNING: "in-progress",
    SUCCEEDED: "success",
    FAILED: "error",
    CANCELLED: "stopped",
};

/** Start a browser download of a presigned URL through a temporary anchor (never logged). */
export function triggerBrowserDownload(url: string, fileName: string): void {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
}
