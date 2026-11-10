/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view's popup for a natural-language asset hit (D83). Such a hit carries the asset's
 * location and nothing else of its metadata: `geo_MD_location` and the location keys of `MD_`
 * (location, latitude, lat, longitude, lon, long, any letter case). The map plots it from them, and
 * the popup shows the asset's name, database and link with no empty row, and no metadata list:
 * location keys alone are not the asset's metadata, so a "Metadata Fields (1)" list would misstate it.
 * A keyword hit's MD_ is the asset's metadata and keeps its list.
 */
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ fitBounds: () => undefined }));
            return <div data-testid="map">{props.children}</div>;
        }),
        // A marker is a button that selects its result, as a click on the map does.
        Marker: (props: any) => (
            <button
                type="button"
                data-testid="marker"
                data-lat={props.latitude}
                data-lon={props.longitude}
                onClick={() =>
                    props.onClick({ originalEvent: { stopPropagation: () => undefined } })
                }
            />
        ),
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

const vector = {
    distance: 0.2,
    embeddingModelId: "m",
    sourceModalities: ["genai-metadata"],
    indexedAt: "2026-11-10T00:00:00Z",
    fileClass: "model",
    segmentHits: 0,
    bestSegment: null,
};

/** A natural-language asset hit as `POST /search/nlp` returns it after D83. */
const nlpAssetHit = (id: string, location: Record<string, any>) => ({
    _index: "vector",
    _id: `db-a#${id}`,
    _score: 0.8,
    _index_type: "asset",
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: id,
        str_assetname: `Pump house ${id}`,
        str_assettype: "glb",
        list_tags: [],
        bool_archived: false,
        ...location,
    },
    _vector: vector,
});

const renderMap = (hits: any[]) =>
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
                            hits: { total: { value: hits.length, relation: "gte" }, hits },
                            aggregations: {},
                        },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

const openOnlyMarker = () => {
    const markers = screen.getAllByTestId("marker");
    expect(markers).toHaveLength(1);
    fireEvent.click(markers[0]);
    return screen.getByTestId("popup");
};

/** The popup's label rows a hit can have, and which of them the popup shows. */
const POPUP_LABELS = ["Asset Name", "File", "Shape", "Type", "Size", "Created", "Database"];
const popupLabels = (popup: HTMLElement) =>
    POPUP_LABELS.filter((label) => within(popup).queryByText(label, { exact: true }) !== null);
const popupHasRow = (popup: HTMLElement, label: string) =>
    within(popup).queryByText(label, { exact: true }) !== null;

describe("Map view popup for a natural-language asset hit (D83)", () => {
    beforeEach(() => jest.spyOn(console, "log").mockImplementation(() => undefined));
    afterEach(() => jest.restoreAllMocks());

    it.each([
        [
            "geo_MD_location with MD_ latitude and longitude",
            {
                geo_MD_location: { type: "Point", coordinates: [-77.0, 38.9] },
                MD_: { Latitude: "38.9", Longitude: "-77.0" },
            },
        ],
        [
            "MD_ location as JSON text only",
            { MD_: { location: '{"latitude": 38.9, "longitude": -77.0}' } },
        ],
        ["MD_ location as an object only", { MD_: { location: { lat: 38.9, long: -77.0 } } }],
        ["MD_ lat and lon only", { MD_: { lat: "38.9", lon: "-77.0" } }],
    ])("plots %s and opens a popup with no metadata list and no empty row", (_, location) => {
        const { container } = renderMap([nlpAssetHit("a1", location)]);
        expect(screen.getByText(/1 of 1\+ result/)).toBeInTheDocument();
        const marker = screen.getByTestId("marker");
        expect(Number(marker.getAttribute("data-lat"))).toBeCloseTo(38.9);
        expect(Number(marker.getAttribute("data-lon"))).toBeCloseTo(-77.0);

        const popup = openOnlyMarker();
        expect(popup).toHaveTextContent("Pump house a1");
        expect(popupLabels(popup)).toEqual(["Asset Name", "Database"]);
        // The hit has no description and no tags: neither row is shown empty.
        expect(popupHasRow(popup, "Description")).toBe(false);
        expect(popupHasRow(popup, "Tags")).toBe(false);
        expect(screen.getByRole("link", { name: "db-a" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "View Asset Details" })).toBeInTheDocument();
        // No metadata list: the hit carries the asset's location keys, not its metadata.
        expect(createWrapper(popup).findPopover()).toBeNull();
        expect(popup).not.toHaveTextContent("Metadata Fields");
        expect(popup).not.toHaveTextContent("[object Object]");
    });

    it("plots nothing and opens no popup for a natural-language asset hit without a location", () => {
        renderMap([nlpAssetHit("a2", {})]);
        expect(screen.queryAllByTestId("marker")).toHaveLength(0);
        expect(screen.getByText(/0 of 1\+ result/)).toBeInTheDocument();
        expect(screen.queryByTestId("popup")).toBeNull();
    });

    it("keeps the metadata list of a keyword asset hit, whose MD_ is the asset's metadata", () => {
        const keywordHit = {
            _id: "db-a#a3",
            _source: {
                str_rectype: "asset",
                str_databaseid: "db-a",
                str_assetid: "a3",
                str_assetname: "Pump house a3",
                MD_: { location: '{"latitude": 38.9, "longitude": -77.0}', site: "Plant-7" },
            },
        };
        renderMap([keywordHit]);
        const popup = openOnlyMarker();
        expect(createWrapper(popup).findPopover()).not.toBeNull();
    });
});
