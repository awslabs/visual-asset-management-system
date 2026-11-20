/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    RasterDownloadRefused,
    RasterFileRef,
    keepFresh,
    requestPresignedUrl,
} from "./presignedUrl";
import { MAX_RASTER_OVERLAYS } from "./rasterEligibility";
import { FileIdentity, PreparedRaster } from "./rasterResolver";

/** The part of a MapLibre map the manager uses (kept narrow so tests can supply a stand-in). */
export interface MapLike {
    addSource(id: string, source: any): unknown;
    removeSource(id: string): unknown;
    getSource(id: string): any;
    addLayer(layer: any, beforeId?: string): unknown;
    removeLayer(id: string): unknown;
    getLayer(id: string): any;
    moveLayer(id: string, beforeId?: string): unknown;
    setPaintProperty(layerId: string, name: string, value: unknown): unknown;
    getStyle(): { layers?: Array<{ id: string }> } | undefined;
    fitBounds(bounds: [[number, number], [number, number]], options?: any): unknown;
    on(type: string, listener: (event: any) => void): unknown;
    off(type: string, listener: (event: any) => void): unknown;
}

/** What the layer list shows for one raster. */
export interface RasterLayerEntry {
    id: string;
    label: string;
    mode: "tile" | "placed";
    opacity: number;
    approximate: boolean;
    approximateNote: string | null;
    usedCog: boolean;
    staleCog: boolean;
    sourceKey: string;
    file: RasterFileRef;
    /** Why the raster could not be drawn, once it has failed; null while it draws. */
    failed: string | null;
    /** False once the API refused the file: a retry would be refused again, so none is offered. */
    retryable: boolean;
    /** The raster's extent is known, so "Zoom to layer" (and framing on open) can act. */
    framable: boolean;
    /**
     * False while the layer is hidden: it stays in the list with its opacity and place, but has no map
     * layer or source (so no tiles are fetched) until it is shown again.
     */
    visible: boolean;
}

export const TILE_READ_FAILED_MESSAGE =
    "This raster could not be drawn: its map tiles could not be read or decoded. The file may be damaged, or not a Web Mercator COG.";

export const REFRESH_GAVE_UP_MESSAGE =
    "The download link for this raster could not be renewed. Remove it and open the file again.";

export type AddResult = { ok: true } | { ok: false; message: string };

interface Internal {
    entry: RasterLayerEntry;
    sourceId: string;
    layerId: string;
    url: string;
    blobUrl?: string;
    corners?: [number, number][];
    bounds: [[number, number], [number, number]] | null;
    stopRefresh?: () => void;
    refreshNow?: () => Promise<void>;
    /** A refresh asked for after a read error is in flight. */
    errorRefreshInFlight: boolean;
    /** When the last error-triggered refresh put a fresh URL on the map. */
    errorRefreshDoneAt: number;
    errorRefreshes: number;
    /** The one automatic retry after a refreshed URL still failed is waiting or done. */
    autoRetried: boolean;
    autoRetryTimer?: ReturnType<typeof setTimeout>;
    /** A fresh link is being fetched to show a hidden tile layer. */
    showing?: boolean;
}

export interface ManagerOptions {
    /** Id of the first layer the rasters must sit below (footprints, points); none puts them on top. */
    beforeLayerId?: () => string | undefined;
    onChange?: (entries: RasterLayerEntry[]) => void;
    maxOverlays?: number;
    /** Replaced in tests. */
    requestUrl?: (file: RasterFileRef) => Promise<string>;
}

const ID_PREFIX = "vams-raster";
/**
 * A read error this soon after a fresh URL was put on the map means the URL was not the problem: the
 * file cannot be read or decoded, and the layer is marked failed instead of refreshed again.
 */
const TILE_FAIL_WINDOW_MS = 30 * 1000;
/** Error-triggered refreshes allowed over a layer's life before it is marked failed. */
const MAX_ERROR_REFRESHES = 3;
/**
 * Wait before the single automatic retry: a read refused right after a fresh URL went on the map is
 * most often a short S3 or network failure, which usually clears within seconds.
 */
export const AUTO_RETRY_DELAY_MS = 4 * 1000;

/** What the API said about a file (its asset-relative path and latest version), when it was asked. */
export type IdentityLookup = (file: RasterFileRef) => FileIdentity | undefined;

const stripSlashes = (path: string) => path.replace(/^\/+/, "");
const fileName = (file: RasterFileRef) => {
    const parts = file.key.split("/");
    return parts[parts.length - 1];
};

