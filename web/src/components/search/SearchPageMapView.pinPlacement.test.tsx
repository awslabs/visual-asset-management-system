/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view draws each point result's pin with its tip on the result's location, for keyword and
 * natural-language results alike (#1306). The marker's `anchor="bottom"` puts the bottom centre of
 * the pin element on the location; the pin element also carried `translate(-50%, -100%)`, which
 * moved it a second time, so the tip sat about 15 px left of and 32 px above the location.
 *
 * The test reads the rendered marker the way maplibre places it: the anchor, the pin element's size
 * and any transform on it, and the icon's view box, and works out where the icon's tip lands
 * relative to the location. It also holds `PIN_BOX`, which the popup offsets are built from, to the
 * box the rendered pin element covers.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageMapView from "./SearchPageMapView";
import { INITIAL_STATE } from "./SearchPageTypes";
import { PIN_BOX } from "./utils/mapPopupPlacement";

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ fitBounds: () => undefined }));
            return <div data-testid="map">{props.children}</div>;
        }),
        // The marker element maplibre positions, carrying the anchor it was given.
        Marker: (props: any) => (
            <div data-testid="marker" data-anchor={props.anchor ?? "center"}>
                {props.children}
            </div>
        ),
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

// The pin icon's tip in its own units: the end of the curve `c0 5.25 7 13 7 13` from (5, 9).
const ICON_TIP_SEGMENT = "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13";
const ICON_TIP: [number, number] = [12, 22];

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

/** The fraction of an element's own size a CSS translate moves it by, read from inline style. */
const translateFraction = (transform: string): [number, number] => {
    if (!transform || transform === "none") return [0, 0];
    const m = transform.match(/^translate\(\s*(-?[\d.]+)%\s*,\s*(-?[\d.]+)%\s*\)$/);
    if (!m) throw new Error(`unexpected pin transform: ${transform}`);
    return [Number(m[1]) / 100, Number(m[2]) / 100];
};

/** Offset of the marker element's top-left corner from the location, for maplibre's anchor. */
const anchorShift = (anchor: string, width: number, height: number): [number, number] => {
    const x = anchor.includes("left") ? 0 : anchor.includes("right") ? -width : -width / 2;
    const y = anchor.startsWith("top") ? 0 : anchor.startsWith("bottom") ? -height : -height / 2;
    return [x, y];
};

/**
 * The rendered pin of the only marker: the box its element covers and where the icon's tip lands,
 * both relative to the location, in pixels.
 */
const measurePin = () => {
    const marker = screen.getByTestId("marker");
    const pin = marker.firstElementChild as HTMLElement;
    const width = parseFloat(pin.style.width);
    const height = parseFloat(pin.style.height);
    const [fx, fy] = translateFraction(pin.style.transform);
    const [ax, ay] = anchorShift(marker.getAttribute("data-anchor") ?? "center", width, height);
    const left = ax + fx * width;
    const top = ay + fy * height;

    const svg = pin.querySelector<SVGSVGElement>("svg");
    const path = svg?.querySelector<SVGPathElement>("path");
    if (!svg || !path) throw new Error("the marker renders no pin icon");
    expect(path.getAttribute("d")?.startsWith(ICON_TIP_SEGMENT)).toBe(true);
    const [vx, vy, vw, vh] = (svg.getAttribute("viewBox") ?? "").split(/\s+/).map(Number);
    // The icon fills the element: an svg without its own size takes the element's width.
    const svgWidth = Number(svg.getAttribute("width") ?? width);
    const svgHeight = Number(svg.getAttribute("height") ?? (svgWidth * vh) / vw);
    const tip = [
        left + ((ICON_TIP[0] - vx) / vw) * svgWidth,
        top + ((ICON_TIP[1] - vy) / vh) * svgHeight,
    ];
    return { box: { left, right: left + width, top, bottom: top + height }, tip };
};

describe("Map view pin placement (#1306)", () => {
    beforeEach(() => jest.spyOn(console, "log").mockImplementation(() => undefined));
    afterEach(() => jest.restoreAllMocks());

    it.each([
        ["a keyword result", keywordAssetHit],
        ["a natural-language result", nlpAssetHit],
    ])("puts the tip of the pin on the location of %s", (_, hit) => {
        renderMap(hit);
        const { tip } = measurePin();
        expect(tip[0]).toBeCloseTo(0, 5);
        expect(tip[1]).toBeCloseTo(0, 5);
    });

    it.each([
        ["a keyword result", keywordAssetHit],
        ["a natural-language result", nlpAssetHit],
    ])("draws the pin of %s in the box the popup offsets keep clear", (_, hit) => {
        renderMap(hit);
        expect(measurePin().box).toEqual(PIN_BOX);
    });
});
