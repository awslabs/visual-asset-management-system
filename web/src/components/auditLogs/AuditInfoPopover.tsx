/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Button from "@cloudscape-design/components/button";
import Popover from "@cloudscape-design/components/popover";

export interface AuditInfoPopoverProps {
    /** Accessible name of the info button; say what the text is about. */
    ariaLabel: string;
    /** Header of the popover. */
    header?: string;
    /** The explanation. Plain text or simple elements; never record content. */
    children: React.ReactNode;
    testId?: string;
    size?: "small" | "medium" | "large";
}

/**
 * An info icon that opens the longer explanation of a control or a state in a popover. The trigger
 * is a real button, so it takes keyboard focus; Enter or Space opens the popover and Escape closes
 * it and returns focus to the icon. Hints that are not needed while working live here instead of
 * in inline banners.
 */
export const AuditInfoPopover: React.FC<AuditInfoPopoverProps> = ({
    ariaLabel,
    header,
    children,
    testId,
    size = "medium",
}) => (
    <span data-testid={testId}>
        <Popover
            size={size}
            position="bottom"
            triggerType="custom"
            header={header}
            dismissButton
            dismissAriaLabel="Close"
            content={children}
        >
            {/* formAction none: the icon sits inside the search form and must not submit it. */}
            <Button
                variant="inline-icon"
                iconName="status-info"
                ariaLabel={ariaLabel}
                formAction="none"
            />
        </Popover>
    </span>
);

export default AuditInfoPopover;
