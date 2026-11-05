/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Z } from "./zLayers";

/** The tooltip's distance from each viewport edge, in px. */
const VIEWPORT_MARGIN = 8;

interface DisabledReasonTooltipProps {
    /** Why the wrapped control is disabled. When undefined the control is rendered on its own. */
    reason?: string;
    /** One focusable element; it receives the tooltip's trigger props. */
    children: React.ReactElement;
}

/**
 * Shows a disabled control's reason on hover and on keyboard focus, as a Cloudscape `disabledReason`
 * does. Escape dismisses it without moving focus. The control must stay focusable (`aria-disabled`,
 * not the `disabled` attribute) for keyboard users to reach the reason.
 */
export const DisabledReasonTooltip: React.FC<DisabledReasonTooltipProps> = ({
    reason,
    children,
}) => {
    if (!reason) return children;
    return (
        <Tooltip.Provider delayDuration={150}>
            <Tooltip.Root>
                <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
                <Tooltip.Portal>
                    <Tooltip.Content
                        // Above the control (below when there is no room), shifted sideways to stay
                        // inside the viewport. Beside it there is no room on a phone-width screen.
                        side="top"
                        sideOffset={6}
                        collisionPadding={VIEWPORT_MARGIN}
                        // Portalled to body like the menu it can open from, so z-index alone decides
                        // the order; at the same layer the later-mounted tooltip paints on top.
                        style={{
                            zIndex: Z.tooltip,
                            maxWidth: `min(20rem, calc(100vw - ${2 * VIEWPORT_MARGIN}px))`,
                        }}
                        className="rounded bg-gray-900 dark:bg-gray-700 px-3 py-2 text-xs text-white shadow-lg"
                    >
                        {reason}
                        <Tooltip.Arrow className="fill-gray-900 dark:fill-gray-700" />
                    </Tooltip.Content>
                </Tooltip.Portal>
            </Tooltip.Root>
        </Tooltip.Provider>
    );
};

type DisabledReasonButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
    /** When set, the button is disabled, stays in the tab order, and explains itself with this text. */
    disabledReason?: string;
};

/**
 * A button that, while `disabledReason` is set, is greyed, does nothing when activated, and shows the
 * reason on hover and focus. Use it for an action the API refuses on this record, where hiding the
 * button would leave the user guessing why it is missing.
 */
export const DisabledReasonButton: React.FC<DisabledReasonButtonProps> = ({
    disabledReason,
    onClick,
    className = "",
    type = "button",
    ...rest
}) => (
    <DisabledReasonTooltip reason={disabledReason}>
        <button
            type={type}
            {...rest}
            aria-disabled={disabledReason ? true : undefined}
            aria-description={disabledReason}
            onClick={disabledReason ? undefined : onClick}
            className={disabledReason ? `${className} opacity-50 cursor-not-allowed` : className}
        />
    </DisabledReasonTooltip>
);
