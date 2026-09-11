/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/**
 * The "open in the visualizer" glyph, shared by the file-search table and the asset file manager so
 * one control does not drift from the other.
 *
 * Cloudscape has no built-in eye icon (its closest, "view-full", renders as a fullscreen-brackets
 * box), so this is supplied through the Button `iconSvg` slot. stroke/fill use currentColor so it
 * inherits the Cloudscape icon-button color and theming.
 */
export const EYE_ICON_SVG = (
    <svg viewBox="0 0 16 16" focusable="false" aria-hidden="true">
        <path
            d="M8 3C4.5 3 1.7 5.1 1 8c.7 2.9 3.5 5 7 5s6.3-2.1 7-5c-.7-2.9-3.5-5-7-5z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinejoin="round"
        />
        <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
);
