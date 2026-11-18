/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    AUTO_RETRY_DELAY_MS,
    MapLike,
    RasterLayerManager,
    firstLayerIdWithPrefix,
} from "./RasterLayerManager";
import { PreparedRaster } from "./rasterResolver";
import { RasterDownloadRefused } from "./presignedUrl";

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

    it("retries once on its own when reads still fail right after a fresh URL, then marks it failed (r2-D4)", async () => {
        jest.useFakeTimers();
        try {
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
            const fail = () =>
                map.listeners.get("error")!({
                    sourceId: [...map.sources.keys()][0],
                    error: new Error("Request failed"),
                });
            // maplibre reports a failed cog:// tile as "Request failed", with no status.
            fail();
            await jest.advanceTimersByTimeAsync(0);
            expect(request).toHaveBeenCalledTimes(1);
            expect(manager.entries()[0].failed).toBeNull();
            // The fresh URL fails too: one automatic retry after a short wait, errors meanwhile ignored.
            fail();
            fail();
            expect(manager.entries()[0].failed).toBeNull();
            expect(request).toHaveBeenCalledTimes(1);
            await jest.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
            expect(request).toHaveBeenCalledTimes(2);
            expect(manager.entries()[0].failed).toBeNull();
            expect(map.sources.size).toBe(1);
            // The retry fails as well: the file cannot be read, not a passing outage.
            fail();
            await jest.advanceTimersByTimeAsync(0);
            const [entry] = manager.entries();
            expect(entry.failed).toMatch(/could not be drawn/);
            expect(request).toHaveBeenCalledTimes(2);
            // Its layer leaves the map; its row stays with the reason, and later errors are ignored.
            expect(map.sources.size).toBe(0);
            expect(changes[changes.length - 1][0].failed).toMatch(/could not be drawn/);
            map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
            expect(request).toHaveBeenCalledTimes(2);
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("draws again after the single automatic retry when the store has recovered (r2-D4)", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockResolvedValue("https://s3/fresh");
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            const err = () => ({
                sourceId: [...map.sources.keys()][0],
                error: new Error("Request failed"),
            });
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
            // No further error: the layer stays on the map with the last fresh URL.
            await jest.advanceTimersByTimeAsync(60 * 1000);
            expect(manager.entries()[0].failed).toBeNull();
            expect([...map.sources.values()][0].url).toContain("fresh");
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("retry() rebuilds a failed tile layer from a fresh URL, keeping place and opacity (r2-D4)", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockResolvedValue("https://s3/fresh2");
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            const [one] = manager.entries();
            manager.setOpacity(one.id, 0.4);
            const err = () => ({
                sourceId: [...map.sources.keys()][0],
                error: new Error("Request failed"),
            });
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            expect(manager.entries()[0].failed).toMatch(/could not be drawn/);
            expect(map.sources.size).toBe(0);
            request.mockClear();

            await expect(manager.retry(one.id)).resolves.toBe(true);
            expect(request).toHaveBeenCalledTimes(1);
            expect(manager.entries()[0].failed).toBeNull();
            expect(map.sources.size).toBe(1);
            const layers = map.order.filter((id) => id.startsWith("vams-raster-layer"));
            expect(layers).toHaveLength(1);
            expect(map.order.indexOf("geojson-fill-1")).toBeGreaterThan(
                map.order.indexOf(layers[0])
            );
            expect(map.paint[layers[0]]["raster-opacity"]).toBe(0.4);
            expect([...map.sources.values()].some((s) => s.url.includes("fresh2"))).toBe(true);
            // A rebuilt layer can fail and be retried again (counters were reset).
            map.listeners.get("error")!({
                sourceId: [...map.sources.keys()].pop(),
                error: new Error("x"),
            });
            await jest.advanceTimersByTimeAsync(0);
            expect(manager.entries()[0].failed).toBeNull();
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("retry() does nothing for a raster that is not failed, and keeps the row failed when no URL can be had", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockResolvedValue("https://s3/fresh");
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            const [one] = manager.entries();
            await expect(manager.retry(one.id)).resolves.toBe(false);
            await expect(manager.retry("nope")).resolves.toBe(false);
            expect(request).not.toHaveBeenCalled();

            const err = () => ({
                sourceId: [...map.sources.keys()][0],
                error: new Error("Request failed"),
            });
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            request.mockRejectedValue(new Error("refused"));
            await expect(manager.retry(one.id)).resolves.toBe(false);
            expect(manager.entries()[0].failed).toMatch(/could not be renewed/);
            expect(map.sources.size).toBe(0);
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("shows a file once, however its key is written, and keeps pinned versions apart (r2-D5, r3-L)", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        const cog = (key: string, versionId?: string, assetId = "a"): PreparedRaster => ({
            ...prepared("x"),
            id: `db/${assetId}/${key}${versionId ? `@${versionId}` : ""}`,
            label: "a.tif.cog.tif",
            file: { databaseId: "db", assetId, key, versionId },
        });
        expect(manager.add(cog("/maps/a.tif.cog.tif"))).toEqual({ ok: true });
        // The same COG with the leading slash dropped is the same raster.
        expect(manager.add(cog("maps/a.tif.cog.tif"))).toEqual({ ok: true });
        expect(manager.entries()).toHaveLength(1);
        expect(manager.hasFile({ databaseId: "db", assetId: "a", key: "maps/a.tif.cog.tif" })).toBe(
            true
        );
        // A pinned version is not the unpinned (latest) file: it is a second layer, as is another
        // version, another key, or another asset.
        manager.add(cog("maps/a.tif.cog.tif", "v3"));
        expect(manager.entries()).toHaveLength(2);
        manager.add(cog("maps/a.tif.cog.tif", "v3"));
        manager.add(cog("maps/a.tif.cog.tif", "v4"));
        expect(manager.entries()).toHaveLength(3);
        manager.add(cog("/maps/b.tif.cog.tif"));
        manager.add(cog("/maps/a.tif.cog.tif", undefined, "z"));
        expect(manager.entries()).toHaveLength(5);
        manager.dispose();
    });

    it("treats a pin on the latest version as the unpinned file, and an older pin as another (r4-D1)", () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        const cog = (versionId?: string): PreparedRaster => ({
            ...prepared("x"),
            id: `db/a/maps/a.tif.cog.tif${versionId ? `@${versionId}` : ""}`,
            file: { databaseId: "db", assetId: "a", key: "/maps/a.tif.cog.tif", versionId },
        });
        manager.add(cog());
        expect(manager.needsVersionCheck(cog("v7").file)).toBe(true);
        expect(manager.needsVersionCheck(cog().file)).toBe(false);
        expect(manager.add(cog("v7"), undefined, "v7")).toEqual({ ok: true });
        expect(manager.entries()).toHaveLength(1);
        manager.add(cog("v6"), undefined, "v7");
        expect(manager.entries()).toHaveLength(2);
        // Without the latest, the pin stays apart.
        manager.add(cog("v5"));
        expect(manager.entries()).toHaveLength(3);
        // A pin first, the unpinned file second: same rule.
        const other = new RasterLayerManager(new FakeMap(), OPTIONS(map));
        other.add(cog("v7"));
        expect(other.needsVersionCheck(cog().file)).toBe(true);
        other.add(cog(), undefined, "v7");
        expect(other.entries()).toHaveLength(1);
        other.dispose();
        manager.dispose();
    });

    it("hides a tile layer off the map and shows it again with its opacity and place (item 1)", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockResolvedValue("https://s3/fresh-show");
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            manager.add(prepared("2.tif"));
            const [one, two] = manager.entries();
            manager.setOpacity(one.id, 0.3);
            await manager.setVisible(one.id, false);
            // No map layer or source remains for it, so no tile is fetched; the row stays, dimmed state set.
            expect(manager.entries()[0].visible).toBe(false);
            expect(map.sources.size).toBe(1);
            expect(map.order.filter((id) => id.startsWith("vams-raster-layer"))).toHaveLength(1);
            // The renewal timer is stopped: nothing is requested however long it stays hidden.
            await jest.advanceTimersByTimeAsync(24 * 60 * 60 * 1000);
            expect(request.mock.calls.filter(([file]) => file.key === "1.tif")).toHaveLength(0);
            request.mockClear();
            // Zoom has nothing to act on, order still works, opacity can still be set.
            expect(manager.zoomTo(one.id)).toBe(false);
            manager.setOpacity(one.id, 0.4);
            manager.move(one.id, "up");
            expect(manager.entries().map((e) => e.id)).toEqual([two.id, one.id]);

            await expect(manager.setVisible(one.id, true)).resolves.toBe(true);
            // A tile layer asks for a fresh link first.
            expect(request.mock.calls.filter(([file]) => file.key === "1.tif")).toHaveLength(1);
            expect(manager.entries()[1].visible).toBe(true);
            expect(map.sources.size).toBe(2);
            const layers = map.order.filter((id) => id.startsWith("vams-raster-layer"));
            expect(layers).toHaveLength(2);
            expect(map.order.indexOf("geojson-fill-1")).toBeGreaterThan(
                map.order.indexOf(layers[1])
            );
            expect(layers.some((id) => map.paint[id]["raster-opacity"] === 0.4)).toBe(true);
            expect([...map.sources.values()].some((s) => s.url.includes("fresh-show"))).toBe(true);
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
    });

    it("keeps a hidden placed image's blob URL until the layer is removed or the map goes (item 1)", async () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("p.tif", "placed"));
        const [entry] = manager.entries();
        await manager.setVisible(entry.id, false);
        expect(map.sources.size).toBe(0);
        expect(URL.revokeObjectURL).not.toHaveBeenCalled();
        await expect(manager.setVisible(entry.id, true)).resolves.toBe(true);
        expect(map.sources.size).toBe(1);
        expect((URL.createObjectURL as jest.Mock).mock.calls).toHaveLength(1);
        await manager.setVisible(entry.id, false);
        manager.dispose();
        expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    });

    it("counts hidden layers against the cap, so showing one always succeeds (item 1)", async () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), maxOverlays: 2 });
        manager.add(prepared("1.tif"));
        manager.add(prepared("2.tif"));
        await manager.setVisible(manager.entries()[0].id, false);
        const full = manager.add(prepared("3.tif"));
        expect(full.ok).toBe(false);
        expect((full as any).message).toMatch(/Up to 2 rasters/);
        manager.dispose();
    });

    it("does not re-add a hidden layer when the style is restored (item 1)", async () => {
        const map = new FakeMap();
        const manager = new RasterLayerManager(map, OPTIONS(map));
        manager.add(prepared("1.tif"));
        await manager.setVisible(manager.entries()[0].id, false);
        manager.restore();
        expect(map.sources.size).toBe(0);
        manager.dispose();
    });

    it("shows nothing and puts the API's reason on the row when the link for a hidden layer is refused (item 1)", async () => {
        const map = new FakeMap();
        const request = jest.fn().mockRejectedValue(new RasterDownloadRefused("Quarantined."));
        const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
        manager.add(prepared("1.tif"));
        const [entry] = manager.entries();
        await manager.setVisible(entry.id, false);
        await expect(manager.setVisible(entry.id, true)).resolves.toBe(false);
        expect(map.sources.size).toBe(0);
        expect(manager.entries()[0].failed).toBe("Quarantined.");
        expect(manager.entries()[0].retryable).toBe(false);
        manager.dispose();
    });

    it("gives up at once on a refusal from the API, with its reason and no Retry (r3-D2)", async () => {
        jest.useFakeTimers();
        try {
            for (const reason of [
                "You do not have permission to view or download this file.",
                "Asset is quarantined and cannot be downloaded",
            ]) {
                const map = new FakeMap();
                const request = jest.fn().mockRejectedValue(new RasterDownloadRefused(reason));
                const manager = new RasterLayerManager(map, {
                    ...OPTIONS(map),
                    requestUrl: request,
                });
                manager.add(prepared("1.tif"));
                const sourceId = [...map.sources.keys()][0];
                // A tile read fails; the renewal it triggers is refused.
                map.listeners.get("error")!({ sourceId, error: new Error("Request failed") });
                await jest.advanceTimersByTimeAsync(0);
                expect(request).toHaveBeenCalledTimes(1);
                let [entry] = manager.entries();
                expect(entry.failed).toBe(reason);
                expect(entry.retryable).toBe(false);
                expect(map.sources.size).toBe(0);
                await jest.advanceTimersByTimeAsync(60 * 60 * 1000);
                expect(request).toHaveBeenCalledTimes(1);
                // Retry itself, asked for anyway, shows the reason and stays non-retryable.
                await expect(manager.retry(entry.id)).resolves.toBe(false);
                [entry] = manager.entries();
                expect(entry.failed).toBe(reason);
                expect(entry.retryable).toBe(false);
                manager.dispose();
            }
        } finally {
            jest.useRealTimers();
        }
    });

    it("Retry refused by the API shows its reason and offers no further Retry (r3-D2)", async () => {
        jest.useFakeTimers();
        try {
            const map = new FakeMap();
            const request = jest.fn().mockResolvedValue("https://s3/fresh");
            const manager = new RasterLayerManager(map, { ...OPTIONS(map), requestUrl: request });
            manager.add(prepared("1.tif"));
            const err = () => ({
                sourceId: [...map.sources.keys()][0],
                error: new Error("Request failed"),
            });
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
            map.listeners.get("error")!(err());
            await jest.advanceTimersByTimeAsync(0);
            const [failed] = manager.entries();
            expect(failed.retryable).toBe(true);
            request.mockRejectedValue(new RasterDownloadRefused("Gone: this version is archived."));
            await expect(manager.retry(failed.id)).resolves.toBe(false);
            const [after] = manager.entries();
            expect(after.failed).toBe("Gone: this version is archived.");
            expect(after.retryable).toBe(false);
            manager.dispose();
        } finally {
            jest.useRealTimers();
        }
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
