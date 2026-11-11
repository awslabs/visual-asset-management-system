/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where the search Map view opens a result's popup (#1303).
 *
 * The popup is given no fixed anchor, so maplibre chooses the side of the result's point on which
 * the rendered popup fits inside the map: below the point near the map's top edge, above it near
 * the bottom edge, to its right near the left edge, to its left near the right edge, and above it
 * otherwise. maplibre measures the popup as rendered and chooses again on every pan and zoom. A
 * fixed `anchor="top"` centred the popup under the point wherever the point was, so near an edge
 * part of it lay outside the map and was cut off.
 *
 * `POPUP_PADDING` keeps the popup that far from the map's edges. The offsets move a popup placed
 * above or to the left of a point marker clear of the pin, so the pin stays visible and clickable.
 */

/** Width and height of a point marker's pin, in pixels. */
export const MAP_MARKER_SIZE_PX = 30;

/** Distance the popup keeps from each edge of the map, in pixels. */
export const POPUP_PADDING = { top: 10, right: 10, bottom: 10, left: 10 };

/** maplibre's popup anchors: the side or corner of the popup that sits on the point. */
export type PopupAnchor =
    | "center"
    | "top"
    | "bottom"
    | "left"
    | "right"
    | "top-left"
    | "top-right"
    | "bottom-left"
    | "bottom-right";

export type PopupOffsets = Record<PopupAnchor, [number, number]>;

/** Box, relative to the marker's coordinate, in which the pin is drawn: x and y grow right and down. */
export interface PinBox {
    left: number;
    right: number;
    top: number;
    bottom: number;
}

/**
 * The pin icon, drawn in a 24-unit square. Its tip is the point (12, 22): the end of the curve
 * `c0 5.25 7 13 7 13` that starts at (5, 9).
 */
export const MAP_PIN_PATH =
    "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z";

/**
 * The icon's view box: the 24-unit square moved down by the 2 units under the tip, so the tip is the
 * bottom centre of the pin element (#1306).
 */
export const MAP_PIN_VIEW_BOX = "0 -2 24 24";

/**
 * Where a point marker's pin is drawn. The marker's `anchor="bottom"` puts the bottom centre of the
 * pin element on the coordinate and nothing moves it again, so the element is the marker-sized
 * square centred above the coordinate and the tip of the icon (`MAP_PIN_VIEW_BOX`) is on the
 * coordinate. An inline `translate(-50%, -100%)` on the element moved the pin a second time, by
 * half its width and its full height (#1306).
 */
export const PIN_BOX: PinBox = {
    left: -MAP_MARKER_SIZE_PX / 2,
    right: MAP_MARKER_SIZE_PX / 2,
    top: -MAP_MARKER_SIZE_PX,
    bottom: 0,
};

const NO_OFFSETS: PopupOffsets = {
    center: [0, 0],
    top: [0, 0],
    bottom: [0, 0],
    left: [0, 0],
    right: [0, 0],
    "top-left": [0, 0],
    "top-right": [0, 0],
    "bottom-left": [0, 0],
    "bottom-right": [0, 0],
};

// Each popup ends at the side of the pin it is placed on: one above the point at the pin's top, one
// below it at the pin's bottom, one to the left at the pin's left edge and one to the right at the
// pin's right edge. Derived from PIN_BOX, so they follow the pin.
const MARKER_OFFSETS: PopupOffsets = {
    center: [0, 0],
    top: [0, PIN_BOX.bottom],
    "top-left": [0, PIN_BOX.bottom],
    "top-right": [0, PIN_BOX.bottom],
    bottom: [0, PIN_BOX.top],
    "bottom-left": [0, PIN_BOX.top],
    "bottom-right": [0, PIN_BOX.top],
    left: [PIN_BOX.right, 0],
    right: [PIN_BOX.left, 0],
};

/**
 * The popup offsets for a result: clear of the pin for a point marker, none for a shape, whose
 * popup points at the centre of the shape's bounds.
 */
export function popupOffsetsFor(resultType: "point" | "geojson" | string): PopupOffsets {
    return resultType === "point" ? MARKER_OFFSETS : NO_OFFSETS;
}
