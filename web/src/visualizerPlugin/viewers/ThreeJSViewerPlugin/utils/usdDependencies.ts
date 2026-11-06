/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Multi-file USD support for the Three.js viewer.
 *
 * three.js's USDLoader composes references, payloads and textures only from the files inside a
 * .usdz package; for a standalone layer it fetches nothing. So the viewer collects every asset path
 * a layer names, downloads those files from the same asset through the shared resolver, recurses
 * into the layers it finds, and packages the result in memory as a .usdz (root layer first) for
 * USDLoader.parse. Only the public loader API is used.
 *
 * The composer looks a file up by its path joined to the referencing layer's directory, without
 * normalizing `..`, so each file is stored under exactly that key. A missing or rejected image is
 * stored as a transparent placeholder under its key, so the composer never falls back to fetching
 * it by URL.
 */

import {
    AssetDependencyResolver,
    placeholderImageBytes,
    stripUsdAssetDelimiters,
} from "./assetDependencyResolver";

const MiB = 1024 * 1024;

/** USDZ size guard: limits checked on the archive's directory before anything is extracted. */
export const USDZ_LIMITS = {
    /** Uncompressed total may be at most this multiple of the archive size, plus the allowance. */
    expansionFactor: 4,
    expansionAllowanceBytes: 64 * MiB,
    maxEntries: 10000,
};

/** USD layer extensions three.js parses inside a package. */
export const USD_LAYER_EXTENSIONS = ["usd", "usda", "usdc"];

/** Image extensions three.js reads from a USD package. */
export const USD_PACKAGE_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "avif"];

/** Extensions that mark a binary-layer string as an asset path. */
const ASSET_PATH_EXTENSIONS = [
    ...USD_LAYER_EXTENSIONS,
    ...USD_PACKAGE_IMAGE_EXTENSIONS,
    "usdz",
    "exr",
    "hdr",
    "tga",
    "tif",
    "tiff",
    "bmp",
    "webp",
    "gif",
    "ktx2",
    "mtlx",
];

const USDA_ASSET_PATH = /@@@([\s\S]*?)@@@|@([^@\r\n]+)@/g;

