/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { footprintArea, footprintFallbackAnchor, pickFootprint } from "./mapPick";

const ring = (w: number, e: number, s: number, n: number) => [
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
    coordinates: ring(w, e, s, n),
});
const item = (id: string, geoJson: any) => ({ id, type: "geojson", geoJson });
const feature = (id: string) => ({ layer: { id: `geojson-fill-${id}` } });

describe("footprintArea", () => {
    const rad = Math.PI / 180;
    const y = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
    const boxArea = (w: number, e: number, s: number, n: number) => (e - w) * rad * (y(n) - y(s));

    it("is the Mercator area of a polygon, each part of a MultiPolygon on its own", () => {
        expect(footprintArea(polygon(0, 2, 0, 3))).toBeCloseTo(boxArea(0, 2, 0, 3), 12);
        const split = {
            type: "MultiPolygon",
            coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
        };
        // Two 1 x 2 degree parts, not one shape from -180 to 180.
        expect(footprintArea(split)).toBeCloseTo(2 * boxArea(0, 1, -1, 1), 12);
        expect(footprintArea({ type: "Feature", geometry: polygon(0, 2, 0, 3) })).toBeCloseTo(
            boxArea(0, 2, 0, 3),
            12
        );
    });

    it("counts a rotated footprint's real area, not its box (r5-D9)", () => {
        // A diamond inside the 0..2 x 0..2 box: half the box's area.
        const diamond = {
            type: "Polygon",
            coordinates: [
                [
                    [1, 0],
                    [2, 1],
                    [1, 2],
                    [0, 1],
                    [1, 0],
                ],
            ],
        };
        const area = footprintArea(diamond);
        expect(area).toBeLessThan(0.55 * boxArea(0, 2, 0, 2));
        expect(area).toBeGreaterThan(0.45 * boxArea(0, 2, 0, 2));
    });

    it("subtracts holes and counts an L shape's area", () => {
        const withHole = {
            type: "Polygon",
            coordinates: [ring(0, 4, 0, 4)[0], ring(1, 3, 1, 3)[0]],
        };
        expect(footprintArea(withHole)).toBeCloseTo(boxArea(0, 4, 0, 4) - boxArea(1, 3, 1, 3), 12);
        // Each part of a MultiPolygon subtracts its own holes.
        const multi = {
            type: "MultiPolygon",
            coordinates: [withHole.coordinates, ring(10, 11, 0, 1)],
        };
        expect(footprintArea(multi)).toBeCloseTo(
            boxArea(0, 4, 0, 4) - boxArea(1, 3, 1, 3) + boxArea(10, 11, 0, 1),
            12
        );
        expect(
            footprintArea({
                type: "GeometryCollection",
                geometries: [withHole, polygon(10, 11, 0, 1)],
            })
        ).toBeCloseTo(footprintArea(multi), 12);
        // A hole larger than its shell does not make a negative area.
        const badHole = {
            type: "Polygon",
            coordinates: [ring(0, 1, 0, 1)[0], ring(0, 4, 0, 4)[0]],
        };
        expect(footprintArea(badHole)).toBe(0);
        const ell = {
            type: "Polygon",
            coordinates: [
                [
                    [0, 0],
                    [3, 0],
                    [3, 1],
                    [1, 1],
                    [1, 3],
                    [0, 3],
                    [0, 0],
                ],
            ],
        };
        expect(footprintArea(ell)).toBeLessThan(0.6 * boxArea(0, 3, 0, 3));
    });

    it("is 0 without polygons and infinite for an unreadable coordinate", () => {
        expect(footprintArea(undefined)).toBe(0);
        expect(footprintArea({ type: "Point", coordinates: [1, 2] })).toBe(0);
        expect(
            footprintArea({
                type: "Polygon",
                coordinates: [
                    [
                        [0, 0],
                        [NaN, 1],
                        [1, 1],
                        [0, 0],
                    ],
                ],
            })
        ).toBe(Infinity);
        // Malformed vertices, rings and coordinates never throw.
        const bad = (coordinates: any, type = "Polygon") => ({ type, coordinates });
        const ok = ring(0, 1, 0, 1)[0];
        for (const geo of [
            bad([[[0, 0], null, [1, 1], [0, 0]]]),
            bad([[[0, 0], undefined, [1, 1], [0, 0]]]),
            bad([[[0, 0], "x", [1, 1], [0, 0]]]),
            bad([[[0, 0], [1], [1, 1], [0, 0]]]),
            bad([undefined]),
            bad([null]),
            bad([ok, undefined]),
            bad([ok, [[0, 0], null, [1, 1]]]),
            bad("not coordinates"),
            bad(undefined),
            bad(undefined, "MultiPolygon"),
            bad("x", "MultiPolygon"),
            bad([undefined, [ok]], "MultiPolygon"),
        ]) {
            expect(() => footprintArea(geo)).not.toThrow();
            expect(footprintArea(geo)).toBe(Infinity);
        }
    });

    it("measures area as drawn on the flat map, so latitude does not skew the comparison (#1729)", () => {
        const equator = footprintArea(polygon(0, 1, 0, 1));
        const north = footprintArea(polygon(0, 1, 70, 71));
        // Equal degree boxes are stretched by the projection towards the poles.
        expect(north).toBeGreaterThan(equator * 2.5);
        expect(north / equator).toBeCloseTo(1 / Math.cos((70.5 * Math.PI) / 180), 1);
    });
});