/**
 * The same file, whichever way it was named. The path is the asset-relative path the API returned
 * when it is known (a file manager entry names the full stored key, a source's recorded COG the path
 * inside the asset), else the key as given; leading slashes are ignored. An unpinned file loads the
 * latest version, so it is the same bytes as a pin on that version; when the latest is not known, a
 * pin and the unpinned file may be different content and stay separate.
 */
function sameFile(a: RasterFileRef, b: RasterFileRef, identityOf?: IdentityLookup): boolean {
    if (a.databaseId !== b.databaseId || a.assetId !== b.assetId) return false;
    const path = (file: RasterFileRef) =>
        stripSlashes(identityOf?.(file)?.relativePath ?? file.key);
    if (stripSlashes(a.key) !== stripSlashes(b.key) && path(a) !== path(b)) return false;
    const effective = (file: RasterFileRef) =>
        file.versionId || identityOf?.(file)?.latestVersionId || "";
    if (!a.versionId && !b.versionId) return true;
    return effective(a) !== "" && effective(a) === effective(b);
}

/** A lookup that answers the same latest version for every file (callers that know one file). */
export function latestOnly(latestVersionId?: string): IdentityLookup | undefined {
    return latestVersionId ? () => ({ latestVersionId }) : undefined;
}

function boundsOfCorners(corners: [number, number][]): [[number, number], [number, number]] {
    const lons = corners.map((corner) => corner[0]);
    const lats = corners.map((corner) => corner[1]);
    return [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
    ];
}

/**
 * Draws prepared rasters on a MapLibre map, for the file viewer and the Search map alike. A tile-mode
 * raster is a `cog://` raster source over the file's presigned URL, replaced shortly before the URL
 * expires and again when a tile read is refused as expired; a placed raster is an image source over a
 * blob URL on four corners. The manager owns the order (bottom to top), opacity and cleanup: removing
 * a layer or disposing the manager removes its map layer and source, stops its refresh timer and
 * revokes its blob URL.
 */
export class RasterLayerManager {
    private items: Internal[] = [];
    private counter = 0;
    private disposed = false;
    private readonly max: number;
    private readonly onMapError = (event: any) => this.handleMapError(event);
    private readonly onSourceData = (event: any) => this.handleSourceData(event);

    constructor(private readonly map: MapLike, private readonly options: ManagerOptions = {}) {
        this.max = options.maxOverlays ?? MAX_RASTER_OVERLAYS;
        map.on("error", this.onMapError);
        map.on("sourcedata", this.onSourceData);
    }

    /** The shown rasters, bottom to top. */
    entries(): RasterLayerEntry[] {
        return this.items.map((item) => ({ ...item.entry }));
    }

    has(id: string): boolean {
        return this.items.some((item) => item.entry.id === id);
    }

    /**
     * True when the file drawn by a layer is this file. A source resolved to its converted COG and
     * the same COG selected by itself are one raster, though their ids can differ in how the key and
     * version are written.
     */
    hasFile(file: RasterFileRef, identityOf?: IdentityLookup): boolean {
        return this.items.some((item) => sameFile(item.entry.file, file, identityOf));
    }

    /**
     * The listed files that may be this file under another name or version: same database, asset
     * and file name, but not the same by key and version as written. A file manager entry names the
     * full stored key and a source's COG the path inside the asset, and one may be pinned and the
     * other not, so whether they are one raster needs the API's identity of each (see sameFile).
     */
    possibleDuplicates(file: RasterFileRef): RasterFileRef[] {
        return this.items
            .map((item) => item.entry.file)
            .filter(
                (listed) =>
                    listed.databaseId === file.databaseId &&
                    listed.assetId === file.assetId &&
                    fileName(listed) === fileName(file) &&
                    !sameFile(listed, file)
            );
    }

    /** True when whether this file is already listed depends on the API's identity of the files. */
    needsVersionCheck(file: RasterFileRef): boolean {
        return this.possibleDuplicates(file).length > 0;
    }

