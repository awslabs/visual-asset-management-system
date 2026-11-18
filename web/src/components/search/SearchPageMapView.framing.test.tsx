/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view frames every plotted result (D99). The map's instance, which the view's ref exposes,
 * exists only after the map library has loaded (react-map-gl sets it once its asynchronous import of
 * maplibre-gl resolves), so the view frames its results when the map loads and again whenever they
 * change. A natural-language answer is already in hand when the user opens the Map view, so it is
 * framed on load; before D99 it stayed on the first point. A keyword answer, fetched again for the
 * map, arrives after the load, and paging through either answer changes the results the map plots:
 * both are framed too.
 *
 * The mocked map below behaves as react-map-gl does: its ref is null until `loadMap()` gives it an
 * instance, and it calls `onLoad` after that.
 */
import React from "react";
import { act, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";
import { FIT_BOUNDS_OPTIONS } from "./utils/mapBounds";

const mockEaseTo = jest.fn();
const mockMap: { load: () => void } = { load: () => undefined };
const mockMapProps: { last: any } = { last: null };

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            const [instance, setInstance] = ReactActual.useState(null);
            mockMapProps.last = props;
            mockMap.load = () =>
                setInstance({
                    easeTo: mockEaseTo,
                    getCanvas: () => ({ clientWidth: 600, clientHeight: 500 }),
                });
            ReactActual.useImperativeHandle(ref, () => instance, [instance]);
            ReactActual.useEffect(() => {
                if (instance) props.onLoad?.({ type: "load" });
                // eslint-disable-next-line react-hooks/exhaustive-deps
            }, [instance]);
            return <div data-testid="map">{props.children}</div>;
        }),
        Marker: () => <div data-testid="marker" />,
        Popup: () => null,
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

const DETROIT = { lon: -83.05, lat: 42.33 };
const SEATTLE = { lon: -122.33, lat: 47.61 };
const AUSTIN = { lon: -97.74, lat: 30.27 };
const BOSTON = { lon: -71.06, lat: 42.36 };

/** A natural-language asset hit: the asset's location only, and the `_vector` block. */
const nlpAssetHit = (id: string, at: { lon: number; lat: number }) => ({
    _index: "vector",
    _id: `db-a#${id}`,
    _score: 0.8,
    _index_type: "asset",
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: id,
        str_assetname: `Plant ${id}`,
        geo_MD_location: { type: "Point", coordinates: [at.lon, at.lat] },
    },
    _vector: {
        distance: 0.2,
        embeddingModelId: "m",
        sourceModalities: ["genai-metadata"],
        indexedAt: "2026-11-10T00:00:00Z",
        fileClass: "model",
        segmentHits: 0,
        bestSegment: null,
    },
});

/** A keyword asset hit with its location in its metadata. */
const keywordAssetHit = (id: string, at: { lon: number; lat: number }) => ({
    _id: `db-a#${id}`,
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: id,
        str_assetname: `Plant ${id}`,
        MD_: { latitude: String(at.lat), longitude: String(at.lon) },
    },
});

/** A natural-language answer of 11 or more matches, of which `hits` is the page shown. */
const stateWith = (hits: any[], from = 0) =>
    ({
        ...INITIAL_STATE,
        filters: { _rectype: { label: "Assets", value: "asset" } },
        tablePreferences: { pageSize: 2 },
        pagination: { from, size: 2 },
        useMapView: true,
        result: hits.length
            ? { hits: { total: { value: 11, relation: "gte" }, hits }, aggregations: {} }
            : undefined,
    } as any);

const view = (state: any) => (
    <MemoryRouter>
        <SearchPageMapView state={state} dispatch={jest.fn()} />
    </MemoryRouter>
);

/** The camera `easeTo` was last given. */
const lastCamera = () => mockEaseTo.mock.calls[mockEaseTo.mock.calls.length - 1][0];

/** The point sits inside the 600 x 500 view the camera frames, with the padding to spare. */
const expectFramed = (...points: Array<{ lon: number; lat: number }>) => {
    const { center, zoom, duration } = lastCamera();
    expect(duration).toBe(FIT_BOUNDS_OPTIONS.duration);
    const scale = 512 * 2 ** zoom;
    const y = (lat: number) =>
        Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / (2 * Math.PI);
    for (const point of points) {
        let dLon = point.lon - center[0];
        while (dLon > 180) dLon -= 360;
        while (dLon < -180) dLon += 360;
        const px = 300 + (dLon / 360) * scale;
        const py = 250 - (y(point.lat) - y(center[1])) * scale;
        expect(px).toBeGreaterThanOrEqual(FIT_BOUNDS_OPTIONS.padding - 1);
        expect(px).toBeLessThanOrEqual(600 - FIT_BOUNDS_OPTIONS.padding + 1);
        expect(py).toBeGreaterThanOrEqual(FIT_BOUNDS_OPTIONS.padding - 1);
        expect(py).toBeLessThanOrEqual(500 - FIT_BOUNDS_OPTIONS.padding + 1);
    }
};

