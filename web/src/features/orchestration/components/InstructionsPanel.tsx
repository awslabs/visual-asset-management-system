/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Z } from "./zLayers";

interface InstructionsPanelProps {
    /** The instruction text. Rendered verbatim, preserving line breaks and indentation. */
    text?: string;
    /** Heading shown above the text (inline mode) or as the trigger label (tooltip mode). */
    title?: string;
    /**
     * Line count above which the text collapses into a hover tooltip instead of rendering inline.
     * A pipeline that documents every metadata key it reads runs to dozens of lines, which would
     * otherwise push the actual form controls off the screen.
     */
    inlineLineLimit?: number;
    /** Character count that also forces tooltip mode — a few very long lines are as bad as many. */
    inlineCharLimit?: number;
}

/** Whether the text is short enough to show inline without dominating the surface. */
export function fitsInline(text: string, lineLimit: number, charLimit: number): boolean {
    if (!text) return true;
    return text.split("\n").length <= lineLimit && text.length <= charLimit;
}

/**
 * Read-only display for a pipeline template's `inputInstructions`.
 *
 * Two behaviors, chosen by length:
 *   - Short: rendered inline so the guidance is visible without interaction — instructions nobody
 *     notices are the same as no instructions.
 *   - Long: collapsed behind a hover/focus affordance, because a template that lists every metadata
 *     key it accepts is long enough to bury the form it is explaining.
 *
 * Text is rendered with `whitespace-pre-wrap`, not as markdown: the field is authored as plain text
 * in a vamsSchema bundle, and metadata documentation relies on line breaks and indentation that a
 * markdown renderer would collapse.
 */
const InstructionsPanel: React.FC<InstructionsPanelProps> = ({
    text,
    title = "Instructions",
    inlineLineLimit = 6,
    inlineCharLimit = 400,
}) => {
    const value = (text || "").trim();
    if (!value) return null;

    // Shared by both modes so the two never drift apart visually.
    const body = <div className="whitespace-pre-wrap break-words font-sans">{value}</div>;

    if (fitsInline(value, inlineLineLimit, inlineCharLimit)) {
        return (
            <div
                className="orch-outline rounded border border-border-default bg-surface-secondary px-3 py-2 text-sm text-text-secondary"
                data-testid="instructions-inline"
            >
                <div className="font-medium text-text-primary mb-1">{title}</div>
                {body}
            </div>
        );
    }

    const lineCount = value.split("\n").length;
    return (
        <Tooltip.Provider delayDuration={150}>
            <Tooltip.Root>
                <Tooltip.Trigger asChild>
                    <button
                        type="button"
                        // Focusable, not hover-only: a keyboard or touch user has to be able to
                        // reach content that is otherwise invisible.
                        aria-label={`${title} (${lineCount} lines)`}
                        data-testid="instructions-tooltip-trigger"
                        className="orch-outline inline-flex items-center gap-1.5 rounded border border-border-default bg-surface-secondary px-3 py-1.5 text-sm text-text-primary hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                        <span
                            aria-hidden="true"
                            className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-text-secondary text-[10px] leading-none text-text-secondary"
                        >
                            i
                        </span>
                        {title}
                        <span className="text-text-secondary">({lineCount} lines)</span>
                    </button>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                    <Tooltip.Content
                        side="bottom"
                        align="start"
                        sideOffset={4}
                        // Portalled to body, so it is a SIBLING of the execute dialog rather than a
                        // child — z-index alone decides the order, and Tailwind's z-50 painted this
                        // underneath the modal.
                        style={{ zIndex: Z.tooltip }}
                        // Scrollable and viewport-bounded: the whole point is not to take over the
                        // screen, so very long instructions scroll inside the tooltip.
                        className="max-h-[60vh] max-w-2xl overflow-y-auto rounded bg-gray-900 px-3 py-2 text-xs text-white shadow-lg dark:bg-gray-700"
                        data-testid="instructions-tooltip-content"
                    >
                        {body}
                        <Tooltip.Arrow className="fill-gray-900 dark:fill-gray-700" />
                    </Tooltip.Content>
                </Tooltip.Portal>
            </Tooltip.Root>
        </Tooltip.Provider>
    );
};

export default InstructionsPanel;
