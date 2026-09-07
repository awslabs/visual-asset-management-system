/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    MAX_GEOJSON_NESTING_DEPTH,
    MAX_GEOJSON_POSITIONS,
    countGeoJsonPositions,
    geoJsonNestingDepth,
    parseCoordinate,
    parseLatLon,
} from "./geoSearch";

describe("the bounds mirror backend/backend/models/search.py", () => {
    it("matches GeoPointModel's declared ranges and MAX_GEOJSON_POSITIONS", () => {
        // If the backend changes these, this test is the reminder to change them here too.
        expect([LATITUDE_MIN, LATITUDE_MAX]).toEqual([-90, 90]);
        expect([LONGITUDE_MIN, LONGITUDE_MAX]).toEqual([-180, 180]);
        expect(MAX_GEOJSON_POSITIONS).toBe(100000);
    });

    it("matches MAX_GEOJSON_NESTING_DEPTH in backend/backend/models/metadata.py", () => {
        // Shared by the search geoJson filter and the geojson metadata value type.
        expect(MAX_GEOJSON_NESTING_DEPTH).toBe(32);
    });
});

describe("parseCoordinate", () => {
    it("accepts a plain decimal, with or without surrounding space", () => {
        expect(parseCoordinate("47.6062")).toBe(47.6062);
        expect(parseCoordinate("  -122.3321 ")).toBe(-122.3321);
        expect(parseCoordinate("0")).toBe(0);
    });

    it("rejects trailing garbage that parseFloat would silently truncate", () => {
        // The whole reason for not using parseFloat: it returns 47.6 for both of these, so a slipped
        // keystroke becomes a plausible-looking coordinate with no error anywhere.
        expect(parseFloat("47.6abc")).toBe(47.6); // documents the behaviour being avoided
        expect(parseCoordinate("47.6abc")).toBeNull();
        expect(parseCoordinate("47.6.2")).toBeNull();
        expect(parseCoordinate("--5")).toBeNull();
    });

    it("rejects empty, whitespace-only, and non-finite input", () => {
        expect(parseCoordinate("")).toBeNull();
        expect(parseCoordinate("   ")).toBeNull();
        expect(parseCoordinate("Infinity")).toBeNull();
        expect(parseCoordinate("NaN")).toBeNull();
    });
});

describe("parseLatLon", () => {
    it("returns the pair when both values are in range", () => {
        expect(parseLatLon("47.6062", "-122.3321")).toEqual({
            point: { lat: 47.6062, lon: -122.3321 },
        });
    });

    it("accepts the exact bounds", () => {
        expect(parseLatLon("-90", "-180").point).toEqual({ lat: -90, lon: -180 });
        expect(parseLatLon("90", "180").point).toEqual({ lat: 90, lon: 180 });
    });

    it("names latitude when latitude is out of range", () => {
        const result = parseLatLon("91", "0");
        expect(result.point).toBeUndefined();
        expect(result.error).toBe("Latitude must be between -90 and 90.");
    });

    it("names longitude when longitude is out of range", () => {
        // The recorded failure scenario: a slipped decimal point in the longitude field.
        const result = parseLatLon("47.6062", "-1223321");
        expect(result.point).toBeUndefined();
        expect(result.error).toBe("Longitude must be between -180 and 180.");
    });

    it("reports a missing value as required rather than as unparseable", () => {
        expect(parseLatLon("", "-122.3").error).toBe("Latitude and longitude are required.");
        expect(parseLatLon("47.6", "  ").error).toBe("Latitude and longitude are required.");
    });

    it("reports unparseable text against the field it came from", () => {
        expect(parseLatLon("47.6abc", "-122.3").error).toBe("Latitude must be a number.");
        expect(parseLatLon("47.6", "-122.3abc").error).toBe("Longitude must be a number.");
    });

    it("prefixes a bounding-box corner label so the user knows which box to fix", () => {
        expect(parseLatLon("999", "0", "Top-left").error).toBe(
            "Top-left latitude must be between -90 and 90."
        );
        expect(parseLatLon("0", "999", "Bottom-right").error).toBe(
            "Bottom-right longitude must be between -180 and 180."
        );
        expect(parseLatLon("", "", "Top-left").error).toBe(
            "Top-left latitude and longitude are required."
        );
    });
});

