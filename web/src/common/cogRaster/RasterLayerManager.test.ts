/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { MapLike, RasterLayerManager, firstLayerIdWithPrefix } from "./RasterLayerManager";
import { PreparedRaster } from "./rasterResolver";

class FakeMap implements MapLike {
    sources = new Map<string, any>();
    order: string[] = ["basemap", "geojson-fill-1"];
    listeners = new Map<string, (e: any) => void>();
    paint: Record<string, any> = {};
    fit = jest.fn();
    addSource = (id: string, spec: any) => void this.sources.set(id, spec);
    removeSource = (id: string) => void this.sources.delete(id);
    getSource = (id: string) => this.sources.get(id);
    addLayer = (layer: any, before?: string) => {
        const at = before ? this.order.indexOf(before) : -1;
        if (at === -1) this.order.push(layer.id);
        else this.order.splice(at, 0, layer.id);
        this.paint[layer.id] = { ...layer.paint };
    };
    removeLayer = (id: string) => {
        this.order = this.order.filter((layer) => layer !== id);
        delete this.paint[id];
    };
    getLayer = (id: string) => (this.order.includes(id) ? { id } : undefined);
    moveLayer = (id: string, before?: string) => {
        this.order = this.order.filter((layer) => layer !== id);
        const at = before ? this.order.indexOf(before) : -1;
        if (at === -1) this.order.push(id);
        else this.order.splice(at, 0, id);
    };
    setPaintProperty = (id: string, name: string, value: unknown) => {
        this.paint[id][name] = value;
    };
    getStyle = () => ({ layers: this.order.map((id) => ({ id })) });
    fitBounds = this.fit;
    on = (type: string, listener: (e: any) => void) => void this.listeners.set(type, listener);
    off = (type: string) => void this.listeners.delete(type);
}

const prepared = (name: string, mode: "tile" | "placed" = "tile"): PreparedRaster => ({
    id: `db/a/${name}`,
    label: name,
    mode,
    file: { databaseId: "db", assetId: "a", key: name },
    sourceKey: name,
    usedCog: false,
    staleCog: false,
    approximate: mode === "placed",
    approximateNote: mode === "placed" ? "approximate" : null,
    bboxWgs84: null,
    url: `https://s3/${name}?X-Amz-Date=20261117T170000Z&X-Amz-Expires=3600`,
    placed:
        mode === "placed"
            ? {
                  blob: new Blob(["x"], { type: "image/png" }),
                  corners: [
                      [10, 50],
                      [11, 50],
                      [11, 49],
                      [10, 49],
                  ],
                  width: 4,
                  height: 4,
              }
            : undefined,
});

const OPTIONS = (map: FakeMap) => ({
    beforeLayerId: () => firstLayerIdWithPrefix(map, ["geojson-"]),
});

beforeAll(() => {
    (URL as any).createObjectURL = jest.fn(() => "blob:fake");
    (URL as any).revokeObjectURL = jest.fn();
});
beforeEach(() => jest.clearAllMocks());