    add(prepared: PreparedRaster, opacity = 0.8, identityOf?: IdentityLookup): AddResult {
        if (this.disposed) return { ok: false, message: "The map is closed." };
        if (this.has(prepared.id) || this.hasFile(prepared.file, identityOf)) {
            return { ok: true };
        }
        if (this.items.length >= this.max) {
            return {
                ok: false,
                // Hidden layers count: they keep their place in the list and must be able to come back.
                message: `Up to ${this.max} rasters can be listed at once. Remove or deselect one to add another.`,
            };
        }

        const n = ++this.counter;
        const item: Internal = {
            entry: {
                id: prepared.id,
                label: prepared.label,
                mode: prepared.mode,
                opacity,
                approximate: prepared.approximate,
                approximateNote: prepared.approximateNote,
                usedCog: prepared.usedCog,
                staleCog: prepared.staleCog,
                sourceKey: prepared.sourceKey,
                file: prepared.file,
                failed: null,
                retryable: true,
                framable: false,
                visible: true,
            },
            sourceId: `${ID_PREFIX}-src-${n}`,
            layerId: `${ID_PREFIX}-layer-${n}`,
            url: prepared.url,
            bounds: null,
            errorRefreshInFlight: false,
            errorRefreshDoneAt: 0,
            errorRefreshes: 0,
            autoRetried: false,
        };

        if (prepared.mode === "placed" && prepared.placed) {
            item.blobUrl = URL.createObjectURL(prepared.placed.blob);
            item.corners = prepared.placed.corners;
            item.bounds = boundsOfCorners(prepared.placed.corners);
        } else if (prepared.bboxWgs84) {
            // The extent read from the COG's header frames the layer before any tile has loaded.
            const [west, south, east, north] = prepared.bboxWgs84;
            item.bounds = [
                [west, south],
                [east, north],
            ];
        }
        item.entry.framable = item.bounds !== null;

        this.addToMap(item);
        this.items.push(item);

        if (prepared.mode === "tile") this.startKeepFresh(item);

        this.applyOrder();
        this.notify();
        return { ok: true };
    }

    private startKeepFresh(item: Internal): void {
        const handle = keepFresh(
            item.entry.file,
            item.url,
            (url) => this.replaceTileUrl(item, url),
            this.options.requestUrl ?? requestPresignedUrl,
            (reason) => this.fail(item, reason ?? REFRESH_GAVE_UP_MESSAGE, reason === undefined)
        );
        item.stopRefresh = handle.stop;
        item.refreshNow = handle.refreshNow;
    }

    /**
     * Draws a failed raster again: asks for a fresh URL and rebuilds the layer in its place and with
     * its opacity. True when the layer is back on the map; false when the raster is not failed or the
     * URL could not be had (the row then says so).
     */
    async retry(id: string): Promise<boolean> {
        const item = this.items.find((candidate) => candidate.entry.id === id);
        if (!item || this.disposed || !item.entry.failed || item.entry.mode !== "tile")
            return false;
        let url: string;
        try {
            url = await (this.options.requestUrl ?? requestPresignedUrl)(item.entry.file);
        } catch (error) {
            if (this.items.includes(item)) {
                // A refusal shows the API's own reason and offers no Retry; anything else (network,
                // 5xx) keeps the renewal message and Retry.
                const refused = error instanceof RasterDownloadRefused;
                item.entry.failed = refused ? error.message : REFRESH_GAVE_UP_MESSAGE;
                item.entry.retryable = !refused;
                this.notify();
            }
            return false;
        }
        if (this.disposed || !this.items.includes(item)) return false;
        item.stopRefresh?.();
        item.url = url;
        item.entry.failed = null;
        item.entry.retryable = true;
        item.entry.visible = true;
        item.errorRefreshes = 0;
        item.errorRefreshDoneAt = 0;
        item.errorRefreshInFlight = false;
        item.autoRetried = false;
        this.removeFromMap(item);
        this.addToMap(item);
        this.startKeepFresh(item);
        this.applyOrder();
        this.notify();
        return true;
    }