function extensionOf(path: string): string {
    const name = path.split("/").pop() || "";
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
    if (bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength) {
        return bytes.buffer as ArrayBuffer;
    }
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

/** True for a ZIP local file header (`PK\x03\x04`), which a .usdz starts with. */
export function isUsdzBytes(bytes: Uint8Array): boolean {
    return (
        bytes.length >= 4 &&
        bytes[0] === 0x50 &&
        bytes[1] === 0x4b &&
        bytes[2] === 0x03 &&
        bytes[3] === 0x04
    );
}

/** True for a binary USD (crate) layer, which starts with `PXR-USDC`. */
export function isUsdcBytes(bytes: Uint8Array): boolean {
    const header = "PXR-USDC";
    if (bytes.length < header.length) {
        return false;
    }
    for (let i = 0; i < header.length; i++) {
        if (bytes[i] !== header.charCodeAt(i)) {
            return false;
        }
    }
    return true;
}

/**
 * Rejects a .usdz whose directory declares more entries or more uncompressed bytes than the
 * USDZ_LIMITS allow. Reads metadata only: the filter returns false, so nothing is inflated.
 */
export function checkUsdzSize(bytes: Uint8Array, unzipSync: (data: Uint8Array, opts: any) => any) {
    let entries = 0;
    let uncompressed = 0;
    try {
        unzipSync(bytes, {
            filter: (file: { originalSize: number }) => {
                entries++;
                uncompressed += file.originalSize || 0;
                return false;
            },
        });
    } catch (error: any) {
        throw new Error(`The USDZ package could not be read: ${error?.message || error}`);
    }

    const maxBytes =
        USDZ_LIMITS.expansionFactor * bytes.byteLength + USDZ_LIMITS.expansionAllowanceBytes;
    if (entries > USDZ_LIMITS.maxEntries) {
        throw new Error(
            `The USDZ package has ${entries} entries, more than the ${USDZ_LIMITS.maxEntries} this viewer opens.`
        );
    }
    if (uncompressed > maxBytes) {
        throw new Error(
            `The USDZ package expands to ${Math.ceil(
                uncompressed / MiB
            )} MiB, more than the ${Math.floor(
                maxBytes / MiB
            )} MiB this viewer allows for a ${Math.ceil(bytes.byteLength / MiB)} MiB file.`
        );
    }
}

/**
 * Collects strings from parsed layer data, skipping numeric arrays (points, normals, ...).
 * Strings under `assetInfo` go to `metadata` instead: its `identifier` names the asset as a
 * whole, often relative to the scene root, and is not a file the layer composes.
 */
function collectStrings(value: any, into: Set<string>, metadata: Set<string>, depth = 0): void {
    if (depth > 32 || value === null || value === undefined) {
        return;
    }
    if (typeof value === "string") {
        into.add(value);
        return;
    }
    if (typeof value !== "object" || ArrayBuffer.isView(value)) {
        return;
    }
    if (Array.isArray(value)) {
        if (value.length > 0 && typeof value[0] === "number") {
            return;
        }
        value.forEach((item) => collectStrings(item, into, metadata, depth + 1));
        return;
    }
    Object.keys(value).forEach((key) =>
        key === "assetInfo"
            ? collectStrings(value[key], metadata, metadata, depth + 1)
            : collectStrings(value[key], into, metadata, depth + 1)
    );
}

/** True when the `@path@` at `index` is the value of an `assetInfo` `identifier`. */
function isAssetIdentifier(text: string, index: number): boolean {
    return /\bidentifier\s*=\s*$/.test(text.slice(Math.max(0, index - 64), index));
}

/**
 * Every asset path a USD layer names: references, payloads, sublayers and asset-valued attributes
 * such as `inputs:file`. ASCII layers are scanned for `@...@` paths; binary layers are parsed with
 * three.js's USDCParser and their strings filtered by file extension.
 */
export function extractUsdAssetPaths(bytes: Uint8Array, USDCParser?: any): string[] {
    const paths = new Set<string>();

    if (isUsdcBytes(bytes)) {
        if (!USDCParser) {
            return [];
        }
        const strings = new Set<string>();
        const metadata = new Set<string>();
        try {
            const parser = new USDCParser();
            const data = parser.parseData(toArrayBuffer(bytes));
            collectStrings(data?.specsByPath, strings, metadata);
            // Asset paths inside reference and payload list ops are stored in the token table,
            // which the parser keeps after decoding.
            if (Array.isArray(parser.tokens)) {
                parser.tokens.forEach((token: any) => collectStrings(token, strings, metadata));
            }
        } catch (error) {
            console.warn("USD: could not read references from a binary layer", error);
            return [];
        }
        strings.forEach((value) => {
            if (metadata.has(value)) {
                return;
            }
            const path = stripUsdAssetDelimiters(value);
            if (path && ASSET_PATH_EXTENSIONS.includes(extensionOf(path))) {
                paths.add(path);
            }
        });
        return Array.from(paths);
    }

    const text = new TextDecoder().decode(bytes);
    let match: RegExpExecArray | null;
    USDA_ASSET_PATH.lastIndex = 0;
    while ((match = USDA_ASSET_PATH.exec(text)) !== null) {
        const path = (match[1] ?? match[2] ?? "").trim();
        if (path && !isAssetIdentifier(text, match.index)) {
            paths.add(path);
        }
    }
    return Array.from(paths);
}

/**
 * The key three.js's USDComposer looks a file up by: the path with a leading `./` removed, joined
 * to the referencing layer's directory without normalization (LoaderUtils.resolveURL).
 */
export function composerKey(path: string, layerKey: string): string {
    const clean = path.startsWith("./") ? path.slice(2) : path;
    const slash = layerKey.lastIndexOf("/");
    const base = slash >= 0 ? layerKey.slice(0, slash) : "";
    if (!base) {
        return clean;
    }
    if (/^(https?:)?\/\//i.test(clean) || /^data:.*,.*$/i.test(clean) || /^blob:/i.test(clean)) {
        return clean;
    }
    return `${base}/${clean}`;
}

export interface UsdFflate {
    unzipSync: (data: Uint8Array, opts?: any) => Record<string, Uint8Array>;
    zipSync: (data: Record<string, Uint8Array>, opts?: any) => Uint8Array;
}

export interface UsdPackageInput {
    /** Asset file key of the root layer or .usdz. */
    rootKey: string;
    rootBytes: Uint8Array;
    resolver: AssetDependencyResolver;
    fflate: UsdFflate;
    USDCParser?: any;
    onProgress?: (current: number, total: number) => void;
}

export interface UsdPackage {
    /** What to hand USDLoader.parse: the original bytes, or a .usdz built in memory. */
    buffer: ArrayBuffer;
    repacked: boolean;
    /** Entry names in package order (root layer first). */
    entries: string[];
}

/**
 * Resolves the files a USD root names and packages them with it as an in-memory .usdz.
 * Returns the original bytes when nothing outside the root was needed.
 */
export async function buildUsdPackage(input: UsdPackageInput): Promise<UsdPackage> {
    const { rootKey, rootBytes, resolver, fflate, USDCParser, onProgress } = input;
    const rootName = rootKey.split("/").pop() || rootKey;

    // Insertion order is package order; the first entry is the root layer.
    const entries = new Map<string, Uint8Array>();
    // Downloaded or packaged bytes by asset-relative key, to reuse under another spelling.
    const byAssetKey = new Map<string, Uint8Array>();
    const layers: Array<{ entry: string; depth: number }> = [];

    if (isUsdzBytes(rootBytes)) {
        checkUsdzSize(rootBytes, fflate.unzipSync);
        const files = fflate.unzipSync(rootBytes);
        Object.keys(files).forEach((name) => entries.set(name, files[name]));
    } else {
        entries.set(rootName, rootBytes);
    }

    entries.forEach((bytes, name) => {
        // Package entries sit where the package does, so they resolve against the root's folder.
        const normalized = resolver.normalize(name, rootKey);
        if (normalized.kind === "key") {
            byAssetKey.set(normalized.key, bytes);
            resolver.markVisited(normalized.key);
        }
        if (USD_LAYER_EXTENSIONS.includes(extensionOf(name))) {
            layers.push({ entry: name, depth: 0 });
        }
    });

    let changed = false;
    const placeholder = placeholderImageBytes();

    for (let i = 0; i < layers.length; i++) {
        const { entry, depth } = layers[i];
        const layerBytes = entries.get(entry);
        if (!layerBytes) {
            continue;
        }
        // The layer's own key relative to the asset root, which a name it writes resolves against.
        const layerKey = resolver.normalize(entry, rootKey);
        if (layerKey.kind !== "key") {
            continue;
        }

        const wanted: string[] = [];
        for (const path of extractUsdAssetPaths(layerBytes, USDCParser)) {
            // The path rules apply to the name as the layer wrote it. Joined to the layer's folder
            // first, an absolute path or a scheme would no longer start the string.
            const written = resolver.normalize(path, layerKey.key);
            if (written.kind === "rejected") {
                resolver.recordUnloaded(stripUsdAssetDelimiters(path), written.reason);
                if (USD_PACKAGE_IMAGE_EXTENSIONS.includes(extensionOf(path))) {
                    entries.set(composerKey(path, entry), placeholder);
                    changed = true;
                }
                continue;
            }
            const key = composerKey(path, entry);
            if (entries.has(key) || wanted.includes(key)) {
                continue;
            }
            const extension = extensionOf(path);
            if (extension === "usdz" || /\.usdz\[/i.test(path)) {
                // three.js composes layers only from the top-level package.
                resolver.recordUnloaded(path, "unsupported");
                continue;
            }
            const normalized = resolver.normalize(key, rootKey);
            const known = normalized.kind === "key" ? byAssetKey.get(normalized.key) : undefined;
            if (known) {
                // An image already packaged under another spelling is stored again under this
                // one. A layer is not: the composer follows references without cycle checks, so
                // a second spelling of a packaged layer is left unresolved.
                if (!USD_LAYER_EXTENSIONS.includes(extension)) {
                    entries.set(key, known);
                    changed = true;
                }
                continue;
            }
            wanted.push(key);
        }

        if (wanted.length === 0) {
            continue;
        }

        const result = await resolver.resolveDependencies(rootKey, wanted, {
            depth: depth + 1,
            onProgress,
        });

        for (const key of wanted) {
            const dependency = result.resolved.get(key);
            if (dependency) {
                entries.set(key, dependency.bytes);
                byAssetKey.set(dependency.key, dependency.bytes);
                changed = true;
                if (
                    USD_LAYER_EXTENSIONS.includes(extensionOf(key)) &&
                    resolver.markVisited(dependency.key)
                ) {
                    layers.push({ entry: key, depth: depth + 1 });
                }
            } else if (USD_PACKAGE_IMAGE_EXTENSIONS.includes(extensionOf(key))) {
                entries.set(key, placeholder);
                changed = true;
            }
        }
    }

    if (!changed) {
        return {
            buffer: toArrayBuffer(rootBytes),
            repacked: false,
            entries: Array.from(entries.keys()),
        };
    }

    const files: Record<string, Uint8Array> = {};
    entries.forEach((bytes, name) => {
        files[name] = bytes;
    });
    // Stored, not deflated: the package only lives in memory, and USDZ entries are stored anyway.
    const zipped = fflate.zipSync(files, { level: 0 });
    return { buffer: toArrayBuffer(zipped), repacked: true, entries: Object.keys(files) };
}
