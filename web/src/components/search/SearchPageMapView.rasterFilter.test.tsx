/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Search map's "Show raster files" filter (#1599): a view filter on the results-title row, on by
 * default, that takes the hits that can be shown as a raster (footprints and popups) and every drawn
 * raster off the map without removing them, and reframes on the visible set. The API still decides each
 * download; the filter holds no permission.
 */
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";

const mockFitBounds = jest.fn();
jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({
                fitBounds: (...args: any[]) => mockFitBounds(...args),
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
        Marker: () => <div data-testid="marker" />,
        Popup: (props: any) => <div data-testid="popup">{props.children}</div>,
        NavigationControl: () => null,
        Source: (props: any) => <div data-testid="footprint" data-id={props.id} />,
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
        return <div data-testid="raster-layers" data-hidden={String(props.hidden)} />;
    },
}));

const ring = (w: number, e: number, s: number, n: number) => ({
    type: "Polygon",
    coordinates: [
        [
            [w, s],
            [e, s],
            [e, n],
            [w, n],
            [w, s],
        ],
    ],
});

const fileHit = (key: string, location: any) => ({
    _id: `db-a#a1#${key}`,
    _source: {
        str_rectype: "file",
        str_databaseid: "db-a",
        str_assetid: "a1",
        str_assetname: "Survey",
        str_key: key,
        str_fileext: key.split(".").pop(),
        num_filesize: 4096,
        MD_: location ? { location: JSON.stringify(location) } : {},
    },
});

const RASTER = fileHit("a1/site.tif", ring(10, 10.1, 49.9, 50));
const COG = fileHit("a1/site.tif.cog.tif", ring(20, 20.1, 39.9, 40));
const LAS = fileHit("a1/cloud.laz", { type: "Point", coordinates: [-122.3, 47.6] });
const GEOJSON = fileHit("a1/zone.geojson", ring(-100, -99, 30, 31));

const renderMap = (hits: any[], total = hits.length) =>
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
                            hits: { total: { value: total, relation: "eq" }, hits },
                            aggregations: {},
                        },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

const toggle = () => screen.getByRole("checkbox", { name: "Show raster files" });

describe("Search map: Show raster files filter", () => {
    beforeEach(() => {
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        mockFitBounds.mockClear();
        mockRasterLayers.mockClear();
    });
    afterEach(() => jest.restoreAllMocks());

    it("is on by default, on the results-title row, with a visible label", () => {
        renderMap([RASTER, LAS]);
        expect(toggle()).toBeChecked();
        const row = screen.getByTestId("map-results-title-row");
        expect(row).toContainElement(toggle());
        expect(row).toHaveTextContent("2 of 2 results with location data");
        expect(row).toHaveTextContent("Show raster files");
        expect(screen.queryByTestId("map-raster-hidden-count")).toBeNull();
        expect(screen.getAllByTestId("footprint")).toHaveLength(1);
        expect(screen.getAllByTestId("marker")).toHaveLength(1);
    });

    it("lays the row out so the toggle sits on the right and may wrap under the title", () => {
        renderMap([RASTER]);
        const row = screen.getByTestId("map-results-title-row");
        expect(row.style.display).toBe("flex");
        expect(row.style.flexWrap).toBe("wrap");
        expect(row.style.justifyContent).toBe("space-between");
    });

    it("off hides the raster hits and says so; the other hits stay", () => {
        renderMap([RASTER, COG, LAS, GEOJSON]);
        expect(screen.getAllByTestId("footprint")).toHaveLength(3);
        fireEvent.click(toggle());
        expect(toggle()).not.toBeChecked();
        expect(screen.getAllByTestId("footprint")).toHaveLength(1);
        expect(screen.getAllByTestId("marker")).toHaveLength(1);
        const row = screen.getByTestId("map-results-title-row");
        expect(row).toHaveTextContent("2 of 4 results with location data");
        expect(screen.getByTestId("map-raster-hidden-count")).toHaveTextContent(
            "(2 raster files hidden)"
        );
    });

    it("says 1 raster file in the singular", () => {
        renderMap([RASTER, LAS]);
        fireEvent.click(toggle());
        expect(screen.getByTestId("map-raster-hidden-count")).toHaveTextContent(
            "(1 raster file hidden)"
        );
    });

    it("on again restores the hits and the full count", () => {
        renderMap([RASTER, COG, LAS]);
        fireEvent.click(toggle());
        fireEvent.click(toggle());
        expect(toggle()).toBeChecked();
        expect(screen.getAllByTestId("footprint")).toHaveLength(2);
        expect(screen.getByTestId("map-results-title-row")).toHaveTextContent(
            "3 of 3 results with location data"
        );
        expect(screen.queryByTestId("map-raster-hidden-count")).toBeNull();
    });

    it("closes the popup of a hit that is being hidden", () => {
        renderMap([RASTER, LAS]);
        fireEvent.click(screen.getByTestId("click-first-polygon"));
        expect(screen.getByTestId("popup")).toBeInTheDocument();
        fireEvent.click(toggle());
        expect(screen.queryByTestId("popup")).toBeNull();
    });

    it("reframes on the visible set whenever the filter changes", () => {
        renderMap([RASTER, LAS]);
        const calls = () => mockFitBounds.mock.calls.length;
        const initial = calls();
        expect(initial).toBeGreaterThan(0);
        const boundsOf = (index: number) => mockFitBounds.mock.calls[index][0];
        const full = boundsOf(initial - 1);
        fireEvent.click(toggle());
        expect(calls()).toBe(initial + 1);
        expect(JSON.stringify(boundsOf(initial))).not.toEqual(JSON.stringify(full));
        fireEvent.click(toggle());
        expect(calls()).toBe(initial + 2);
        expect(JSON.stringify(boundsOf(initial + 1))).toEqual(JSON.stringify(full));
    });

    it("keeps the current view when no hit is left to frame", () => {
        renderMap([RASTER, COG]);
        const before = mockFitBounds.mock.calls.length;
        fireEvent.click(toggle());
        expect(mockFitBounds.mock.calls.length).toBe(before);
        expect(screen.getByTestId("map-results-title-row")).toHaveTextContent(
            "0 of 2 results with location data"
        );
    });

    it("tells the raster layers to hide drawn rasters, and to bring them back", async () => {
        renderMap([RASTER, LAS]);
        fireEvent.click(screen.getByTestId("click-first-polygon"));
        await act(async () => {
            fireEvent.click(await screen.findByRole("button", { name: "Show raster" }));
        });
        const last = () => mockRasterLayers.mock.calls[mockRasterLayers.mock.calls.length - 1][0];
        expect(last().hidden).toBe(false);
        fireEvent.click(toggle());
        expect(last().hidden).toBe(true);
        // Hidden, not removed: the layers stay mounted with their request.
        expect(screen.getByTestId("raster-layers")).toBeInTheDocument();
        expect(last().requests).toHaveLength(1);
        fireEvent.click(toggle());
        expect(last().hidden).toBe(false);
    });

    it("is a focusable switch whose checked state carries its state", () => {
        renderMap([RASTER]);
        const input = toggle();
        input.focus();
        expect(input).toHaveFocus();
        fireEvent.click(input);
        expect(toggle()).not.toBeChecked();
    });

    it("is not offered in the empty state", () => {
        renderMap([fileHit("a1/none.tif", null)], 1);
        expect(screen.queryByRole("checkbox", { name: "Show raster files" })).toBeNull();
        expect(screen.getByText(/No results have valid location data/)).toBeInTheDocument();
    });
});
