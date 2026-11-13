/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The one place the web app decides where an uploaded file lands in an asset.
 *
 * The upload control turns whatever the user added (picked files, a picked folder, a drop of files
 * and folders) into UploadRoots, and the pages derive the final asset paths from the whole current
 * selection with planUploadPaths. Pages never build or prefix paths themselves, so a picked folder
 * and a dropped folder always land at the same path.
 *
 * The rule (#1462, #1471):
 * - Create Asset with exactly one folder and nothing else: the folder is the asset, so its own name
 *   is dropped (pack/scene.usda is stored as /scene.usda).
 * - Add Files with exactly one folder: the folder keeps its name under the destination.
 * - Either flow, a single folder: the user can override with "Upload folder contents" (dropName) or
 *   "Upload folder" (keepName).
 * - Every other selection (several folders, folders and loose files): every folder keeps its name,
 *   and the override is ignored.
 * - Loose files keep their file name only.
 */

import {
    referencingFileExtensions,
    referencingFileNames,
} from "../../common/constants/fileFormats";

export interface UploadRootFile {
    /** The browser File. */
    handle: File;
    /** Path inside the folder, without the folder's own name, e.g. props/pallet.usda. */
    path: string;
}

export type UploadRoot =
    | { kind: "folder"; name: string; files: UploadRootFile[] }
    | { kind: "file"; name: string; handle: File };

export type FolderNameMode = "auto" | "keepName" | "dropName";

export type UploadFlow = "createAsset" | "addFiles";

export interface PathContext {
    flow: UploadFlow;
    /** Normalized destination folder: "/" or "/a/b/". */
    destination: string;
    /** Only honoured when the selection is a single folder. */
    folderNameMode: FolderNameMode;
}

export type ExistingState = "exists" | "archived";

export interface PlannedFile {
    /** Stable id within one plan: "<rootIndex>:<fileIndex>". */
    id: string;
    handle: File;
    name: string;
    size: number;
    /** Final asset-relative path, always starting with "/". */
    finalPath: string;
    /** Index of the root (in the selection) this file came from. */
    rootIndex: number;
    /** The folder name, or the file name for a loose file. */
    rootLabel: string;
    errors: string[];
    existing?: ExistingState;
}

export const DUPLICATE_PATH_ERROR = "Duplicate path";
export const INVALID_PATH_ERROR = "Invalid path";

// Characters that never belong in an asset path: control characters and a backslash.
// eslint-disable-next-line no-control-regex
const FORBIDDEN_PATH_CHARS = /[\u0000-\u001f\u007f\\]/;

/** True when the selection is exactly one folder and nothing else. */
export function isSingleFolderSelection(roots: UploadRoot[]): boolean {
    return roots.length === 1 && roots[0].kind === "folder";
}

/** The default override choice for a single folder in this flow. */
export function defaultFolderNameMode(flow: UploadFlow): "keepName" | "dropName" {
    return flow === "createAsset" ? "dropName" : "keepName";
}

/** Whether a folder root keeps its own name as its top-level folder. */
export function folderKeepsName(roots: UploadRoot[], ctx: PathContext): boolean {
    if (!isSingleFolderSelection(roots)) return true;
    const mode =
        ctx.folderNameMode === "auto" ? defaultFolderNameMode(ctx.flow) : ctx.folderNameMode;
    return mode === "keepName";
}

function splitSegments(path: string): string[] {
    return path.split("/").filter((s) => s !== "");
}

function segmentsValid(segments: string[]): boolean {
    return (
        segments.length > 0 &&
        segments.every((s) => s !== "." && s !== ".." && !FORBIDDEN_PATH_CHARS.test(s))
    );
}

/**
 * Normalize a destination folder typed or passed by the user to "/" or "/a/b/".
 * Empty input means the asset root. Relative segments, backslashes and control characters are
 * refused rather than rewritten, so a path never resolves somewhere the user did not type.
 */
export function normalizeDestination(input: string | null | undefined): {
    value: string;
    error?: string;
} {
    const raw = (input ?? "").trim();
    if (FORBIDDEN_PATH_CHARS.test(raw)) {
        return {
            value: "/",
            error: "The folder path cannot contain backslashes or control characters.",
        };
    }
    const segments = splitSegments(raw);
    if (segments.length === 0) return { value: "/" };
    if (segments.some((s) => s === "." || s === "..")) {
        return { value: "/", error: 'The folder path cannot contain "." or ".." segments.' };
    }
    return { value: `/${segments.join("/")}/` };
}

/**
 * Derive the final asset path of every file in the selection.
 * The rule is evaluated over the whole selection each time, never per add.
 * Duplicate final paths are flagged on every affected row; nothing is dropped.
 */
export function planUploadPaths(roots: UploadRoot[], ctx: PathContext): PlannedFile[] {
    const destination = normalizeDestination(ctx.destination).value;
    const keepName = folderKeepsName(roots, ctx);
    const planned: PlannedFile[] = [];

    roots.forEach((root, rootIndex) => {
        if (root.kind === "file") {
            planned.push(planOne(root.handle, [root.name], destination, rootIndex, 0, root.name));
            return;
        }
        root.files.forEach((file, fileIndex) => {
            const inner = splitSegments(file.path);
            const segments = keepName ? [...splitSegments(root.name), ...inner] : inner;
            planned.push(
                planOne(file.handle, segments, destination, rootIndex, fileIndex, root.name)
            );
        });
    });

    const counts = new Map<string, number>();
    for (const p of planned) counts.set(p.finalPath, (counts.get(p.finalPath) ?? 0) + 1);
    for (const p of planned) {
        if ((counts.get(p.finalPath) ?? 0) > 1) p.errors.push(DUPLICATE_PATH_ERROR);
    }
    return planned;
}

function planOne(
    handle: File,
    segments: string[],
    destination: string,
    rootIndex: number,
    fileIndex: number,
    rootLabel: string
): PlannedFile {
    const errors: string[] = [];
    if (!segmentsValid(segments)) errors.push(INVALID_PATH_ERROR);
    const name = segments[segments.length - 1] ?? handle?.name ?? "";
    return {
        id: `${rootIndex}:${fileIndex}`,
        handle,
        name,
        size: handle?.size ?? 0,
        finalPath: destination + segments.join("/"),
        rootIndex,
        rootLabel,
        errors,
    };
}

/** Mark planned files whose final path already exists (or is archived) in the asset. */
export function markExisting(
    planned: PlannedFile[],
    existing: Map<string, ExistingState> | null
): PlannedFile[] {
    if (!existing || existing.size === 0) return planned;
    return planned.map((p) => {
        const state = existing.get(p.finalPath);
        return state ? { ...p, existing: state } : p;
    });
}

/** Loose files (not inside an added folder) in a format that can reference other files. */
export function looseReferencingFiles(roots: UploadRoot[]): string[] {
    return roots
        .filter((r): r is Extract<UploadRoot, { kind: "file" }> => r.kind === "file")
        .map((r) => r.name)
        .filter((name) => {
            const lower = name.toLowerCase();
            if (referencingFileNames.includes(lower)) return true;
            const dot = lower.lastIndexOf(".");
            return dot >= 0 && referencingFileExtensions.includes(lower.substring(dot));
        });
}

// ---------------------------------------------------------------------------------------------
// Root builders: every way a user can add items ends in UploadRoot[], so the paths are identical.
// ---------------------------------------------------------------------------------------------

/**
 * Files from an <input type="file">. With webkitdirectory each File carries webkitRelativePath
 * (pack/props/pallet.usda); its first segment is the folder's name. Files without one are loose.
 */
export function rootsFromInputFiles(files: File[]): UploadRoot[] {
    const roots: UploadRoot[] = [];
    const folders = new Map<string, Extract<UploadRoot, { kind: "folder" }>>();
    for (const file of files) {
        const rel = (file as any).webkitRelativePath as string | undefined;
        const segments = rel ? splitSegments(rel) : [];
        if (segments.length < 2) {
            roots.push({ kind: "file", name: file.name, handle: file });
            continue;
        }
        const [folderName, ...inner] = segments;
        let folder = folders.get(folderName);
        if (!folder) {
            folder = { kind: "folder", name: folderName, files: [] };
            folders.set(folderName, folder);
            roots.push(folder);
        }
        folder.files.push({ handle: file, path: inner.join("/") });
    }
    return roots;
}

/** A folder from showDirectoryPicker (FileSystemDirectoryHandle), walked recursively. */
export async function rootFromDirectoryHandle(dirHandle: any): Promise<UploadRoot> {
    const files: UploadRootFile[] = [];
    const walk = async (handle: any, prefix: string) => {
        for await (const entry of handle.values()) {
            const path = prefix ? `${prefix}/${entry.name}` : entry.name;
            if (entry.kind === "file") {
                files.push({ handle: await entry.getFile(), path });
            } else if (entry.kind === "directory") {
                await walk(entry, path);
            }
        }
    };
    await walk(dirHandle, "");
    return { kind: "folder", name: dirHandle.name, files };
}

const readEntryFile = (fileEntry: any): Promise<File> =>
    new Promise((resolve, reject) => fileEntry.file(resolve, reject));

const readEntriesBatch = (dirReader: any): Promise<any[]> =>
    new Promise((resolve, reject) => dirReader.readEntries(resolve, reject));

/** A dropped folder (FileSystemDirectoryEntry), walked recursively. */
export async function rootFromDirectoryEntry(dirEntry: any): Promise<UploadRoot> {
    const files: UploadRootFile[] = [];
    const walk = async (entry: any, prefix: string) => {
        const reader = entry.createReader();
        // readEntries returns a directory's children in batches; an empty batch ends the listing.
        let batch = await readEntriesBatch(reader);
        while (batch.length > 0) {
            for (const child of batch) {
                const path = prefix ? `${prefix}/${child.name}` : child.name;
                if (child.isFile) {
                    files.push({ handle: await readEntryFile(child), path });
                } else if (child.isDirectory) {
                    await walk(child, path);
                }
            }
            batch = await readEntriesBatch(reader);
        }
    };
    await walk(dirEntry, "");
    return { kind: "folder", name: dirEntry.name, files };
}

/**
 * Roots from a drop. The item list is only readable while the drop event is dispatched, so every
 * entry and loose File is captured before the first await.
 */
export async function rootsFromDataTransferItems(
    items: DataTransferItemList
): Promise<UploadRoot[]> {
    const dropped = Array.from(items)
        .filter((item) => item.kind === "file")
        .map((item) => ({
            entry: (item as any).webkitGetAsEntry?.() ?? null,
            file: item.getAsFile(),
        }));

    const roots: UploadRoot[] = [];
    for (const { entry, file } of dropped) {
        if (entry?.isDirectory) {
            roots.push(await rootFromDirectoryEntry(entry));
        } else if (entry?.isFile) {
            const handle = file ?? (await readEntryFile(entry));
            roots.push({ kind: "file", name: entry.name, handle });
        } else if (!entry && file) {
            // Browsers without webkitGetAsEntry deliver plain Files only.
            roots.push({ kind: "file", name: file.name, handle: file });
        }
    }
    return roots;
}

/**
 * Remove planned files (by PlannedFile.id) from the selection. A folder left without files is
 * dropped, so the rule is evaluated again over what remains.
 */
export function removeFromRoots(roots: UploadRoot[], ids: Iterable<string>): UploadRoot[] {
    const remove = new Set(ids);
    const next: UploadRoot[] = [];
    roots.forEach((root, rootIndex) => {
        if (root.kind === "file") {
            if (!remove.has(`${rootIndex}:0`)) next.push(root);
            return;
        }
        const files = root.files.filter((_, i) => !remove.has(`${rootIndex}:${i}`));
        if (files.length > 0) next.push({ ...root, files });
    });
    return next;
}

/** Number of files a set of roots holds. */
export function countRootFiles(roots: UploadRoot[]): number {
    return roots.reduce((n, r) => n + (r.kind === "file" ? 1 : r.files.length), 0);
}
