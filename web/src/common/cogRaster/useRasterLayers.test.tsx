/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRasterLayers } from "./useRasterLayers";
import RasterLayerPanel from "./RasterLayerPanel";
import { RasterFileRef } from "./presignedUrl";
import { MapLike } from "./RasterLayerManager";
import { prepareRaster, rasterId, rasterLabel } from "./rasterResolver";

jest.mock("./cogProtocol", () => ({ registerCogProtocol: jest.fn().mockResolvedValue(undefined) }));
jest.mock("./rasterResolver", () => ({
    ...jest.requireActual("./rasterResolver"),
    prepareRaster: jest.fn(),
}));
jest.mock("./presignedUrl", () => ({
    ...jest.requireActual("./presignedUrl"),
    keepFresh: jest.fn(() => ({ stop: jest.fn(), refreshNow: jest.fn() })),
    requestPresignedUrl: jest.fn().mockResolvedValue("https://s3/fresh"),
}));

function fakeMap(): MapLike {
    const layers = new Set<string>();
    const sources = new Set<string>();
    return {
        addSource: (id: string) => void sources.add(id),
        removeSource: (id: string) => void sources.delete(id),
        getSource: (id: string) => (sources.has(id) ? { id } : undefined),
        addLayer: (layer: any) => void layers.add(layer.id),
        removeLayer: (id: string) => void layers.delete(id),
        getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
        moveLayer: jest.fn(),
        setPaintProperty: jest.fn(),
        getStyle: () => ({ layers: [] }),
        fitBounds: jest.fn(),
        on: jest.fn(),
        off: jest.fn(),
    };
}

const SOURCE: RasterFileRef = { databaseId: "db", assetId: "a", key: "/maps/s.tif" };
const COG: RasterFileRef = { databaseId: "db", assetId: "a", key: "/maps/s.tif.cog.tif" };

/** What prepareRaster returns: a source resolves to its COG (prefer-COG), a COG stays itself. */
function resolveTo(file: RasterFileRef) {
    const isSource = file.key === SOURCE.key;
    const target: RasterFileRef = isSource ? { ...COG } : file;
    return {
        id: rasterId(target),
        label: rasterLabel(target.key),
        mode: "tile" as const,
        file: target,
        sourceKey: file.key,
        usedCog: isSource,
        staleCog: false,
        approximate: false,
        approximateNote: null,
        bboxWgs84: null,
        url: "https://s3/x?X-Amz-Date=20991117T170000Z&X-Amz-Expires=3600",
    };
}

beforeEach(() => {
    jest.clearAllMocks();
    (prepareRaster as jest.Mock).mockImplementation(async (file: RasterFileRef) => resolveTo(file));
});

async function addAll(files: RasterFileRef[]) {
    const map = fakeMap();
    const { result } = renderHook(() => useRasterLayers(map));
    await waitFor(() => expect(result.current.entries).toEqual([]));
    await act(async () => {
        await Promise.all(files.map((file) => result.current.addFile(file)));
    });
    return result;
}

describe("useRasterLayers: a raster is listed once (r2-D5)", () => {
    it("lists the COG once when its source is selected with it", async () => {
        const result = await addAll([SOURCE, COG]);
        expect(result.current.entries).toHaveLength(1);
        expect(result.current.entries[0].label).toBe("s.tif.cog.tif");
    });

    it("lists the COG once when the COG comes first, and when it carries a version", async () => {
        const first = await addAll([COG, SOURCE]);
        expect(first.current.entries).toHaveLength(1);
        const versioned = await addAll([
            SOURCE,
            { ...COG, key: "maps/s.tif.cog.tif", versionId: "v7" },
        ]);
        expect(versioned.current.entries).toHaveLength(1);
    });

    it("lists the COG once when two sources resolve to the same COG", async () => {
        const result = await addAll([SOURCE, { ...SOURCE }]);
        expect(result.current.entries).toHaveLength(1);
        expect(result.current.entries[0].usedCog).toBe(true);
    });

    it("lists a COG selected alone once", async () => {
        const result = await addAll([COG]);
        expect(result.current.entries).toHaveLength(1);
        expect(result.current.entries[0].usedCog).toBe(false);
    });
});

describe("RasterLayerPanel: failed rasters offer Retry (r2-D4)", () => {
    const layers = (retry: jest.Mock, mode: "tile" | "placed") => ({
        entries: [
            {
                id: "r1",
                label: "s.tif.cog.tif",
                mode,
                opacity: 0.8,
                approximate: false,
                approximateNote: null,
                usedCog: false,
                staleCog: false,
                sourceKey: "s",
                file: COG,
                failed: "This raster could not be drawn: its map tiles could not be read or decoded.",
                framable: true,
            },
        ],
        notices: [],
        busy: 0,
        remove: jest.fn(),
        setOpacity: jest.fn(),
        move: jest.fn(),
        zoomTo: jest.fn(),
        dismissNotice: jest.fn(),
        retry,
    });

    it("calls retry with the raster's id", async () => {
        const retry = jest.fn().mockResolvedValue(true);
        render(<RasterLayerPanel layers={layers(retry, "tile")} />);
        expect(screen.getByTestId("raster-layer-failed")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(retry).toHaveBeenCalledWith("r1");
    });

    it("offers no Retry on a raster that is not drawn from tiles", () => {
        render(<RasterLayerPanel layers={layers(jest.fn(), "placed")} />);
        expect(screen.queryByTestId("raster-layer-retry")).not.toBeInTheDocument();
    });
});
