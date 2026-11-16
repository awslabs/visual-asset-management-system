/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Shared dependency resolver for the Three.js viewer.
 *
 * Most 3D formats name other files: glTF buffers and images, OBJ material libraries and their
 * textures, COLLADA/3DS/FBX textures, USD references, payloads, sublayers and textures. The
 * resolver turns those names into files of the SAME asset, downloaded through the authenticated
 * stream route, and hands them to the three.js loaders through a `LoadingManager` URL modifier.
 *
 * Path rules (security):
 * - A name resolves relative to the directory of the file that names it, then is normalized.
 * - A name that climbs above the asset root, is absolute (`/x`, `C:\x`, `//host/x`) or carries a
 *   scheme (`http:`, `https:`, `file:`, `omniverse:`, ...) is never fetched. It is recorded as
 *   rejected and the loader receives a 1x1 transparent placeholder image instead.
 * - The URL modifier passes `blob:` and `data:` URLs through and answers every other URL it cannot
 *   map with the placeholder, so no request leaves the viewer for an unresolved dependency.
 *
 * Limits are named constants: once one is reached, resolving stops, everything already downloaded
 * is kept, and the remaining names are recorded so the viewer can say what was not loaded.
 */

import { getDualAuthorizationHeader } from "../../../../utils/authTokenUtils";

const MiB = 1024 * 1024;

/** Upper bounds for the files resolved on behalf of one model. */
export const DEPENDENCY_LIMITS = {
    /** Files downloaded for one model, counting every attempt. */
    maxFiles: 256,
    /** Bytes downloaded for one model, all files together. */
    maxTotalBytes: 512 * MiB,
    /** Bytes for any single referenced file. */
    maxFileBytes: 256 * MiB,
    /** Reference depth: a file referenced by the model is depth 1, a file it references depth 2. */
    maxDepth: 8,
};

export type DependencyLimits = typeof DEPENDENCY_LIMITS;

/** Concurrent downloads per batch. */
export const DEPENDENCY_PARALLEL_LIMIT = 5;

/** A 1x1 fully transparent PNG, returned for every dependency that is not loaded. */
export const PLACEHOLDER_IMAGE_DATA_URL =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=";