describe("pickFootprint", () => {
    it("compares a donut by its area without the hole", () => {
        const donut = {
            type: "Polygon",
            coordinates: [ring(0, 4, 0, 4)[0], ring(0.5, 3.5, 0.5, 3.5)[0]],
        };
        // The map reports a donut only for a click on its ring, never in its hole. Its area is 16 - 9 = 7
        // square degrees (about), so a smaller box under the ring part wins and a larger one does not.
        const small = [item("donut", donut), item("small", polygon(0, 2, 0, 0.4))];
        expect(pickFootprint([feature("donut"), feature("small")], small)?.id).toBe("small");
        const big = [item("donut", donut), item("big", polygon(0, 4, 0, 4))];
        expect(pickFootprint([feature("big"), feature("donut")], big)?.id).toBe("donut");
    });

    const big = item("big", polygon(-123, -121, 36, 38));
    const small = item("small", polygon(-122.9, -122.8, 37.1, 37.2));
    const sameA = item("sameA", polygon(-123, -122, 37, 38));
    const sameB = item("sameB", polygon(-123, -122, 37, 38));
    const items = [big, small, sameA, sameB];

    it("opens the smallest of several overlapping footprints, whatever their order", () => {
        expect(pickFootprint([feature("big"), feature("small")], items)).toBe(small);
        expect(pickFootprint([feature("small"), feature("big")], items)).toBe(small);
    });

    it("keeps the topmost of footprints with the same area (a source and its COG)", () => {
        expect(pickFootprint([feature("sameB"), feature("sameA")], items)).toBe(sameB);
        expect(pickFootprint([feature("sameA"), feature("sameB")], items)).toBe(sameA);
    });

    it("ignores features that are not footprint fills, and unknown ids", () => {
        expect(
            pickFootprint(
                [{ layer: { id: "geojson-outline-big" } }, { layer: { id: "x" } }, feature("nope")],
                items
            )
        ).toBeNull();
        expect(pickFootprint(undefined, items)).toBeNull();
        expect(pickFootprint([], items)).toBeNull();
    });

    it("opens a rotated footprint over a larger axis-aligned one whose box it shares (r5-D9)", () => {
        // A diamond with a 2 x 2 box and half that area, over an axis-aligned 1.6 x 1.6 square: the
        // diamond's box is larger than the square, but the diamond itself is smaller.
        const diamond = item("diamond", {
            type: "Polygon",
            coordinates: [
                [
                    [1, 0],
                    [2, 1],
                    [1, 2],
                    [0, 1],
                    [1, 0],
                ],
            ],
        });
        const square = item("square", polygon(0.2, 1.8, 0.2, 1.8));
        expect(pickFootprint([feature("square"), feature("diamond")], [square, diamond])).toBe(
            diamond
        );
    });

    it("never opens a footprint with an unreadable coordinate over a readable one", () => {
        const broken = item("broken", {
            type: "Polygon",
            coordinates: [
                [
                    [0, 0],
                    [NaN, 1],
                    [1, 1],
                    [0, 0],
                ],
            ],
        });
        const square = item("square", polygon(0, 50, 0, 50));
        expect(pickFootprint([feature("broken"), feature("square")], [broken, square])).toBe(
            square
        );
    });

    it("is not stopped by a malformed footprint: a readable overlapping one is still picked", () => {
        const square = item("square", polygon(0, 50, 0, 50));
        for (const coordinates of [
            [[[0, 0], null, [1, 1], [0, 0]]],
            [undefined],
            "not coordinates",
        ]) {
            const broken = item("broken", { type: "Polygon", coordinates });
            expect(() =>
                pickFootprint([feature("broken"), feature("square")], [broken, square])
            ).not.toThrow();
            expect(pickFootprint([feature("broken"), feature("square")], [broken, square])).toBe(
                square
            );
            expect(pickFootprint([feature("square"), feature("broken")], [broken, square])).toBe(
                square
            );
        }
    });

    it("measures a split footprint by its parts, so it beats a large footprint over it", () => {
        const split = item("split", {
            type: "MultiPolygon",
            coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
        });
        const wide = item("wide", polygon(-130, -100, 20, 50));
        expect(pickFootprint([feature("wide"), feature("split")], [wide, split])).toBe(split);
    });
});

describe("footprintFallbackAnchor", () => {
    it("is the centre of the box of a plain polygon", () => {
        expect(footprintFallbackAnchor(polygon(-123, -121, 36, 38))).toEqual([-122, 37]);
    });

    it("lies on a part of a footprint split at 180, not at longitude 0", () => {
        const [lon] = footprintFallbackAnchor({
            type: "MultiPolygon",
            coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
        })!;
        expect(Math.abs(lon)).toBeGreaterThan(178);
    });

    it("lies in the larger arm of an L-shaped footprint's largest part, not its empty corner", () => {
        const l = {
            type: "MultiPolygon",
            coordinates: [ring(0, 10, 0, 1), ring(0, 1, 1, 10)],
        };
        // Both arms have area 10 and 9: the horizontal arm's centre is on the shape.
        expect(footprintFallbackAnchor(l)).toEqual([5, 0.5]);
    });

    it("is null without coordinates", () => {
        expect(footprintFallbackAnchor({ type: "Polygon", coordinates: [] })).toBeNull();
        expect(footprintFallbackAnchor(undefined)).toBeNull();
    });
});
