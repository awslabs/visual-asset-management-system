/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    MAP_MARKER_SIZE_PX,
    PIN_BOX,
    POPUP_PADDING,
    PopupAnchor,
    popupOffsetsFor,
} from "./mapPopupPlacement";

const ANCHORS: PopupAnchor[] = [
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
];

/**
 * The box a popup of `width` x `height` covers, relative to its point, when maplibre places it with
 * `anchor` and `offset`: the anchor names the side or corner of the popup that sits on the point.
 */
const popupBox = (anchor: PopupAnchor, offset: [number, number], width: number, height: number) => {
    const [dx, dy] = offset;
    const left = anchor.includes("left") ? 0 : anchor.includes("right") ? -width : -width / 2;
    const top = anchor.startsWith("top") ? 0 : anchor.startsWith("bottom") ? -height : -height / 2;
    return { left: left + dx, right: left + dx + width, top: top + dy, bottom: top + dy + height };
};

const overlaps = (
    a: { left: number; right: number; top: number; bottom: number },
    b: { left: number; right: number; top: number; bottom: number }
) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

describe("Map view popup placement (#1303)", () => {
    it("draws the pin in the marker-sized square above and to the left of its coordinate", () => {
        expect(PIN_BOX.right - PIN_BOX.left).toBe(MAP_MARKER_SIZE_PX);
        expect(PIN_BOX.bottom - PIN_BOX.top).toBe(MAP_MARKER_SIZE_PX);
        expect(PIN_BOX.bottom).toBeLessThanOrEqual(0);
    });

    it.each(ANCHORS)("keeps a popup anchored %s clear of a point marker's pin", (anchor) => {
        const offsets = popupOffsetsFor("point");
        for (const [width, height] of [
            [300, 120],
            [400, 420],
            [324, 60],
        ]) {
            expect(overlaps(popupBox(anchor, offsets[anchor], width, height), PIN_BOX)).toBe(false);
        }
    });

    it("moves a popup placed below or to the right of a pin by nothing", () => {
        const offsets = popupOffsetsFor("point");
        for (const anchor of ["top", "top-left", "top-right", "left"] as PopupAnchor[]) {
            expect(offsets[anchor]).toEqual([0, 0]);
        }
    });

    it("points a shape's popup at the shape's centre with no offset", () => {
        const offsets = popupOffsetsFor("geojson");
        for (const anchor of ANCHORS) {
            expect(offsets[anchor]).toEqual([0, 0]);
        }
    });

    it("keeps the popup off every edge of the map", () => {
        expect(Object.keys(POPUP_PADDING).sort()).toEqual(["bottom", "left", "right", "top"]);
        for (const side of Object.values(POPUP_PADDING)) {
            expect(side).toBeGreaterThan(0);
        }
    });
});