/** The placeholder PNG as bytes, for in-memory packages that need a file rather than a URL. */
export function placeholderImageBytes(): Uint8Array {
    const base64 = PLACEHOLDER_IMAGE_DATA_URL.slice(PLACEHOLDER_IMAGE_DATA_URL.indexOf(",") + 1);
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

/** Why a referenced file was not loaded. */
export type UnloadedReason =
    | "not-found"
    | "outside-asset"
    | "external"
    | "udim"
    | "limit"
    | "unsupported"
    | "sublayer";

export interface UnloadedDependency {
    /** The asset-relative key when the name resolved to one, otherwise the name as written. */
    path: string;
    reason: UnloadedReason;
}

export interface ResolvedDependency {
    /** The name as the referencing file wrote it. */
    ref: string;
    /** Normalized key relative to the asset root (no leading asset id segment). */
    key: string;
    bytes: Uint8Array;
}

export interface ResolveResult {
    /** Keyed by the name exactly as passed in. */
    resolved: Map<string, ResolvedDependency>;
    missing: string[];
    rejected: string[];
}

export type NormalizedDependencyPath =
    | { kind: "key"; key: string }
    | { kind: "inline" }
    | { kind: "empty" }
    | { kind: "udim" }
    | { kind: "rejected"; reason: "outside-asset" | "external" };

export interface DependencyResolverContext {
    assetId: string;
    databaseId: string;
    /** API base URL ending with "/" (config.api). */
    apiEndpoint: string;
    /** Logical asset version every dependency is resolved from, when viewing a version. */
    assetVersionId?: string;
    /** Aborts in-flight downloads when the viewer closes. */
    signal?: AbortSignal;
    limits?: Partial<DependencyLimits>;
    /** Defaults to the session's dual authorization header. */
    getAuthHeader?: () => Promise<string>;
}

export interface ResolveOptions {
    /** Depth of the files being resolved (1 for files the model names directly). */
    depth?: number;
    onProgress?: (current: number, total: number) => void;
}

const UDIM_TOKEN = /<udim>/i;
const DRIVE_LETTER = /^[a-z]:\//i;
const URL_SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const INLINE_URL = /^(blob|data):/i;

/**
 * Removes USD asset-path delimiters (`@path@`, `@@@path@@@`), any `<primPath>` that follows a
 * reference, and a `[layer]` suffix naming a layer inside a .usdz package.
 */
export function stripUsdAssetDelimiters(ref: string): string {
    let value = ref.trim();
    const triple = /^@@@([\s\S]*?)@@@/.exec(value);
    if (triple) {
        value = triple[1];
    } else {
        const single = /^@([^@]*)@/.exec(value);
        if (single) {
            value = single[1];
        }
    }
    const packaged = /^(.*?\.usdz)\[.*\]$/i.exec(value);
    if (packaged) {
        value = packaged[1];
    }
    return value.trim();
}

/** Path segments of a file key relative to the asset root, without a leading asset id segment. */
function assetRelativeSegments(fileKey: string, assetId: string): string[] {
    const segments = fileKey.split("/").filter((segment) => segment !== "");
    if (segments.length > 0 && segments[0] === assetId) {
        segments.shift();
    }
    return segments;
}

/**
 * Resolves a dependency name written in `baseFileKey` to a key relative to the asset root, or says
 * why it must not be fetched.
 */
export function normalizeDependencyPath(
    ref: string,
    baseFileKey: string,
    assetId: string
): NormalizedDependencyPath {
    const cleaned = stripUsdAssetDelimiters(ref).replace(/\\/g, "/");
    if (cleaned === "") {
        return { kind: "empty" };
    }
    if (INLINE_URL.test(cleaned)) {
        return { kind: "inline" };
    }
    if (UDIM_TOKEN.test(cleaned)) {
        return { kind: "udim" };
    }
    if (cleaned.startsWith("/") || DRIVE_LETTER.test(cleaned)) {
        return { kind: "rejected", reason: "outside-asset" };
    }
    if (URL_SCHEME.test(cleaned)) {
        return { kind: "rejected", reason: "external" };
    }

    const segments = cleaned.split("/");
    const last = segments[segments.length - 1];
    if (last === "" || last === "." || last === "..") {
        // Names a folder, not a file.
        return { kind: "empty" };
    }

    const resolved = assetRelativeSegments(baseFileKey, assetId);
    resolved.pop(); // the referencing file's own name

    for (const segment of segments) {
        if (segment === "" || segment === ".") {
            continue;
        }
        if (segment === "..") {
            if (resolved.length === 0) {
                return { kind: "rejected", reason: "outside-asset" };
            }
            resolved.pop();
            continue;
        }
        resolved.push(segment);
    }

    if (resolved.length === 0) {
        return { kind: "empty" };
    }
    return { kind: "key", key: resolved.join("/") };
}

/** Stream route URL for an asset-relative key; every path segment is URL-encoded. */
export function buildDependencyUrl(key: string, context: DependencyResolverContext): string {
    const encodedPath = key
        .split("/")
        .map((segment) => encodeURIComponent(segment))
        .join("/");
    let url = `${context.apiEndpoint}database/${context.databaseId}/assets/${encodeURIComponent(
        context.assetId
    )}/download/stream/${encodedPath}`;
    // A dependency is part of the same asset version as the model, so the S3 versionId of the
    // model file is never passed on; the logical asset version is.
    if (context.assetVersionId) {
        url += `?assetVersionId=${encodeURIComponent(context.assetVersionId)}`;
    }
    return url;
}

const IMAGE_MIME_TYPES: Record<string, string> = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    gif: "image/gif",
    webp: "image/webp",
    avif: "image/avif",
    bmp: "image/bmp",
};

function mimeTypeFor(key: string): string {
    const extension = key.toLowerCase().split(".").pop() || "";
    return IMAGE_MIME_TYPES[extension] || "";
}

function isAbortError(error: any): boolean {
    return error?.name === "AbortError";
}

/** Returns a `LoadingManager` whose URL modifier is `modifier`, or undefined without three.js. */
export function createLoadingManager(
    THREE: any,
    modifier: (url: string) => string
): any | undefined {
    if (!THREE?.LoadingManager) {
        return undefined;
    }
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(modifier);
    return manager;
}

/** A URL modifier that only lets `blob:` and `data:` URLs through. */
export function isolatedUrlModifier(url: string): string {
    return INLINE_URL.test(url) ? url : PLACEHOLDER_IMAGE_DATA_URL;
}

export class AssetDependencyResolver {
    private readonly context: DependencyResolverContext;
    private readonly limits: DependencyLimits;
    private readonly downloads = new Map<string, Promise<Uint8Array | null>>();
    private readonly fetched = new Map<string, Uint8Array>();
    private readonly visited = new Set<string>();
    private readonly unloaded = new Map<string, UnloadedDependency>();
    private readonly blobUrls = new Map<string, string>();
    private authHeader: Promise<string> | null = null;
    private attempts = 0;
    private totalBytes = 0;
    private stopped = false;

    constructor(context: DependencyResolverContext) {
        this.context = context;
        this.limits = { ...DEPENDENCY_LIMITS, ...(context.limits || {}) };
    }

