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
});
