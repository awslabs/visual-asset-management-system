/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Z } from "./zLayers";

interface InfoTooltipProps {
    /** The explanatory text shown on hover/focus. */
    text: React.ReactNode;
    /** Accessible label for the trigger button. */
    label?: string;
}

/**
 * A small "(i)" info icon that reveals an explanation on hover/focus. Used next to form field
 * labels to explain what each setting means, matching the "add info icons to hover over" UX ask.
 */
const InfoTooltip: React.FC<InfoTooltipProps> = ({ text, label = "More information" }) => (
    <Tooltip.Provider delayDuration={150}>
        <Tooltip.Root>
            <Tooltip.Trigger asChild>
                <button
                    type="button"
                    aria-label={label}
                    className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-text-secondary text-text-secondary text-[10px] leading-none align-middle hover:bg-surface-secondary focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                    i
                </button>
            </Tooltip.Trigger>
            <Tooltip.Portal>
                <Tooltip.Content
                    side="top"
                    align="start"
                    sideOffset={4}
                    // Portalled to body, so it is a SIBLING of any dialog it was opened from rather
                    // than a child — z-index alone decides the order, and Tailwind's z-50 painted
                    // this underneath the execute modal.
                    style={{ zIndex: Z.tooltip }}
                    className="max-w-xs rounded bg-gray-900 dark:bg-gray-700 px-3 py-2 text-xs text-white shadow-lg"
                >
                    {text}
                    <Tooltip.Arrow className="fill-gray-900 dark:fill-gray-700" />
                </Tooltip.Content>
            </Tooltip.Portal>
        </Tooltip.Root>
    </Tooltip.Provider>
);

export default InfoTooltip;
