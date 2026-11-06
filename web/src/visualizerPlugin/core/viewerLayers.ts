/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Stacking order of what a viewer draws over its scene, lowest first. A viewer with a side panel
 * takes the z-index of each element it positions over the scene from here, by what the element is,
 * and writes no number of its own: two elements given the same value are stacked by document order.
 *
 * DynamicViewer mounts every viewer inside `.visualizer-container-canvases`, whose transform makes it
 * a stacking context and the containing block of a panel's `position: fixed`. These values therefore
 * order one viewer's own elements against each other only. They cannot lift a viewer above the
 * application header, toasts or Cloudscape modals, and the host's wide and fullscreen controls
 * (`.visualizer-footer`, painted after that container) stay above the whole viewer.
 *
 * The canvas is not positioned and stays below every layer. The layers are 10 apart, so an element
 * that must sit between two of them takes a value in between. A panel is a stacking context of its
 * own, so a menu inside it is ordered within the panel and does not use this scale.
 */
export const VIEWER_LAYER = {
    /** Read-only boxes over the scene: the viewer name, the controls hint and the scene stats */
    info: 1000,
    /**
     * What the user works in: the side panel (its tabs, buttons, scene tree and resize handle, and the
     * menus opened inside it) and the button that shows the panel again after it is hidden
     */
    panel: 1010,
    /** Dismissible notices across the top of the viewer, such as files that failed to load */
    notice: 1020,
    /** A cover over the whole viewer: the loading spinner and a message that replaces the scene */
    cover: 1030,
} as const;

export type ViewerLayer = keyof typeof VIEWER_LAYER;
