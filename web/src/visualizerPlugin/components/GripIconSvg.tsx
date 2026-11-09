/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

// Dot centres: two columns of three, like two vertical ellipses side by side.
const GRIP_COLUMNS = [5, 11];
const GRIP_ROWS = [3, 8, 13];

/**
 * The grip on the handle that resizes the scene tree of the Three.js and Needle USD viewers.
 *
 * Inline SVG rather than a text character, so it draws whatever fonts the browser has. It is 1em
 * square and filled with currentColor, so the font size and colour around it size and colour it.
 */
export const GRIP_ICON_SVG = (
    <svg
        width="1em"
        height="1em"
        viewBox="0 0 16 16"
        focusable="false"
        aria-hidden="true"
        style={{ display: "inline-block", verticalAlign: "-0.125em" }}
    >
        <g fill="currentColor">
            {GRIP_COLUMNS.flatMap((cx) =>
                GRIP_ROWS.map((cy) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.5" />)
            )}
        </g>
    </svg>
);
