/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Search map's "Show raster" action (#1599). A file hit that is a TIFF with a footprint offers
 * it in its popup, and clicking it hands the file to the raster layers below the map. Other hits do
 * not offer it, and the raster code is not loaded until the action is used.
 */
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView, { canShowRaster } from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";
import { shownRasterKey } from "./SearchRasterLayers.keys";

const HIT_FILE = { databaseId: "db-a", assetId: "a1", key: "a1/maps/site.tif" };

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        // A button stands in for a click on the first polygon of the map.
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({
                easeTo: () => undefined,
                getCanvas: () => ({ clientWidth: 600, clientHeight: 400 }),
                getMap: () => ({ fake: true }),
            }));
            ReactActual.useEffect(() => props.onLoad?.(), []);
            return (
                <div data-testid="map">
                    <button
                        type="button"
                        data-testid="click-first-polygon"
                        onClick={() =>
                            props.onClick({
                                features: [{ layer: { id: props.interactiveLayerIds[0] } }],
                            })
                        }
                    />
                    {props.children}
                </div>
            );
        }),
        Marker: () => null,
        Popup: (props: any) => <div data-testid="popup">{props.children}</div>,
        NavigationControl: () => null,
        Source: () => null,
        Layer: () => null,
    };
});
jest.mock("../../common/utils/maplibreWorker", () => ({}));
jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ locationServiceApiUrl: "https://maps.example.com/style" }) },
}));
jest.mock("./SearchPreviewThumbnail/PreviewThumbnailCell", () => ({
    __esModule: true,
    default: () => null,
}));
const mockRasterLayers = jest.fn();
jest.mock("./SearchRasterLayers", () => ({
    __esModule: true,
    default: (props: any) => {
        mockRasterLayers(props);
        return <div data-testid="raster-layers">{props.requests.length}</div>;
    },
}));

const footprint = {
    type: "Polygon",
    coordinates: [
        [
            [10, 50],
            [10.1, 50],
            [10.1, 49.9],
            [10, 49.9],
            [10, 50],
        ],
    ],
};

const fileHit = (key: string, location: any = footprint) => ({
    _id: `db-a#a1#${key}`,
    _source: {
        str_rectype: "file",
        str_databaseid: "db-a",
        str_assetid: "a1",
        str_assetname: "Survey",
        str_key: key,
        str_fileext: key.split(".").pop(),
        num_filesize: 4096,
        MD_: { location: JSON.stringify(location) },
    },
});

const renderMap = (hits: any[]) =>
    render(
        <MemoryRouter>
            <SearchPageMapView
                state={
                    {
                        ...INITIAL_STATE,
                        filters: { _rectype: { label: "Files", value: "file" } },
                        tablePreferences: { pageSize: 25 },
                        useMapView: true,
                        result: {
                            hits: { total: { value: hits.length, relation: "eq" }, hits },
                            aggregations: {},
                        },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

describe("canShowRaster", () => {
    const base = { rectype: "file", type: "geojson", geoJson: footprint };
    it("accepts a TIFF file hit with a footprint, a converted COG included", () => {
        expect(canShowRaster({ ...base, fileKey: "a1/site.tif" })).toBe(true);
        expect(canShowRaster({ ...base, fileKey: "a1/site.TIFF" })).toBe(true);
        expect(canShowRaster({ ...base, fileKey: "a1/site.tif.cog.tif" })).toBe(true);
    });
    it("refuses other files, points and asset hits", () => {
        expect(canShowRaster({ ...base, fileKey: "a1/site.png" })).toBe(false);
        expect(canShowRaster({ ...base, type: "point", fileKey: "a1/site.tif" })).toBe(false);
        expect(canShowRaster({ ...base, rectype: "asset", fileKey: "a1/site.tif" })).toBe(false);
        expect(canShowRaster({ ...base, geoJson: undefined, fileKey: "a1/site.tif" })).toBe(false);
    });
});

describe("Search map Show raster action", () => {
    beforeEach(() => {
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        mockRasterLayers.mockClear();
    });
    afterEach(() => jest.restoreAllMocks());

    it("offers Show raster for a TIFF footprint and hands the file to the raster layers", async () => {
        renderMap([fileHit("a1/maps/site.tif")]);
        expect(screen.queryByTestId("raster-layers")).toBeNull();
        fireEvent.click(screen.getByTestId("click-first-polygon"));
        const button = await screen.findByRole("button", { name: "Show raster" });
        await act(async () => {
            fireEvent.click(button);
        });
        expect(await screen.findByTestId("raster-layers")).toHaveTextContent("1");
        const props = mockRasterLayers.mock.calls[mockRasterLayers.mock.calls.length - 1][0];
        expect(props.requests[0]).toMatchObject({
            seq: 1,
            action: "show",
            file: { databaseId: "db-a", assetId: "a1", key: "a1/maps/site.tif" },
            sizeBytes: 4096,
        });
        expect(props.map).toEqual({ fake: true });
    });

    it("turns into Hide raster once the raster is shown, and asks to hide it on the next click", async () => {
        renderMap([fileHit("a1/maps/site.tif")]);
        fireEvent.click(screen.getByTestId("click-first-polygon"));
        await act(async () => {
            fireEvent.click(await screen.findByRole("button", { name: "Show raster" }));
        });
        await screen.findByTestId("raster-layers");
        const lastProps = () =>
            mockRasterLayers.mock.calls[mockRasterLayers.mock.calls.length - 1][0];
        // Until the layers report the raster as shown, the button still offers Show.
        expect(screen.queryByRole("button", { name: "Hide raster" })).toBeNull();
        await act(async () => {
            lastProps().onShownChange(new Set([shownRasterKey(HIT_FILE)]));
        });
        const hide = await screen.findByRole("button", { name: "Hide raster" });
        expect(screen.queryByRole("button", { name: "Show raster" })).toBeNull();
        await act(async () => {
            fireEvent.click(hide);
        });
        expect(lastProps().requests).toHaveLength(2);
        expect(lastProps().requests[1]).toMatchObject({ seq: 2, action: "hide", file: HIT_FILE });
        // The layers report the raster gone (removed here or from the layer list): Show again.
        await act(async () => {
            lastProps().onShownChange(new Set());
        });
        expect(await screen.findByRole("button", { name: "Show raster" })).toBeInTheDocument();
    });

    it("keeps Show raster for another hit while one raster is shown", async () => {
        renderMap([fileHit("a1/maps/site.tif"), fileHit("a1/maps/other.tif")]);
        fireEvent.click(screen.getAllByTestId("click-first-polygon")[0]);
        await act(async () => {
            fireEvent.click(await screen.findByRole("button", { name: "Show raster" }));
        });
        await screen.findByTestId("raster-layers");
        await act(async () => {
            mockRasterLayers.mock.calls[mockRasterLayers.mock.calls.length - 1][0].onShownChange(
                new Set([shownRasterKey({ ...HIT_FILE, key: "a1/maps/other.tif" })])
            );
        });
        expect(screen.getByRole("button", { name: "Show raster" })).toBeInTheDocument();
    });

    it("does not offer Show raster for a file that is not a TIFF", () => {
        renderMap([fileHit("a1/maps/site.geojson")]);
        fireEvent.click(screen.getByTestId("click-first-polygon"));
        expect(screen.getByTestId("popup")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show raster" })).toBeNull();
    });
});