describe("Map view framing (D99)", () => {
    beforeEach(() => {
        mockEaseTo.mockReset();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    it("frames every point of a natural-language answer already in hand when the map loads", () => {
        render(view(stateWith([nlpAssetHit("det", DETROIT), nlpAssetHit("sea", SEATTLE)])));
        // No map instance yet: nothing to move.
        expect(mockEaseTo).not.toHaveBeenCalled();

        act(() => mockMap.load());

        expect(mockEaseTo).toHaveBeenCalledTimes(1);
        expectFramed(DETROIT, SEATTLE);
    });

    it("frames the next page of a natural-language answer after paging (C1)", () => {
        const { rerender } = render(
            view(stateWith([nlpAssetHit("det", DETROIT), nlpAssetHit("sea", SEATTLE)]))
        );
        act(() => mockMap.load());
        expect(mockEaseTo).toHaveBeenCalledTimes(1);

        // The next page of the answer held: the view gets the next slice, with no new search.
        rerender(view(stateWith([nlpAssetHit("aus", AUSTIN), nlpAssetHit("bos", BOSTON)], 2)));

        expect(mockEaseTo).toHaveBeenCalledTimes(2);
        expectFramed(AUSTIN, BOSTON);

        // And back to the first page.
        rerender(view(stateWith([nlpAssetHit("det", DETROIT), nlpAssetHit("sea", SEATTLE)], 0)));
        expect(mockEaseTo).toHaveBeenCalledTimes(3);
        expectFramed(DETROIT, SEATTLE);
    });

    it("frames a keyword answer that arrives after the map has loaded", () => {
        const { rerender } = render(view(stateWith([])));
        act(() => mockMap.load());
        // Nothing plotted yet: no box to frame.
        expect(mockEaseTo).not.toHaveBeenCalled();

        rerender(
            view(stateWith([keywordAssetHit("det", DETROIT), keywordAssetHit("sea", SEATTLE)]))
        );

        expect(mockEaseTo).toHaveBeenCalledTimes(1);
        expectFramed(DETROIT, SEATTLE);
    });

    it("frames the Pacific, with every hit in view, for a footprint split at 180 beside Bay Area files (UAT A)", () => {
        const fileHit = (id: string, geo: any) => ({
            _index: "file",
            _id: `db#${id}`,
            _score: 1,
            _source: {
                str_rectype: "file",
                str_databaseid: "db",
                str_assetid: "a",
                str_key: `/${id}.tif`,
                str_fileext: "tif",
                geo_MD_location: geo,
            },
        });
        const ring = (w: number, e: number, s: number, n: number) => [
            [
                [w, s],
                [e, s],
                [e, n],
                [w, n],
                [w, s],
            ],
        ];
        const hits = [
            fileHit("bay", { type: "Polygon", coordinates: ring(-123, -122, 37, 38) }),
            fileHit("split", {
                type: "MultiPolygon",
                coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
            }),
        ];
        render(
            view({
                ...INITIAL_STATE,
                filters: { _rectype: { value: "file" } },
                useMapView: true,
                result: { hits: { total: { value: 2, relation: "eq" }, hits }, aggregations: {} },
            } as any)
        );
        act(() => mockMap.load());
        expect(mockEaseTo).toHaveBeenCalledTimes(1);
        // The camera sits in the western Pacific half of the world, not at longitude 0.
        const { center } = lastCamera();
        expect(Math.abs(center[0])).toBeGreaterThan(120);
        expectFramed(
            { lon: -123, lat: 37 },
            { lon: -122, lat: 38 },
            { lon: 179, lat: -1 },
            { lon: -179, lat: 1 },
            { lon: 180, lat: 0 }
        );
    });

    it("draws a flat map, so hits on both sides of the world can all be in view (UAT A)", () => {
        render(view(stateWith([nlpAssetHit("det", DETROIT)])));
        expect(mockMapProps.last.projection).toBe("mercator");
    });

    it("frames nothing when no result on the page has a location", () => {
        render(view(stateWith([{ ...nlpAssetHit("x", DETROIT), _source: { str_assetid: "x" } }])));
        act(() => mockMap.load());
        expect(mockEaseTo).not.toHaveBeenCalled();
    });
});