describe("RasterLayerManager", () => {
    it("adds a tile layer on a cog:// source below the footprint layers", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        expect(manager.add(prepared("a.cog.tif"))).toEqual({ ok: true });
        const source = [...map.sources.values()][0];
        expect(source.type).toBe("raster");
        expect(source.url).toMatch(/^cog:\/\/https:\/\/s3\/a\.cog\.tif/);
        expect(source.tileSize).toBe(256);
        expect(map.order).toEqual([
            "basemap",
            expect.stringMatching(/vams-raster-layer/),
            "geojson-fill-1",
        ]);
        manager.dispose();
    });

    it("adds a placed layer as an image source on four corners and revokes the blob on remove", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("b.tif", "placed"));
        const source = [...map.sources.values()][0];
        expect(source.type).toBe("image");
        expect(source.url).toBe("blob:fake");
        expect(source.coordinates).toHaveLength(4);
        manager.remove("db/a/b.tif");
        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake");
        expect(map.sources.size).toBe(0);
        expect(map.order).toEqual(["basemap", "geojson-fill-1"]);
    });

    it("shows a raster once and refuses one past the cap with a message", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), maxOverlays: 2 });
        manager.add(prepared("1.tif"));
        manager.add(prepared("1.tif"));
        expect(manager.entries()).toHaveLength(1);
        manager.add(prepared("2.tif"));
        const refused = manager.add(prepared("3.tif"));
        expect(refused).toMatchObject({ ok: false });
        expect((refused as any).message).toMatch(/Up to 2 rasters/);
        manager.dispose();
    });

    it("sets opacity and reorders", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("1.tif"));
        manager.add(prepared("2.tif"));
        const [first] = manager.entries();
        manager.setOpacity(first.id, 0.25);
        const layerOf = (index: number) =>
            map.order.filter((id) => id.startsWith("vams-raster-layer"))[index];
        expect(map.paint[layerOf(0)]["raster-opacity"]).toBe(0.25);
        manager.move(first.id, "up");
        expect(manager.entries().map((e) => e.label)).toEqual(["2.tif", "1.tif"]);
        expect(layerOf(1)).toContain("layer-1");
        manager.move(first.id, "up");
        expect(manager.entries().map((e) => e.label)).toEqual(["2.tif", "1.tif"]);
        manager.dispose();
    });

    it("replaces a tile source with a fresh URL on a 403, keeping order and opacity", async () => {
        const map = new FakeMap();
        const request = jest
            .fn()
            .mockResolvedValue("https://s3/fresh?X-Amz-Date=20261117T170000Z&X-Amz-Expires=3600");
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
        manager.add(prepared("1.tif"));
        manager.add(prepared("2.tif"));
        const [first] = manager.entries();
        manager.setOpacity(first.id, 0.3);
        const sourceId = [...map.sources.keys()][0];
        map.listeners.get("error")!({ sourceId, error: { status: 403, message: "Forbidden" } });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(request).toHaveBeenCalledTimes(1);
        expect(
            map.sources.get(sourceId)?.url ??
                [...map.sources.values()].find((s) => s.url.includes("fresh"))
        ).toBeTruthy();
        const layers = map.order.filter((id) => id.startsWith("vams-raster-layer"));
        expect(layers).toHaveLength(2);
        expect(map.order.indexOf("geojson-fill-1")).toBeGreaterThan(map.order.indexOf(layers[1]));
        const freshLayer = layers.find((id) => map.paint[id]["raster-opacity"] === 0.3);
        expect(freshLayer).toBeTruthy();
        manager.dispose();
    });

    it("marks a tile layer failed when reads still fail right after a fresh URL (D4)", async () => {
        const map = new FakeMap();
        const changes: any[] = [];
        const request = jest.fn().mockResolvedValue("https://s3/fresh");
        const manager = new RasterLayerManager(map, {
            ...OPTIONS(map),
            requestUrl: request,
            onChange: (entries) => changes.push(entries),
        });
        manager.add(prepared("1.tif"));
        const sourceId = [...map.sources.keys()][0];
        // maplibre reports a failed cog:// tile as "Request failed", with no status.
        map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(request).toHaveBeenCalledTimes(1);
        expect(manager.entries()[0].failed).toBeNull();
        // The fresh URL fails too: the file cannot be decoded, not an expired link.
        map.listeners.get("error")!({
            sourceId,
            error: new RangeError("Invalid typed array length"),
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(request).toHaveBeenCalledTimes(1);
        const [entry] = manager.entries();
        expect(entry.failed).toMatch(/could not be drawn/);
        // Its layer leaves the map; its row stays with the reason, and later errors are ignored.
        expect(map.sources.size).toBe(0);
        expect(changes[changes.length - 1][0].failed).toMatch(/could not be drawn/);
        map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
        expect(request).toHaveBeenCalledTimes(1);
        manager.dispose();
    });

    it("ignores further errors while a refresh is in flight", async () => {
        const map = new FakeMap();
        let resolve: (url: string) => void = () => undefined;
        const request = jest.fn(() => new Promise<string>((r) => (resolve = r)));
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
        manager.add(prepared("1.tif"));
        const sourceId = [...map.sources.keys()][0];
        map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
        map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
        expect(request).toHaveBeenCalledTimes(1);
        resolve("https://s3/fresh");
        await new Promise((r) => setTimeout(r, 0));
        expect(manager.entries()[0].failed).toBeNull();
        manager.dispose();
    });

    it("marks a tile layer failed when its link cannot be renewed", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockRejectedValue(new Error("refused"));
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            const sourceId = [...map.sources.keys()][0];
            map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
            await jest.advanceTimersByTimeAsync(60 * 60 * 1000);
            expect(request).toHaveBeenCalledTimes(3);
            expect(manager.entries()[0].failed).toMatch(/could not be renewed/);
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("frames a tile layer from the extent its header gave (D3)", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add({ ...prepared("t.cog.tif"), bboxWgs84: [1, 2, 3, 4] });
        expect(manager.entries()[0].framable).toBe(true);
        expect(manager.zoomTo("db/a/t.cog.tif")).toBe(true);
        expect(map.fit).toHaveBeenLastCalledWith(
            [
                [1, 2],
                [3, 4],
            ],
            expect.anything()
        );
        manager.dispose();
    });

    it("becomes framable once the tile source reports its extent (D3)", () => {
        const map = new FakeMap();
        const changes: any[] = [];
        const manager = new RasterLayerManager(map, {
            ...OPTIONS(map),
            onChange: (entries) => changes.push(entries),
        });
        manager.add(prepared("t.cog.tif"));
        expect(manager.entries()[0].framable).toBe(false);
        expect(manager.zoomTo("db/a/t.cog.tif")).toBe(false);
        const [sourceId, source] = [...map.sources.entries()][0];
        source.bounds = [5, 6, 7, 8];
        map.listeners.get("sourcedata")!({ sourceId });
        expect(changes[changes.length - 1][0].framable).toBe(true);
        expect(manager.zoomTo("db/a/t.cog.tif")).toBe(true);
        manager.dispose();
    });

    it("ignores errors from other sources", async () => {
        const map = new FakeMap();
        const request = jest.fn();
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
        manager.add(prepared("1.tif"));
        map.listeners.get("error")!({ sourceId: "other", error: { status: 403 } });
        map.listeners.get("error")!({ error: new Error("style") });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(request).not.toHaveBeenCalled();
        manager.dispose();
    });

    it("zooms to a placed layer's corners and a tile layer's source bounds", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("p.tif", "placed"));
        expect(manager.zoomTo("db/a/p.tif")).toBe(true);
        expect(map.fit).toHaveBeenLastCalledWith(
            [
                [10, 49],
                [11, 50],
            ],
            expect.anything()
        );
        manager.add(prepared("t.cog.tif"));
        const tileSource = [...map.sources.entries()].find(([, s]) => s.type === "raster")!;
        tileSource[1].bounds = [1, 2, 3, 4];
        expect(manager.zoomTo("db/a/t.cog.tif")).toBe(true);
        expect(map.fit).toHaveBeenLastCalledWith(
            [
                [1, 2],
                [3, 4],
            ],
            expect.anything()
        );
        expect(manager.zoomTo("missing")).toBe(false);
        manager.dispose();
    });

    it("dispose removes every layer and source and stops listening", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("1.tif"));
        manager.add(prepared("2.tif", "placed"));
        manager.dispose();
        expect(map.sources.size).toBe(0);
        expect(map.order).toEqual(["basemap", "geojson-fill-1"]);
        expect(map.listeners.size).toBe(0);
        expect(manager.add(prepared("3.tif"))).toMatchObject({ ok: false });
    });
});
