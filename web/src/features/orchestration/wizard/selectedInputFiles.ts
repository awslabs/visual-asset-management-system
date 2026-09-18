/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ExecuteInputFile } from "../types";
import type { ResolvedRestrictions } from "./resolveRestrictions";
import { applyInputFileFilters } from "./ExecuteWizard";

/**
 * Pure helpers over the wizard's input-file selection. A selection can carry hundreds of entries, so
 * everything the list and the review screen derive from it — identity, counts per asset, order,
 * compatibility — is computed here once per selection rather than inside a row.
 */

/** The identity a selection deduplicates on: one asset-relative key of one asset of one database. */
export const inputFileKey = (
    file: Pick<ExecuteInputFile, "databaseId" | "assetId" | "relativeFileKey">
): string => `${file.databaseId}:${file.assetId}:${file.relativeFileKey}`;

export const inputAssetKey = (file: Pick<ExecuteInputFile, "databaseId" | "assetId">): string =>
    `${file.databaseId}:${file.assetId}`;

/** An entry the request can carry: an asset and a file selection are both present. */
export const isCompleteInputFile = (file: ExecuteInputFile): boolean =>
    !!file.assetId && !!file.relativeFileKey;

/** A whole-asset ('/') or folder ('/dir/') key names a container rather than one object. */
export const isContainerKey = (key: string): boolean => key === "/" || key.endsWith("/");

export interface AppendResult {
    files: ExecuteInputFile[];
    /** Entries actually added. */
    added: number;
    /** Entries dropped as duplicates of the selection or of each other. */
    skipped: number;
}

/** `incoming` appended to `existing`, keeping one entry per {@link inputFileKey}. */
export function appendInputFiles(
    existing: ExecuteInputFile[],
    incoming: ExecuteInputFile[]
): AppendResult {
    const seen = new Set(existing.map(inputFileKey));
    const files = [...existing];
    let added = 0;
    let skipped = 0;
    incoming.forEach((file) => {
        const key = inputFileKey(file);
        if (seen.has(key)) {
            skipped += 1;
            return;
        }
        seen.add(key);
        files.push(file);
        added += 1;
    });
    return { files, added, skipped };
}

export interface AssetCount {
    databaseId: string;
    assetId: string;
    count: number;
}

export interface InputFilesSummary {
    /** Complete entries — what the request will carry. */
    total: number;
    assetCount: number;
    /** One row per asset, in first-seen order. */
    perAsset: AssetCount[];
}

export function summarizeInputFiles(files: ExecuteInputFile[]): InputFilesSummary {
    const perAsset = new Map<string, AssetCount>();
    let total = 0;
    (files || []).forEach((file) => {
        if (!isCompleteInputFile(file)) return;
        total += 1;
        const key = inputAssetKey(file);
        const entry = perAsset.get(key);
        if (entry) entry.count += 1;
        else perAsset.set(key, { databaseId: file.databaseId, assetId: file.assetId, count: 1 });
    });
    return { total, assetCount: perAsset.size, perAsset: Array.from(perAsset.values()) };
}

export const pluralize = (count: number, noun: string): string =>
    `${count} ${noun}${count === 1 ? "" : "s"}`;

/** "N files across M assets" — the header line of the list and of the Review card. */
export function describeInputFiles(summary: InputFilesSummary): string {
    return `${pluralize(summary.total, "file")} across ${pluralize(summary.assetCount, "asset")}`;
}

export type InputFileCompatibility = { ok: true } | { ok: false; reason: string };

type CompatibilityRestrictions = Pick<
    ResolvedRestrictions,
    "allow" | "exclude" | "wholeAssetAllowed" | "folderAllowed"
>;

/**
 * Whether one entry passes the resolved chain: the asset-scope gate for a container selection, then
 * the resolved allow/exclude filters. The same rules `validateInputSelection` raises as errors, read
 * per file so the list can badge each row.
 */
export function inputFileCompatibility(
    file: ExecuteInputFile,
    restrictions: CompatibilityRestrictions
): InputFileCompatibility {
    const key = file.relativeFileKey || "";
    if (key === "/" && !restrictions.wholeAssetAllowed) {
        return { ok: false, reason: "Whole-asset selection is not allowed by this workflow." };
    }
    if (key !== "/" && key.endsWith("/") && !restrictions.folderAllowed) {
        return { ok: false, reason: "Folder selection is not allowed by this workflow." };
    }
    const passes = applyInputFileFilters([file], {
        allow: restrictions.allow,
        exclude: restrictions.exclude,
    });
    if (passes.length === 0) {
        return { ok: false, reason: "Fails the resolved input-file filters." };
    }
    return { ok: true };
}

/** One list row: the entry plus its position in the selection, so a row action edits by index. */
export interface InputFileRow {
    file: ExecuteInputFile;
    index: number;
}

/** Rows grouped by asset (first-seen order), keeping the selection's order within an asset. */
export function rowsByAsset(files: ExecuteInputFile[], skip?: Set<number>): InputFileRow[] {
    const groups = new Map<string, InputFileRow[]>();
    (files || []).forEach((file, index) => {
        if (skip?.has(index)) return;
        const key = inputAssetKey(file);
        const group = groups.get(key);
        if (group) group.push({ file, index });
        else groups.set(key, [{ file, index }]);
    });
    const out: InputFileRow[] = [];
    groups.forEach((group) => out.push(...group));
    return out;
}

/** Rows whose key or asset contains `needle` (case-insensitive); every row for an empty needle. */
export function filterRows<R extends InputFileRow>(rows: R[], needle: string): R[] {
    const term = (needle || "").trim().toLowerCase();
    if (!term) return rows;
    return rows.filter(
        ({ file }) =>
            (file.relativeFileKey || "").toLowerCase().includes(term) ||
            inputAssetKey(file).toLowerCase().includes(term)
    );
}

/** Asset-relative key with exactly one leading slash; a trailing slash (a folder) is kept. */
export const normalizeRelativeKey = (raw: string): string => {
    const trimmed = (raw || "").trim();
    if (!trimmed) return "";
    return `/${trimmed.replace(/^\/+/, "")}`;
};

/**
 * One relative key per line, normalized and deduplicated in order. Blank lines are skipped, so a
 * trailing newline or a doubled paste adds nothing.
 */
export function parsePastedKeys(text: string): string[] {
    const seen = new Set<string>();
    const keys: string[] = [];
    (text || "").split(/\r?\n/).forEach((line) => {
        const key = normalizeRelativeKey(line);
        if (!key || seen.has(key)) return;
        seen.add(key);
        keys.push(key);
    });
    return keys;
}

/** The label a row shows for its key: the container forms are spelled out. */
export function describeKey(key: string): string {
    if (key === "/") return "Whole asset (all files)";
    if (key.endsWith("/")) return `${key} (folder)`;
    return key;
}
