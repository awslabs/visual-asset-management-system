/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A click on a footprint opens that file's popup where the user clicked. It used to open at the centre
 * of the box of the whole shape, which is far from the click for a large footprint and, for a footprint
 * split at longitude 180, on the other side of the world (longitude 0). Of several overlapping
 * footprints the smallest opens.
 */
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";

const popupProps: any[] = [];
const clickAt: { fire: (features: string[], lngLat: { lng: number; lat: number }) => void } = {
    fire: () => undefined,
};

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ fitBounds: () => undefined }));
            clickAt.fire = (features, lngLat) =>
                props.onClick({
                    features: features.map((id) => ({ layer: { id: `geojson-fill-${id}` } })),
                    lngLat,
                });
            return <div data-testid="map">{props.children}</div>;
        }),
        Marker: (props: any) => (
            <button
                type="button"
                data-testid="marker"
                onClick={() =>
                    props.onClick({ originalEvent: { stopPropagation: () => undefined } })
                }
            />
        ),
        Popup: (props: any) => {
            popupProps.push(props);
            return <div data-testid="popup">{props.children}</div>;
        },
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

const ring = (w: number, e: number, s: number, n: number) => [
    [
        [w, s],
        [e, s],
        [e, n],
        [w, n],
        [w, s],
    ],
];
const fileHit = (name: string, geo: any) => ({
    _index: "file",
    _id: `db#${name}`,
    _score: 1,
    _source: {
        str_rectype: "file",
        str_databaseid: "db",
        str_assetid: "a",
        str_assetname: "Asset",
        str_key: `/${name}.tif`,
        str_fileext: "tif",
        geo_MD_location: geo,
    },
});

const HITS = [
    fileHit("big", { type: "Polygon", coordinates: ring(-123, -121, 36, 38) }),
    fileHit("small", { type: "Polygon", coordinates: ring(-122.9, -122.8, 37.1, 37.2) }),
    fileHit("split", {
        type: "MultiPolygon",
        coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
    }),
    fileHit("ell", {
        type: "MultiPolygon",
        coordinates: [ring(0, 10, 0, 1), ring(0, 1, 1, 10)],
    }),
];
const ID = (name: string) => `db#${name}`;

const renderMap = () =>
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
                            hits: { total: { value: HITS.length, relation: "eq" }, hits: HITS },
                            aggregations: {},
                        },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

const lastPopup = () => popupProps[popupProps.length - 1];

describe("Map view footprint click", () => {
    beforeEach(() => {
        popupProps.length = 0;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    it("opens the popup at the click, not at the centre of the footprint's box", () => {
        renderMap();
        act(() => clickAt.fire([ID("big")], { lng: -122.7, lat: 36.4 }));
        expect(screen.getByTestId("popup")).toBeInTheDocument();
        expect(lastPopup().longitude).toBeCloseTo(-122.7, 9);
        expect(lastPopup().latitude).toBeCloseTo(36.4, 9);
    });

    it("opens a footprint split at 180 at the click, not at longitude 0", () => {
        renderMap();
        act(() => clickAt.fire([ID("split")], { lng: 179.4, lat: 0.3 }));
        expect(lastPopup().longitude).toBeCloseTo(179.4, 9);
        expect(lastPopup().latitude).toBeCloseTo(0.3, 9);
    });

    it("opens an L-shaped footprint at the click, in its empty corner's neighbour arm", () => {
        renderMap();
        act(() => clickAt.fire([ID("ell")], { lng: 0.5, lat: 8 }));
        expect(lastPopup().longitude).toBeCloseTo(0.5, 9);
        expect(lastPopup().latitude).toBeCloseTo(8, 9);
    });

    it("opens the smallest of overlapping footprints, at the click", () => {
        renderMap();
        // The big footprint is listed first (topmost under MapLibre's order), the small one beneath it.
        act(() => clickAt.fire([ID("big"), ID("small")], { lng: -122.85, lat: 37.15 }));
        expect(screen.getByText("/small.tif")).toBeInTheDocument();
        expect(lastPopup().longitude).toBeCloseTo(-122.85, 9);
    });

    it("moves the popup to the next click and ignores a click on no footprint", () => {
        renderMap();
        act(() => clickAt.fire([ID("big")], { lng: -122.7, lat: 36.4 }));
        act(() => clickAt.fire([ID("big")], { lng: -121.3, lat: 37.6 }));
        expect(lastPopup().longitude).toBeCloseTo(-121.3, 9);
        const count = popupProps.length;
        act(() => clickAt.fire([], { lng: 10, lat: 10 }));
        expect(popupProps.length).toBe(count);
        expect(screen.getByTestId("popup")).toBeInTheDocument();
    });

    it("keeps a point marker's popup on the marker", () => {
        const point = {
            _id: "db#pt",
            _source: {
                str_rectype: "asset",
                str_databaseid: "db",
                str_assetid: "p",
                str_assetname: "Pump",
                MD_: { location: '{"latitude": 47.6, "longitude": -122.3}' },
            },
        };
        render(
            <MemoryRouter>
                <SearchPageMapView
                    state={
                        {
                            ...INITIAL_STATE,
                            filters: { _rectype: { label: "Assets", value: "asset" } },
                            tablePreferences: { pageSize: 25 },
                            useMapView: true,
                            result: {
                                hits: { total: { value: 1, relation: "eq" }, hits: [point] },
                                aggregations: {},
                            },
                        } as any
                    }
                    dispatch={jest.fn()}
                />
            </MemoryRouter>
        );
        fireEvent.click(screen.getByTestId("marker"));
        expect(lastPopup().longitude).toBeCloseTo(-122.3, 9);
        expect(lastPopup().latitude).toBeCloseTo(47.6, 9);
    });
});
