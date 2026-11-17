/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { RasterFileRef, keepFresh, requestPresignedUrl } from "./presignedUrl";
import { MAX_RASTER_OVERLAYS } from "./rasterEligibility";
import { PreparedRaster } from "./rasterResolver";

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
}

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
    lastTileErrorAt: number;
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
/** A tile error from the same layer is acted on at most this often. */
const TILE_ERROR_COOLDOWN_MS = 15 * 1000;

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

    constructor(private readonly map: MapLike, private readonly options: ManagerOptions = {}) {
        this.max = options.maxOverlays ?? MAX_RASTER_OVERLAYS;
        map.on("error", this.onMapError);
    }

    /** The shown rasters, bottom to top. */
    entries(): RasterLayerEntry[] {
        return this.items.map((item) => ({ ...item.entry }));
    }

    has(id: string): boolean {
        return this.items.some((item) => item.entry.id === id);
    }

    add(prepared: PreparedRaster, opacity = 0.8): AddResult {
        if (this.disposed) return { ok: false, message: "The map is closed." };
        if (this.has(prepared.id)) return { ok: true };
        if (this.items.length >= this.max) {
            return {
                ok: false,
                message: `Up to ${this.max} rasters can be shown at once. Remove one to add another.`,
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
            },
            sourceId: `${ID_PREFIX}-src-${n}`,
            layerId: `${ID_PREFIX}-layer-${n}`,
            url: prepared.url,
            bounds: null,
            lastTileErrorAt: 0,
        };

        if (prepared.mode === "placed" && prepared.placed) {
            item.blobUrl = URL.createObjectURL(prepared.placed.blob);
            item.corners = prepared.placed.corners;
            item.bounds = boundsOfCorners(prepared.placed.corners);
        }

        this.addToMap(item);
        this.items.push(item);

        if (prepared.mode === "tile") {
            const handle = keepFresh(
                prepared.file,
                prepared.url,
                (url) => this.replaceTileUrl(item, url),
                this.options.requestUrl ?? requestPresignedUrl
            );
            item.stopRefresh = handle.stop;
            item.refreshNow = handle.refreshNow;
        }

        this.applyOrder();
        this.notify();
        return { ok: true };
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
        if (!item) return false;
        const bounds = item.bounds ?? this.boundsFromSource(item);
        if (!bounds) return false;
        this.map.fitBounds(bounds, { padding: 40, maxZoom: 18 });
        return true;
    }

    /** Re-adds every layer after the map's style was replaced (a style change drops custom layers). */
    restore(): void {
        for (const item of this.items) {
            if (!this.map.getSource(item.sourceId)) this.addToMap(item);
        }
        this.applyOrder();
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.map.off("error", this.onMapError);
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
        this.removeFromMap(item);
        if (item.blobUrl) URL.revokeObjectURL(item.blobUrl);
    }

    /** Replaces a tile source with one over a fresh URL, keeping its place in the order and its opacity. */
    private replaceTileUrl(item: Internal, url: string): void {
        if (this.disposed || !this.items.includes(item)) return;
        item.url = url;
        this.removeFromMap(item);
        this.addToMap(item);
        this.applyOrder();
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
        if (!item || item.entry.mode !== "tile" || !item.refreshNow) return;
        // A read the store refused as expired (403) or unauthorized: ask for a fresh URL, at most once
        // in a while, so a file that really is denied does not loop.
        const status = event?.error?.status ?? event?.status;
        const message = String(event?.error?.message ?? "");
        if (status !== 403 && !/\b403\b/.test(message)) return;
        const now = Date.now();
        if (now - item.lastTileErrorAt < TILE_ERROR_COOLDOWN_MS) return;
        item.lastTileErrorAt = now;
        void item.refreshNow();
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