    /** Normalizes `ref` as written in `baseFileKey` (see normalizeDependencyPath). */
    normalize(ref: string, baseFileKey: string): NormalizedDependencyPath {
        return normalizeDependencyPath(ref, baseFileKey, this.context.assetId);
    }

    /** Marks a file as visited; false when it already was, which breaks reference cycles. */
    markVisited(key: string): boolean {
        if (this.visited.has(key)) {
            return false;
        }
        this.visited.add(key);
        return true;
    }

    /** Records a referenced file the viewer did not load. The first reason recorded is kept. */
    recordUnloaded(path: string, reason: UnloadedReason): void {
        if (!this.unloaded.has(path)) {
            this.unloaded.set(path, { path, reason });
        }
    }

    /** Every referenced file that was not loaded, in the order first seen. */
    notLoaded(): UnloadedDependency[] {
        return Array.from(this.unloaded.values());
    }

    /** True once a limit stopped resolving. */
    get limitReached(): boolean {
        return this.stopped;
    }

    /** Number of referenced files downloaded. */
    get resolvedCount(): number {
        return this.fetched.size;
    }

    /**
     * Downloads the files `refs` name, resolved relative to `baseFileKey`. Each file is fetched at
     * most once per resolver, whatever spelling names it.
     */
    async resolveDependencies(
        baseFileKey: string,
        refs: string[],
        options: ResolveOptions = {}
    ): Promise<ResolveResult> {
        const depth = options.depth ?? 1;
        const result: ResolveResult = { resolved: new Map(), missing: [], rejected: [] };
        const toFetch: Array<{ ref: string; key: string }> = [];

        for (const ref of Array.from(new Set(refs))) {
            const normalized = this.normalize(ref, baseFileKey);
            switch (normalized.kind) {
                case "key":
                    if (depth > this.limits.maxDepth) {
                        this.stopped = true;
                        this.recordUnloaded(normalized.key, "limit");
                        result.missing.push(ref);
                    } else {
                        toFetch.push({ ref, key: normalized.key });
                    }
                    break;
                case "udim":
                    this.recordUnloaded(stripUsdAssetDelimiters(ref), "udim");
                    result.missing.push(ref);
                    break;
                case "rejected":
                    this.recordUnloaded(stripUsdAssetDelimiters(ref), normalized.reason);
                    result.rejected.push(ref);
                    break;
                default:
                    // Inline (blob:/data:) and empty names need no file.
                    break;
            }
        }

        let done = 0;
        for (let i = 0; i < toFetch.length; i += DEPENDENCY_PARALLEL_LIMIT) {
            const batch = toFetch.slice(i, i + DEPENDENCY_PARALLEL_LIMIT);
            await Promise.all(
                batch.map(async ({ ref, key }) => {
                    const bytes = await this.download(key);
                    if (bytes) {
                        result.resolved.set(ref, { ref, key, bytes });
                    } else {
                        result.missing.push(ref);
                    }
                    done++;
                    options.onProgress?.(done, toFetch.length);
                })
            );
        }

        return result;
    }

    /** Already-downloaded bytes for an asset-relative key. */
    getFetched(key: string): Uint8Array | undefined {
        return this.fetched.get(key);
    }

    /** A blob URL for a resolved file, created once and revoked by revokeAll(). */
    blobUrlFor(dependency: { key: string; bytes: Uint8Array }): string {
        const existing = this.blobUrls.get(dependency.key);
        if (existing) {
            return existing;
        }
        const type = mimeTypeFor(dependency.key);
        const blob = new Blob([dependency.bytes as BlobPart], type ? { type } : undefined);
        const url = URL.createObjectURL(blob);
        this.blobUrls.set(dependency.key, url);
        return url;
    }

    /** Blob URLs this resolver has created and not yet revoked. */
    get createdBlobUrls(): string[] {
        return Array.from(this.blobUrls.values());
    }

