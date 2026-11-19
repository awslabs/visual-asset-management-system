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
    it("counts each part of a MultiPolygon on its own", () => {
        const split = {
            type: "MultiPolygon",
            coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
        };
        // Two 1 x 2 parts, not one box from -180 to 180.
        expect(footprintArea(split)).toBeCloseTo(4, 9);
        expect(footprintArea({ type: "Feature", geometry: polygon(0, 2, 0, 3) })).toBeCloseTo(6, 9);
        expect(footprintArea(undefined)).toBe(0);
    });
});

describe("pickFootprint", () => {
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