    /**
     * Hides or shows a layer. Hiding takes the layer and its source off the map, stops the link
     * renewal and any pending automatic retry, so no tile is fetched while it is hidden; the row keeps its
     * opacity and place, and a placed image keeps its blob URL (revoked, as ever, when the layer is
     * removed or the manager is disposed). Showing a tile layer asks for a fresh link first, since the
     * old one may have expired; a refusal by the API puts its reason on the row. Resolves to the
     * visibility the layer ends up with.
     */
    async setVisible(id: string, visible: boolean): Promise<boolean> {
        const item = this.items.find((candidate) => candidate.entry.id === id);
        if (!item || this.disposed || item.entry.failed) return false;
        if (!visible) {
            if (!item.entry.visible) return false;
            item.entry.visible = false;
            item.stopRefresh?.();
            if (item.autoRetryTimer) clearTimeout(item.autoRetryTimer);
            item.autoRetryTimer = undefined;
            item.errorRefreshInFlight = false;
            this.removeFromMap(item);
            this.notify();
            return false;
        }
        if (item.entry.visible || item.showing) return item.entry.visible;
        if (item.entry.mode === "tile") {
            item.showing = true;
            let url: string;
            try {
                url = await (this.options.requestUrl ?? requestPresignedUrl)(item.entry.file);
            } catch (error) {
                item.showing = false;
                if (this.items.includes(item)) {
                    const refused = error instanceof RasterDownloadRefused;
                    this.fail(item, refused ? error.message : REFRESH_GAVE_UP_MESSAGE, !refused);
                }
                return false;
            }
            item.showing = false;
            if (this.disposed || !this.items.includes(item) || item.entry.failed) return false;
            item.url = url;
            item.entry.visible = true;
            item.errorRefreshes = 0;
            item.errorRefreshDoneAt = 0;
            item.autoRetried = false;
            this.addToMap(item);
            this.startKeepFresh(item);
        } else {
            item.entry.visible = true;
            this.addToMap(item);
        }
        this.applyOrder();
        this.notify();
        return true;
    }

    remove(id: string): void {
        const index = this.items.findIndex((item) => item.entry.id === id);
        if (index === -1) return;
        const [item] = this.items.splice(index, 1);
        this.release(item);
        this.notify();
    }

    setOpacity(id: string, opacity: number): void {
        const item = this.items.find((candidate) => candidate.entry.id === id);
        if (!item) return;
        item.entry.opacity = Math.min(1, Math.max(0, opacity));
        if (this.map.getLayer(item.layerId)) {
            this.map.setPaintProperty(item.layerId, "raster-opacity", item.entry.opacity);
        }
        this.notify();
    }

    /** Moves a raster one place up (towards the top) or down. */
    move(id: string, direction: "up" | "down"): void {
        const index = this.items.findIndex((item) => item.entry.id === id);
        const target = direction === "up" ? index + 1 : index - 1;
        if (index === -1 || target < 0 || target >= this.items.length) return;
        [this.items[index], this.items[target]] = [this.items[target], this.items[index]];
        this.applyOrder();
        this.notify();
    }

    zoomTo(id: string): boolean {
        const item = this.items.find((candidate) => candidate.entry.id === id);
        // A hidden layer is not on the map, so there is nothing to zoom to until it is shown.
        if (!item || !item.entry.visible) return false;
        const bounds = item.bounds ?? this.boundsFromSource(item);
        if (!bounds) return false;
        this.map.fitBounds(bounds, { padding: 40, maxZoom: 18 });
        return true;
    }

    /** Re-adds every layer after the map's style was replaced (a style change drops custom layers). */
    restore(): void {
        for (const item of this.items) {
            if (item.entry.visible && !item.entry.failed && !this.map.getSource(item.sourceId)) {
                this.addToMap(item);
            }
        }
        this.applyOrder();
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.map.off("error", this.onMapError);
        this.map.off("sourcedata", this.onSourceData);
        for (const item of this.items) this.release(item);
        this.items = [];
        this.notify();
    }

    private boundsFromSource(item: Internal): [[number, number], [number, number]] | null {
        const bounds = this.map.getSource(item.sourceId)?.bounds;
        if (Array.isArray(bounds) && bounds.length === 4 && bounds.every(Number.isFinite)) {
            return [
                [bounds[0], bounds[1]],
                [bounds[2], bounds[3]],
            ];
        }
        return null;
    }

    private sourceSpec(item: Internal): any {
        if (item.entry.mode === "placed") {
            return { type: "image", url: item.blobUrl, coordinates: item.corners };
        }
        return { type: "raster", url: `cog://${item.url}`, tileSize: 256 };
    }

    private addToMap(item: Internal): void {
        this.map.addSource(item.sourceId, this.sourceSpec(item));
        this.map.addLayer(
            {
                id: item.layerId,
                type: "raster",
                source: item.sourceId,
                paint: { "raster-opacity": item.entry.opacity, "raster-fade-duration": 0 },
            },
            this.anchor()
        );
    }

    private removeFromMap(item: Internal): void {
        if (this.map.getLayer(item.layerId)) this.map.removeLayer(item.layerId);
        if (this.map.getSource(item.sourceId)) this.map.removeSource(item.sourceId);
    }