    /**
     * URL modifier for a `LoadingManager`: a URL naming a resolved file returns its blob URL,
     * `blob:`/`data:` URLs pass through, and anything else returns the placeholder and is recorded.
     * Names resolve relative to `baseFileKey`.
     */
    createUrlModifier(
        resolved: Map<string, ResolvedDependency> | Iterable<ResolvedDependency>,
        baseFileKey: string
    ): (url: string) => string {
        const dependencies = resolved instanceof Map ? Array.from(resolved.values()) : resolved;
        const byRef = new Map<string, ResolvedDependency>();
        const byKey = new Map<string, ResolvedDependency>();
        for (const dependency of Array.from(dependencies)) {
            byRef.set(dependency.ref, dependency);
            byRef.set(dependency.ref.normalize("NFC"), dependency);
            byKey.set(dependency.key, dependency);
        }

        const lookup = (url: string): ResolvedDependency | undefined => {
            const direct = byRef.get(url);
            if (direct) {
                return direct;
            }
            const normalized = this.normalize(url, baseFileKey);
            if (normalized.kind !== "key") {
                return undefined;
            }
            const mapped = byKey.get(normalized.key);
            if (mapped) {
                return mapped;
            }
            const bytes = this.fetched.get(normalized.key);
            return bytes ? { ref: url, key: normalized.key, bytes } : undefined;
        };

        return (url: string) => {
            if (INLINE_URL.test(url)) {
                return url;
            }
            let dependency = lookup(url);
            if (!dependency) {
                // three.js percent-encodes some names (LoadingManager normalizes to NFC first).
                try {
                    const decoded = decodeURI(url);
                    if (decoded !== url) {
                        dependency = lookup(decoded);
                    }
                } catch {
                    // Not a valid escape sequence: keep the name as written.
                }
            }
            if (dependency) {
                return this.blobUrlFor(dependency);
            }

            const normalized = this.normalize(url, baseFileKey);
            if (normalized.kind === "key") {
                this.recordUnloaded(normalized.key, "not-found");
            } else if (normalized.kind === "rejected") {
                this.recordUnloaded(url, normalized.reason);
            } else if (normalized.kind === "udim") {
                this.recordUnloaded(url, "udim");
            }
            return PLACEHOLDER_IMAGE_DATA_URL;
        };
    }

    /**
     * URL modifier that answers every non-inline URL with the placeholder and appends it to
     * `requests`. Used for a first parse that learns which files a loader asks for.
     */
    createRecordingModifier(requests: string[]): (url: string) => string {
        return (url: string) => {
            if (INLINE_URL.test(url)) {
                return url;
            }
            requests.push(url);
            return PLACEHOLDER_IMAGE_DATA_URL;
        };
    }

    /** Revokes every blob URL this resolver created. Called from viewer cleanup. */
    revokeAll(): void {
        this.blobUrls.forEach((url) => URL.revokeObjectURL(url));
        this.blobUrls.clear();
    }

    private getAuthHeader(): Promise<string> {
        if (!this.authHeader) {
            this.authHeader = this.context.getAuthHeader
                ? this.context.getAuthHeader()
                : getDualAuthorizationHeader();
        }
        return this.authHeader;
    }

    private download(key: string): Promise<Uint8Array | null> {
        const existing = this.downloads.get(key);
        if (existing) {
            return existing;
        }
        const pending = this.fetchOnce(key);
        this.downloads.set(key, pending);
        return pending;
    }

    private async fetchOnce(key: string): Promise<Uint8Array | null> {
        if (this.stopped || this.attempts >= this.limits.maxFiles) {
            this.stopped = true;
            this.recordUnloaded(key, "limit");
            return null;
        }
        this.attempts++;

        let response: Response;
        try {
            response = await fetch(buildDependencyUrl(key, this.context), {
                headers: { Authorization: await this.getAuthHeader() },
                signal: this.context.signal,
            });
        } catch (error) {
            if (isAbortError(error)) {
                throw error;
            }
            this.recordUnloaded(key, "not-found");
            return null;
        }

        if (!response.ok) {
            this.recordUnloaded(key, "not-found");
            return null;
        }

        const declared = Number(response.headers?.get?.("content-length") ?? NaN);
        if (Number.isFinite(declared) && declared > this.limits.maxFileBytes) {
            response.body?.cancel?.().catch(() => undefined);
            this.recordUnloaded(key, "limit");
            return null;
        }

        let bytes: Uint8Array;
        try {
            bytes = new Uint8Array(await response.arrayBuffer());
        } catch (error) {
            if (isAbortError(error)) {
                throw error;
            }
            this.recordUnloaded(key, "not-found");
            return null;
        }

        if (bytes.byteLength > this.limits.maxFileBytes) {
            this.recordUnloaded(key, "limit");
            return null;
        }
        if (this.totalBytes + bytes.byteLength > this.limits.maxTotalBytes) {
            this.stopped = true;
            this.recordUnloaded(key, "limit");
            return null;
        }

        this.totalBytes += bytes.byteLength;
        this.fetched.set(key, bytes);
        return bytes;
    }
}

const REASON_TEXT: Record<UnloadedReason, string> = {
    "not-found": "not found in this asset",
    "outside-asset": "outside this asset",
    external: "external URL, not fetched",
    udim: "UDIM texture sets are not supported",
    limit: "reference limit reached",
    unsupported: "not supported by this viewer",
    sublayer: "not shown by this viewer (USD sublayers)",
};

/** One line per file that was not loaded, e.g. "textures/wood.png (not found in this asset)". */
export function describeUnloaded(list: UnloadedDependency[]): string[] {
    return list.map((item) => `${item.path} (${REASON_TEXT[item.reason]})`);
}
