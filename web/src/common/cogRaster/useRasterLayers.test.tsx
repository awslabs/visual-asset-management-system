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
import { latestVersionId, prepareRaster, rasterId, rasterLabel } from "./rasterResolver";

jest.mock("./cogProtocol", () => ({ registerCogProtocol: jest.fn().mockResolvedValue(undefined) }));
jest.mock("./rasterResolver", () => ({
    ...jest.requireActual("./rasterResolver"),
    prepareRaster: jest.fn(),
    latestVersionId: jest.fn(),
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
    (latestVersionId as jest.Mock).mockResolvedValue(undefined);
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

    it("lists the COG once when the COG comes first, and when its key has no leading slash", async () => {
        const first = await addAll([COG, SOURCE]);
        expect(first.current.entries).toHaveLength(1);
        const slashless = await addAll([SOURCE, { ...COG, key: "maps/s.tif.cog.tif" }]);
        expect(slashless.current.entries).toHaveLength(1);
    });

    it("lists the COG once when two sources resolve to the same COG", async () => {
        const result = await addAll([SOURCE, { ...SOURCE }]);
        expect(result.current.entries).toHaveLength(1);
        expect(result.current.entries[0].usedCog).toBe(true);
    });

    it("lists the COG once when its source is selected with the COG at its current version (live r4-D1, both orders)", async () => {
        (latestVersionId as jest.Mock).mockResolvedValue("v7");
        const pinned = { ...COG, versionId: "v7" };
        for (const order of [
            [SOURCE, pinned],
            [pinned, SOURCE],
        ]) {
            const result = await addAll(order);
            expect(result.current.entries).toHaveLength(1);
        }
    });

    it("lists the unpinned COG and its pin on the latest version once, and asks the API only then", async () => {
        (latestVersionId as jest.Mock).mockResolvedValue("v7");
        const result = await addAll([COG, { ...COG, versionId: "v7" }]);
        expect(result.current.entries).toHaveLength(1);
        expect(latestVersionId).toHaveBeenCalled();
        (latestVersionId as jest.Mock).mockClear();
        const plain = await addAll([COG]);
        expect(plain.current.entries).toHaveLength(1);
        expect(latestVersionId).not.toHaveBeenCalled();
    });

    it("keeps two different versions of one file as two rows", async () => {
        (latestVersionId as jest.Mock).mockResolvedValue("v8");
        // An older pin and the latest file.
        const older = await addAll([SOURCE, { ...COG, versionId: "v7" }]);
        expect(older.current.entries).toHaveLength(2);
        // Two pins.
        const pins = await addAll([
            { ...COG, versionId: "v7" },
            { ...COG, versionId: "v8" },
        ]);
        expect(pins.current.entries).toHaveLength(2);
    });

    it("keeps a pin apart from the unpinned file when the latest version is unknown", async () => {
        (latestVersionId as jest.Mock).mockResolvedValue(undefined);
        const result = await addAll([SOURCE, { ...COG, versionId: "v7" }]);
        expect(result.current.entries).toHaveLength(2);
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
                retryable: true,
                visible: true,
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
        setVisible: jest.fn().mockResolvedValue(true),
    });

    it("calls retry with the raster's id", async () => {
        const retry = jest.fn().mockResolvedValue(true);
        render(<RasterLayerPanel layers={layers(retry, "tile")} />);
        expect(screen.getByTestId("raster-layer-failed")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(retry).toHaveBeenCalledWith("r1");
    });

    it("offers no Retry when the API refused the file, and shows its reason (r3-D2)", () => {
        const props = layers(jest.fn(), "tile");
        props.entries[0].retryable = false;
        props.entries[0].failed = "Asset is quarantined and cannot be downloaded";
        render(<RasterLayerPanel layers={props} />);
        expect(screen.getByTestId("raster-layer-failed")).toHaveTextContent(/quarantined/);
        expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    });

    it("gives every icon button in a layer row an accessible name (r3-D1)", () => {
        render(<RasterLayerPanel layers={layers(jest.fn(), "tile")} />);
        for (const name of [
            "Zoom to s.tif.cog.tif",
            "Move s.tif.cog.tif up",
            "Move s.tif.cog.tif down",
            "Remove s.tif.cog.tif",
        ]) {
            expect(screen.getByRole("button", { name })).toBeInTheDocument();
        }
        for (const button of screen.getAllByRole("button")) {
            expect(button).toHaveAccessibleName();
        }
        expect(
            screen.getByRole("slider", { name: /Opacity of s.tif.cog.tif/ })
        ).toBeInTheDocument();
    });

    it("offers Hide in place of Remove, with a name, a pressed state and a status message (item 1)", async () => {
        const props = layers(jest.fn(), "tile");
        props.entries[0].failed = null as any;
        render(<RasterLayerPanel layers={props} visibilityToggle />);
        expect(screen.queryByRole("button", { name: /^Remove / })).not.toBeInTheDocument();
        const hide = screen.getByRole("button", { name: "Hide s.tif.cog.tif" });
        expect(hide).toHaveAttribute("aria-pressed", "false");
        await userEvent.click(hide);
        expect(props.setVisible).toHaveBeenCalledWith("r1", false);
        expect(screen.getByTestId("raster-visibility-status")).toHaveTextContent(
            "s.tif.cog.tif hidden"
        );
    });

    it("lists a hidden raster dimmed and labelled, with Show, and Zoom disabled (item 1)", async () => {
        const props = layers(jest.fn(), "tile");
        props.entries[0].failed = null as any;
        props.entries[0].visible = false;
        render(<RasterLayerPanel layers={props} visibilityToggle />);
        expect(screen.getByTestId("raster-hidden-badge")).toHaveTextContent("Hidden");
        expect(screen.getByTestId("raster-layer-row")).toHaveAttribute("data-hidden", "true");
        const show = screen.getByRole("button", { name: "Show s.tif.cog.tif" });
        expect(show).toHaveAttribute("aria-pressed", "true");
        expect(screen.getByRole("button", { name: "Zoom to s.tif.cog.tif" })).toBeDisabled();
        // Move stays available, and the opacity slider keeps its value for when it is shown again.
        expect(screen.getByRole("button", { name: "Move s.tif.cog.tif down" })).toBeInTheDocument();
        await userEvent.click(show);
        expect(props.setVisible).toHaveBeenCalledWith("r1", true);
        expect(screen.getByTestId("raster-visibility-status")).toHaveTextContent(
            "s.tif.cog.tif shown"
        );
    });

    it("is operable from the keyboard (item 1)", async () => {
        const props = layers(jest.fn(), "tile");
        props.entries[0].failed = null as any;
        render(<RasterLayerPanel layers={props} visibilityToggle />);
        screen.getByRole("button", { name: "Hide s.tif.cog.tif" }).focus();
        await userEvent.keyboard("{Enter}");
        expect(props.setVisible).toHaveBeenCalledWith("r1", false);
    });

    it("keeps Remove, and no toggle, where visibilityToggle is off (the Search map)", () => {
        const props = layers(jest.fn(), "tile");
        props.entries[0].failed = null as any;
        render(<RasterLayerPanel layers={props} />);
        expect(screen.getByRole("button", { name: "Remove s.tif.cog.tif" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /^Hide / })).not.toBeInTheDocument();
    });

    it("offers no Retry on a raster that is not drawn from tiles", () => {
        render(<RasterLayerPanel layers={layers(jest.fn(), "placed")} />);
        expect(screen.queryByTestId("raster-layer-retry")).not.toBeInTheDocument();
    });
});
