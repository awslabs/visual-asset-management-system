/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BOUNDS_PADDING_DEGREES, locationDataBounds } from "./mapBounds";

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

    describe("longitudes take the shortest way round (UAT A)", () => {
        const rect = (w: number, e: number, s: number, n: number) => [
            [
                [w, s],
                [e, s],
                [e, n],
                [w, n],
                [w, s],
            ],
        ];
        const polygon = (w: number, e: number, s: number, n: number) => ({
            type: "Polygon",
            coordinates: rect(w, e, s, n),
        });
        const split = {
            type: "MultiPolygon",
            coordinates: [rect(-180, -179, -1, 1), rect(179, 180, -1, 1)],
        };
        const geo = (geoJson: any) => ({ type: "geojson" as const, geoJson });
        const point = (longitude: number, latitude: number) => ({
            type: "point" as const,
            latitude,
            longitude,
        });
        const west = (bounds: any) => bounds[0][0];
        const east = (bounds: any) => bounds[1][0];

        it("frames the Pacific for a footprint split at 180 beside Bay Area files", () => {
            const bounds = locationDataBounds([geo(polygon(-123, -122, 37, 38)), geo(split)]);
            expectBox(bounds, [179 - P, -1 - P, -122 + 360 + P, 38 + P]);
        });

        it("frames both sides of 180 for points at 179 and -179", () => {
            const bounds = locationDataBounds([point(179, 0), point(-179, 0)]);
            expect(west(bounds)).toBeCloseTo(179 - P, 6);
            expect(east(bounds)).toBeCloseTo(181 + P, 6);
        });

        it("keeps a split footprint alone as its two narrow parts, not the world", () => {
            const bounds = locationDataBounds([geo(split)]);
            expect(west(bounds)).toBeCloseTo(179 - P, 6);
            expect(east(bounds)).toBeCloseTo(181 + P, 6);
        });

        it("frames the world for one shape whose own vertices span it (a latitude-cropped global raster)", () => {
            const bounds = locationDataBounds([geo(polygon(-180, 180, 10, 20))]);
            expectBox(bounds, [-180 - P, 10 - P, 180 + P, 20 + P]);
        });

        it("keeps all of one wide shape (-170..170)", () => {
            const bounds = locationDataBounds([geo(polygon(-170, 170, 10, 20))]);
            expectBox(bounds, [-170 - P, 10 - P, 170 + P, 20 + P]);
        });

        it("keeps a wide shape and a Bay Area point together", () => {
            const bounds = locationDataBounds([
                geo(polygon(-180, 180, 10, 20)),
                point(-122.4, 37.8),
            ]);
            expectBox(bounds, [-180 - P, 10 - P, 180 + P, 37.8 + P]);
        });

        it("spans the world for points on several continents", () => {
            const bounds = locationDataBounds([point(-100, 40), point(10, 50), point(100, -30)]);
            expect(west(bounds)).toBeCloseTo(-100 - P, 6);
            expect(east(bounds)).toBeCloseTo(100 + P, 6);
        });

        it("crosses 180 when that is the shorter way between far-apart continents", () => {
            // Asia (139) to North America (-123): 98 degrees east across the Pacific, not 262 the other way.
            const bounds = locationDataBounds([point(139, 35), point(-123, 37)]);
            expect(west(bounds)).toBeCloseTo(139 - P, 6);
            expect(east(bounds)).toBeCloseTo(-123 + 360 + P, 6);
        });

        it("frames a single point and one polygon as before", () => {
            expectBox(locationDataBounds([point(20, 10)]), [20 - P, 10 - P, 20 + P, 10 + P]);
            expectBox(locationDataBounds([geo(polygon(-123, -122, 37, 38))]), [
                -123 - P,
                37 - P,
                -122 + P,
                38 + P,
            ]);
        });

        it("keeps polar latitudes as they are and is null for an empty set", () => {
            expectBox(locationDataBounds([point(0, 89), point(10, -89)]), [
                0 - P,
                -89 - P,
                10 + P,
                89 + P,
            ]);
            expect(locationDataBounds([])).toBeNull();
        });

        it("counts each part of a MultiPoint and each member of a GeometryCollection", () => {
            const multi = {
                type: "MultiPoint",
                coordinates: [
                    [179, 0],
                    [-179, 0],
                ],
            };
            expect(west(locationDataBounds([geo(multi)]))).toBeCloseTo(179 - P, 6);
            expect(east(locationDataBounds([geo(multi)]))).toBeCloseTo(181 + P, 6);
        });
    });
});
