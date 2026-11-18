/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view opens a result's popup with no fixed anchor, so the map places it on the side of the
 * point where it fits inside the map, with the padding and the pin offsets of
 * `utils/mapPopupPlacement.ts` (#1303). A fixed `anchor="top"` centred it under the point and
 * clipped it near the map's edges. The same popup serves keyword and natural-language results.
 */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";
import { POPUP_PADDING, popupOffsetsFor } from "./utils/mapPopupPlacement";

const popupProps: any[] = [];

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ fitBounds: () => undefined }));
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

const keywordAssetHit = {
    _id: "db-a#a1",
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: "a1",
        str_assetname: "Pump house a1",
        MD_: { location: '{"latitude": 47.6, "longitude": -122.3}' },
    },
};

const nlpAssetHit = {
    _index: "vector",
    _id: "db-a#a2",
    _score: 0.8,
    _index_type: "asset",
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: "a2",
        str_assetname: "Pump house a2",
        geo_MD_location: { type: "Point", coordinates: [-117.4, 47.7] },
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
};

const renderMap = (hit: any) =>
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
                            hits: { total: { value: 1, relation: "eq" }, hits: [hit] },
                            aggregations: {},
                        },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

describe("Map view popup placement (#1303)", () => {
    beforeEach(() => {
        popupProps.length = 0;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    it.each([
        ["a keyword result", keywordAssetHit],
        ["a natural-language result", nlpAssetHit],
    ])("lets the map choose the side of the popup for %s", (_, hit) => {
        renderMap(hit);
        fireEvent.click(screen.getByTestId("marker"));
        expect(screen.getByTestId("popup")).toBeInTheDocument();
        const props = popupProps[popupProps.length - 1];
        expect(props.anchor).toBeUndefined();
        expect(props.padding).toEqual(POPUP_PADDING);
        expect(props.offset).toEqual(popupOffsetsFor("point"));
    });
});