describe("countGeoJsonPositions", () => {
    it("counts one position for a Point", () => {
        expect(countGeoJsonPositions({ type: "Point", coordinates: [1, 2] })).toBe(1);
    });

    it("counts every vertex of a Polygon ring", () => {
        const polygon = {
            type: "Polygon",
            coordinates: [
                [
                    [0, 0],
                    [1, 0],
                    [1, 1],
                    [0, 0],
                ],
            ],
        };
        expect(countGeoJsonPositions(polygon)).toBe(4);
    });

    it("descends through features, geometry and geometries", () => {
        const collection = {
            type: "FeatureCollection",
            features: [
                { type: "Feature", geometry: { type: "Point", coordinates: [0, 0] } },
                {
                    type: "Feature",
                    geometry: {
                        type: "GeometryCollection",
                        geometries: [
                            { type: "Point", coordinates: [1, 1] },
                            { type: "Point", coordinates: [2, 2] },
                        ],
                    },
                },
            ],
        };
        expect(countGeoJsonPositions(collection)).toBe(3);
    });

    it("ignores keys the backend does not descend into", () => {
        // Properties can hold arbitrary arrays; counting them would reject a legitimate shape.
        expect(
            countGeoJsonPositions({
                type: "Feature",
                properties: {
                    samples: [
                        [1, 2],
                        [3, 4],
                        [5, 6],
                    ],
                },
                geometry: { type: "Point", coordinates: [0, 0] },
            })
        ).toBe(1);
    });

    it("returns 0 for a value with no coordinates at all", () => {
        expect(countGeoJsonPositions({ type: "Point" })).toBe(0);
        expect(countGeoJsonPositions(null)).toBe(0);
        expect(countGeoJsonPositions("nonsense")).toBe(0);
    });

    it("can exceed the cap, so the cap is reachable", () => {
        // Positive control for the panel's cap check: without a shape that trips it, the check could
        // be unreachable and the test above would still pass.
        const ring = Array.from({ length: MAX_GEOJSON_POSITIONS + 1 }, (_unused, i) => [0, i]);
        expect(countGeoJsonPositions({ type: "Polygon", coordinates: [ring] })).toBe(
            MAX_GEOJSON_POSITIONS + 1
        );
    });
});

const nestedCollection = (levels: number): any => {
    let geometry: any = { type: "Point", coordinates: [1, 2] };
    for (let i = 1; i < levels; i += 1) {
        geometry = { type: "GeometryCollection", geometries: [geometry] };
    }
    return geometry;
};

describe("geoJsonNestingDepth", () => {
    it("counts one level for a bare geometry", () => {
        expect(geoJsonNestingDepth({ type: "Point", coordinates: [1, 2] })).toBe(1);
    });

    it("counts one level per GeometryCollection", () => {
        expect(geoJsonNestingDepth(nestedCollection(2))).toBe(2);
        expect(geoJsonNestingDepth(nestedCollection(5))).toBe(5);
    });

    it("unwraps Feature and FeatureCollection without counting them", () => {
        // The backend starts its depth count at the geometry, so the wrapper is not a level.
        expect(
            geoJsonNestingDepth({
                type: "Feature",
                geometry: { type: "Point", coordinates: [0, 0] },
            })
        ).toBe(1);
        expect(
            geoJsonNestingDepth({
                type: "FeatureCollection",
                features: [
                    { type: "Feature", geometry: nestedCollection(3) },
                    { type: "Feature", geometry: { type: "Point", coordinates: [0, 0] } },
                ],
            })
        ).toBe(3);
    });

    it("accepts nesting at the bound and reports past it one level later", () => {
        expect(geoJsonNestingDepth(nestedCollection(MAX_GEOJSON_NESTING_DEPTH))).toBe(
            MAX_GEOJSON_NESTING_DEPTH
        );
        expect(
            geoJsonNestingDepth(nestedCollection(MAX_GEOJSON_NESTING_DEPTH + 1))
        ).toBeGreaterThan(MAX_GEOJSON_NESTING_DEPTH);
    });

    it("measures a value deep enough to overflow a recursive walk", () => {
        // The whole point of the bound: this value used to raise "Maximum call stack size
        // exceeded" out of countGeoJsonPositions before anything could report a limit.
        expect(geoJsonNestingDepth(nestedCollection(20000))).toBe(MAX_GEOJSON_NESTING_DEPTH + 1);
        expect(countGeoJsonPositions(nestedCollection(20000))).toBe(1);
    });

    it("returns 0 for a value that carries no geometry", () => {
        expect(geoJsonNestingDepth(null)).toBe(0);
        expect(geoJsonNestingDepth("nonsense")).toBe(0);
        expect(geoJsonNestingDepth({ type: "Feature" })).toBe(0);
    });
});
