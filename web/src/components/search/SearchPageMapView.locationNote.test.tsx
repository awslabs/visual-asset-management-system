/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * When none of the results has valid location data, the Map view lists the metadata field types it
 * recognises. A GeoTIFF or COG with georeferencing in its header is one of them: its footprint box is
 * its `location` metadata, written by the metadata pipeline when EXTRACT_GEO_LOCATION is on, and Show
 * raster draws the raster from the file's own tags.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
        Marker: () => null,
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

const unlocated = {
    _id: "db#f1",
    _source: {
        str_rectype: "file",
        str_databaseid: "db",
        str_assetid: "a",
        str_assetname: "Asset",
        str_key: "/no-footprint.tif",
        str_fileext: "tif",
    },
};

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

describe("Map view note for results without location data", () => {
    beforeEach(() => jest.spyOn(console, "log").mockImplementation(() => undefined));
    afterEach(() => jest.restoreAllMocks());

    it("lists the GeoTIFF / COG entry beside the existing field types", () => {
        renderMap([unlocated]);
        expect(screen.getByText(/No results have valid location data/)).toBeInTheDocument();
        // The existing entries are still there.
        expect(screen.getByText("location", { selector: "strong" })).toBeInTheDocument();
        expect(screen.getByText("latitude", { selector: "strong" })).toBeInTheDocument();
        const raster = screen.getByTestId("map-note-raster");
        expect(raster).toHaveTextContent("GeoTIFF or COG");
        expect(raster).toHaveTextContent("georeferencing");
        expect(raster).toHaveTextContent("location metadata");
        expect(raster).toHaveTextContent("reprojected to WGS 84");
        expect(raster).toHaveTextContent("EXTRACT_GEO_LOCATION");
        expect(raster).toHaveTextContent("no georeferencing, or with the option off, has no box");
        expect(raster).toHaveTextContent("Show raster");
        expect(raster).toHaveTextContent("not from metadata");
    });

    it("is not shown when the results have location data", () => {
        renderMap([
            {
                ...unlocated,
                _source: {
                    ...unlocated._source,
                    geo_MD_location: { type: "Point", coordinates: [-122.3, 47.6] },
                },
            },
        ]);
        expect(screen.queryByTestId("map-note-raster")).not.toBeInTheDocument();
    });
});
