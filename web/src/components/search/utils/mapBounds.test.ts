/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BOUNDS_PADDING_DEGREES, cameraForBounds, locationDataBounds } from "./mapBounds";

const P = BOUNDS_PADDING_DEGREES;

/** The box as [west, south, east, north], to compare with toBeCloseTo. */
const flat = (bounds: any) => [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]];

const expectBox = (bounds: any, expected: number[]) => {
    expect(bounds).not.toBeNull();
    flat(bounds).forEach((value, i) => expect(value).toBeCloseTo(expected[i], 6));
};

describe("locationDataBounds (D99)", () => {
    it("holds every point, padded on every side", () => {
        expectBox(
            locationDataBounds([
                { type: "point", latitude: 42.33, longitude: -83.05 },
                { type: "point", latitude: 47.61, longitude: -122.33 },
            ]),
            [-122.33 - P, 42.33 - P, -83.05 + P, 47.61 + P]
        );
    });

    it("gives a single point an area to frame", () => {
        expectBox(locationDataBounds([{ type: "point", latitude: 10, longitude: 20 }]), [
            20 - P,
            10 - P,
            20 + P,
            10 + P,
        ]);
    });

    it("holds every vertex of a GeoJSON Feature's polygon", () => {
        const feature = {
            type: "Feature",
            properties: {},
            geometry: {
                type: "Polygon",
                coordinates: [
                    [
                        [1, 1],
                        [3, 1],
                        [3, 4],
                        [1, 1],
                    ],
                ],
            },
        };
        expectBox(locationDataBounds([{ type: "geojson", geoJson: feature }]), [
            1 - P,
            1 - P,
            3 + P,
            4 + P,
        ]);
    });

    it("holds each member of a GeometryCollection, and a point beside it", () => {
        const collection = {
            type: "GeometryCollection",
            geometries: [
                { type: "Point", coordinates: [5, -2] },
                {
                    type: "LineString",
                    coordinates: [
                        [6, 0],
                        [8, 3],
                    ],
                },
            ],
        };
        expectBox(
            locationDataBounds([
                { type: "geojson", geoJson: collection },
                { type: "point", latitude: -5, longitude: 7 },
            ]),
            [5 - P, -5 - P, 8 + P, 3 + P]
        );
    });

    it("is null when no result carries a coordinate", () => {
        expect(locationDataBounds([])).toBeNull();
        expect(
            locationDataBounds([
                { type: "point" },
                { type: "geojson", geoJson: { type: "Polygon", coordinates: [] } },
            ])
        ).toBeNull();
    });

    it("takes the shortest way round for results either side of the antimeridian (UAT A)", () => {
        // A footprint split at 180 (two parts) beside Bay Area footprints: the frame is the Pacific
        // between them, not the world from -180 to 180.
        const split = {
            type: "MultiPolygon",
            coordinates: [
                [
                    [
                        [-180, -1],
                        [-179, -1],
                        [-179, 1],
                        [-180, 1],
                        [-180, -1],
                    ],
                ],
                [
                    [
                        [179, -1],
                        [180, -1],
                        [180, 1],
                        [179, 1],
                        [179, -1],
                    ],
                ],
            ],
        };
        const bay = {
            type: "Polygon",
            coordinates: [
                [
                    [-123, 37],
                    [-122, 37],
                    [-122, 38],
                    [-123, 38],
                    [-123, 37],
                ],
            ],
        };
        const bounds: any = locationDataBounds([
            { type: "geojson", geoJson: bay },
            { type: "geojson", geoJson: split },
        ]);
        expect(bounds[0][0]).toBeCloseTo(179 - P, 6);
        expect(bounds[1][0]).toBeCloseTo(-122 + 360 + P, 6);
        expect(bounds[0][1]).toBeCloseTo(-1 - P, 6);
        expect(bounds[1][1]).toBeCloseTo(38 + P, 6);
    });

    it("spreads over the world when results are on several continents", () => {
        const bounds: any = locationDataBounds([
            { type: "point", latitude: 40, longitude: -100 },
            { type: "point", latitude: 50, longitude: 10 },
            { type: "point", latitude: -30, longitude: 100 },
        ]);
        expect(bounds[0][0]).toBeCloseTo(-100 - P, 6);
        expect(bounds[1][0]).toBeCloseTo(100 + P, 6);
    });
});

describe("cameraForBounds (UAT A)", () => {
    const view = { width: 493, height: 630, padding: 40, maxZoom: 14 };

    it("centres a box and lets its width set the zoom", () => {
        const camera = cameraForBounds(
            [
                [-10, 0],
                [10, 10],
            ],
            view
        )!;
        expect(camera.center[0]).toBeCloseTo(0, 6);
        expect(camera.center[1]).toBeGreaterThan(4.9);
        expect(camera.center[1]).toBeLessThan(5.1);
        // 413 px for 20 degrees: log2(413 / 512 * 18)
        expect(camera.zoom).toBeCloseTo(Math.log2((413 / 512) * 18), 3);
    });

    it("wraps the centre of an antimeridian box into -180..180 and shows all of it", () => {
        const camera = cameraForBounds(
            [
                [179, -1],
                [239, 38],
            ],
            view
        )!;
        expect(camera.center[0]).toBeCloseTo(-151, 6);
        // 60 degrees over 413 px.
        expect(camera.zoom).toBeCloseTo(Math.log2((413 / 512) * 6), 3);
    });

    it("never zooms past maxZoom for a point, nor below the minimum for the world", () => {
        const point = cameraForBounds(
            [
                [20 - P, 10 - P],
                [20 + P, 10 + P],
            ],
            view
        )!;
        expect(point.zoom).toBeLessThanOrEqual(14);
        const world = cameraForBounds(
            [
                [-180, -60],
                [180, 80],
            ],
            view
        )!;
        expect(world.zoom).toBe(0);
    });

    it("is null for a box or view that cannot be framed", () => {
        expect(
            cameraForBounds(
                [
                    [NaN, 0],
                    [1, 1],
                ],
                view
            )
        ).toBeNull();
        expect(
            cameraForBounds(
                [
                    [0, 0],
                    [1, 1],
                ],
                { ...view, width: 0 }
            )
        ).toBeNull();
    });
});
