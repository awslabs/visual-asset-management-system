/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface RowActionsButtonProps
    extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
    // The button has no visible text, so its accessible name is required.
    "aria-label": string;
    /** Vertical dots (pipeline and workflow cards) or horizontal dots (execution table rows). */
    orientation?: "vertical" | "horizontal";
}

// Dot centres along the long axis of the 16-unit icon box.
const DOT_CENTRES = [3, 8, 13];

/**
 * The three-dot button that opens a row's actions menu. The dots are an inline SVG rather than the
 * U+22EE / U+22EF text glyphs, which render only where an installed font carries them. The icon is
 * 1em square and painted in currentColor, so the button's text size, colour and hover classes size
 * and colour it. Ref and props are forwarded, so it works as the asChild child of a Radix
 * DropdownMenu.Trigger.
 */
const RowActionsButton = React.forwardRef<HTMLButtonElement, RowActionsButtonProps>(
    ({ orientation = "vertical", type = "button", ...props }, ref) => (
        <button ref={ref} type={type} {...props}>
            <svg
                className="block"
                width="1em"
                height="1em"
                viewBox="0 0 16 16"
                fill="currentColor"
                xmlns="http://www.w3.org/2000/svg"
                aria-hidden="true"
            >
                {DOT_CENTRES.map((centre) =>
                    orientation === "vertical" ? (
                        <circle key={centre} cx="8" cy={centre} r="1.5" />
                    ) : (
                        <circle key={centre} cx={centre} cy="8" r="1.5" />
                    )
                )}
            </svg>
        </button>
    )
);

RowActionsButton.displayName = "RowActionsButton";

export default RowActionsButton;
