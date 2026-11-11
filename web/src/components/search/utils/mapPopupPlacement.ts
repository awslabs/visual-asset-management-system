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
 * Where a point marker's pin is drawn. The marker's `anchor="bottom"` puts the bottom centre of its
 * element on the coordinate, and the pin inside it is moved up by its height and left by half its
 * width once more, so the pin fills the square whose bottom-right corner is one pin height above
 * the coordinate.
 */
export const PIN_BOX: PinBox = {
    left: -MAP_MARKER_SIZE_PX,
    right: 0,
    top: -2 * MAP_MARKER_SIZE_PX,
    bottom: -MAP_MARKER_SIZE_PX,
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

// A popup above the point ends at the pin's top; one to the left of the point ends at the pin's left
// edge. A popup below the point, or to its right, does not reach the pin.
const MARKER_OFFSETS: PopupOffsets = {
    ...NO_OFFSETS,
    bottom: [0, PIN_BOX.top],
    "bottom-left": [0, PIN_BOX.top],
    "bottom-right": [0, PIN_BOX.top],
    right: [PIN_BOX.left, 0],
};

/**
 * The popup offsets for a result: clear of the pin for a point marker, none for a shape, whose
 * popup points at the centre of the shape's bounds.
 */
export function popupOffsetsFor(resultType: "point" | "geojson" | string): PopupOffsets {
    return resultType === "point" ? MARKER_OFFSETS : NO_OFFSETS;
}
