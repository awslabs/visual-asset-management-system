/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as Popover from "@radix-ui/react-popover";
import { Z } from "../components/zLayers";
import type { ResolvedRestrictions } from "./resolveRestrictions";

/** Accepted-pattern chips shown inline before the rest fold into a popover. */
export const MAX_INLINE_PATTERNS = 6;

const ARITY_CHIP: Record<ResolvedRestrictions["arity"], string> = {
    none: "No input files",
    one: "1 input file",
    multi: "1 or more input files",
};

const Chip: React.FC<{ children: React.ReactNode; tone?: "neutral" | "mono" | "warn" }> = ({
    children,
    tone = "neutral",
}) => (
    <span
        className={`inline-block rounded-full px-2 py-0.5 ${
            tone === "warn"
                ? "bg-red-100 text-red-900 dark:bg-red-900/30 dark:text-red-200"
                : "bg-surface-secondary text-text-primary"
        } ${tone === "mono" ? "font-mono" : ""}`}
    >
        {children}
    </span>
);

/**
 * What the workflow accepts, as one row of chips under the dialog title from the Inputs step on.
 * The same facts sit on each row of the Workflow step, so this is the only other place they appear.
 */
const RequirementsStrip: React.FC<{ restrictions: ResolvedRestrictions }> = ({
    restrictions: r,
}) => {
    const takesFiles = r.arity !== "none";
    const shown = r.allow.slice(0, MAX_INLINE_PATTERNS);
    const overflow = r.allow.length - shown.length;
    return (
        <div
            data-testid="requirements-strip"
            className="flex flex-wrap items-center gap-1.5 text-xs text-text-secondary"
        >
            <Chip>{ARITY_CHIP[r.arity]}</Chip>
            {takesFiles &&
                (r.allow.length === 0 ? (
                    <Chip>Any file type</Chip>
                ) : (
                    shown.map((p) => (
                        <Chip key={p} tone="mono">
                            {p}
                        </Chip>
                    ))
                ))}
            {takesFiles && overflow > 0 && (
                <Popover.Root>
                    <Popover.Trigger asChild>
                        <button
                            type="button"
                            aria-label={`${overflow} more accepted file types`}
                            className="rounded-full bg-surface-secondary px-2 py-0.5 text-text-primary hover:bg-surface-hover"
                        >
                            +{overflow}
                        </button>
                    </Popover.Trigger>
                    <Popover.Portal>
                        <Popover.Content
                            align="start"
                            sideOffset={4}
                            // Portalled beside the dialog, so only z-index orders it above the modal.
                            style={{ zIndex: Z.tooltip }}
                            className="orchestration-root orch-outline max-w-sm rounded border border-border-default bg-surface-container p-3 text-xs shadow-lg"
                        >
                            <p className="mb-1.5 font-semibold text-text-primary">
                                Accepted file types
                            </p>
                            <div className="flex flex-wrap gap-1">
                                {r.allow.map((p) => (
                                    <Chip key={p} tone="mono">
                                        {p}
                                    </Chip>
                                ))}
                            </div>
                        </Popover.Content>
                    </Popover.Portal>
                </Popover.Root>
            )}
            {takesFiles &&
                r.exclude.map((p) => (
                    <Chip key={`exclude-${p}`} tone="warn">
                        Excludes {p}
                    </Chip>
                ))}
            <Chip>{r.outputType === "none" ? "Results only" : "Writes to an asset"}</Chip>
            {r.metadataInputs.length > 0 && <Chip>Metadata: {r.metadataInputs.join(", ")}</Chip>}
            {!r.templatesResolved && <span>may narrow once a template is chosen</span>}
        </div>
    );
};

export default RequirementsStrip;