    private release(item: Internal): void {
        item.stopRefresh?.();
        if (item.autoRetryTimer) clearTimeout(item.autoRetryTimer);
        this.removeFromMap(item);
        if (item.blobUrl) URL.revokeObjectURL(item.blobUrl);
    }

    /** Replaces a tile source with one over a fresh URL, keeping its place in the order and its opacity. */
    private replaceTileUrl(item: Internal, url: string): void {
        if (this.disposed || !this.items.includes(item) || item.entry.failed) return;
        item.url = url;
        this.removeFromMap(item);
        this.addToMap(item);
        this.applyOrder();
        if (item.errorRefreshInFlight) item.errorRefreshDoneAt = Date.now();
    }

    /** Marks a raster failed: its layer leaves the map, its row stays with the reason. */
    private fail(item: Internal, message: string, retryable = true): void {
        if (item.entry.failed || !this.items.includes(item)) return;
        item.entry.failed = message;
        item.entry.retryable = retryable;
        item.stopRefresh?.();
        if (item.autoRetryTimer) clearTimeout(item.autoRetryTimer);
        this.removeFromMap(item);
        this.notify();
    }

    private anchor(): string | undefined {
        const wanted = this.options.beforeLayerId?.();
        if (!wanted) return undefined;
        return this.map.getLayer(wanted) ? wanted : undefined;
    }

    private applyOrder(): void {
        const anchor = this.anchor();
        for (const item of this.items) {
            if (this.map.getLayer(item.layerId)) this.map.moveLayer(item.layerId, anchor);
        }
    }

    private handleMapError(event: any): void {
        const item = this.items.find((candidate) => candidate.sourceId === event?.sourceId);
        if (!item || item.entry.mode !== "tile" || item.entry.failed || !item.refreshNow) return;
        // A hidden layer has no source on the map: an error that still arrives for it is from a read
        // that was in flight when it was hidden, and says nothing about the file.
        if (!item.entry.visible) return;
        // A read failed. maplibre reports a failed cog:// tile only as "Request failed" (geotiff.js
        // drops the HTTP status), so the cause is told apart by what happens next: the first error asks
        // for a fresh URL (an expired or revoked presigned URL); an error soon after that fresh URL is
        // on the map, or too many refreshes, means the file itself cannot be read or decoded, and the
        // row is marked failed rather than left as a blank layer.
        if (item.errorRefreshInFlight) return;
        const now = Date.now();
        const soonAfterRefresh =
            item.errorRefreshDoneAt > 0 && now - item.errorRefreshDoneAt < TILE_FAIL_WINDOW_MS;
        if (!soonAfterRefresh) item.autoRetried = false;
        if (soonAfterRefresh || item.errorRefreshes >= MAX_ERROR_REFRESHES) {
            if (soonAfterRefresh && !item.autoRetried) {
                // One automatic retry: a short outage of the store clears by itself. Errors while
                // waiting are ignored; an error after the rebuilt layer ends in the failed row.
                item.autoRetried = true;
                item.errorRefreshInFlight = true;
                item.autoRetryTimer = setTimeout(() => {
                    item.autoRetryTimer = undefined;
                    if (this.disposed || item.entry.failed || !item.refreshNow) return;
                    void item.refreshNow().finally(() => {
                        item.errorRefreshInFlight = false;
                    });
                }, AUTO_RETRY_DELAY_MS);
                return;
            }
            this.fail(item, TILE_READ_FAILED_MESSAGE);
            return;
        }
        item.errorRefreshes += 1;
        item.errorRefreshInFlight = true;
        void item.refreshNow().finally(() => {
            item.errorRefreshInFlight = false;
        });
    }

    /** A tile source's own extent, once maplibre has read it, makes a layer without one framable. */
    private handleSourceData(event: any): void {
        const item = this.items.find((candidate) => candidate.sourceId === event?.sourceId);
        if (!item || item.bounds) return;
        const bounds = this.boundsFromSource(item);
        if (!bounds) return;
        item.bounds = bounds;
        item.entry.framable = true;
        this.notify();
    }

    private notify(): void {
        this.options.onChange?.(this.entries());
    }
}

/** The id of the first layer in the style whose id starts with one of `prefixes`, for `beforeLayerId`. */
export function firstLayerIdWithPrefix(map: MapLike, prefixes: string[]): string | undefined {
    const layers = map.getStyle()?.layers ?? [];
    return layers.find((layer) => prefixes.some((prefix) => layer.id.startsWith(prefix)))?.id;
}
