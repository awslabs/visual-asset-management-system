/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The module's stacking order, in one place.
 *
 * Every one of these elements is rendered through a portal to `document.body`, which takes it out of
 * its parent's stacking context — so nesting no longer decides what paints on top, only z-index does.
 * A tooltip opened from inside a dialog is a sibling of that dialog, not a child of it, and a
 * tooltip left at Tailwind's `z-50` therefore paints UNDERNEATH a dialog at 3001. That is exactly
 * how the info-icon and template-instruction tooltips came to be invisible on the execute modal.
 *
 * Values are absolute rather than relative because they have to coexist with the surrounding
 * Cloudscape app, whose fixed TopNavigation sits at 2000.
 */
export const Z = {
    /** The app's fixed Cloudscape TopNavigation (not set here — recorded for ordering). */
    appNav: 2000,
    /** Dialog / drawer scrim. */
    overlay: 3000,
    /** Dialog / drawer panel. */
    modal: 3001,
    /**
     * Tooltips and other transient popovers. Above `modal` because they are commonly opened FROM a
     * dialog, and below `toast` so a failure message is never hidden by a hover.
     */
    tooltip: 3500,
    /** Toast notifications — the top layer, so an error is always reachable. */
    toast: 4000,
} as const;
