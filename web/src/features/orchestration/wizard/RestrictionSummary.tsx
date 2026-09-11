/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import InfoTooltip from "../components/InfoTooltip";
import type { ResolvedRestrictions } from "./resolveRestrictions";
import { summarizeRestrictions } from "./resolveRestrictions";

const ARITY_TEXT: Record<ResolvedRestrictions["arity"], string> = {
    none: "No input files (results-only run)",
    one: "Exactly one input file",
    multi: "One or more input files",
};

/** A pattern chip. Monospace, because these are globs where every character matters. */
const Pattern: React.FC<{ children: React.ReactNode; tone?: "neutral" | "warn" }> = ({
    children,
    tone = "neutral",
}) => (
    <span
        className={
            "inline-block rounded px-1.5 py-0.5 font-mono text-xs " +
            (tone === "warn"
                ? "bg-red-100 text-red-900 dark:bg-red-900/30 dark:text-red-200"
                : "bg-surface-secondary text-text-primary")
        }
    >
        {children}
    </span>
);

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
        <span className="text-text-secondary">{label}:</span>
        <span className="text-text-primary">{children}</span>
    </div>
);

/**
 * What a workflow accepts, resolved down the workflow -> pipeline -> template chain.
 *
 * Two densities. `compact` is a single line for the workflow picker, where the user is scanning and a
 * full breakdown would crowd the dialog. The full form is for the wizard's input step, where the
 * user is about to choose files and needs the actual patterns.
 */
/** The accepted/excluded pattern LISTS, for the compact summary's hover. */
const PatternHelp: React.FC<{ r: ResolvedRestrictions }> = ({ r }) => (
    <>
        <p className="mb-1">
            <strong>Accepted:</strong> {r.allow.length === 0 ? "any file type" : r.allow.join(", ")}
        </p>
        {r.exclude.length > 0 && (
            <p className="mb-1">
                <strong>Excluded:</strong> {r.exclude.join(", ")}
            </p>
        )}
        <p className="mb-1">
            {r.arity === "none"
                ? "Takes no input files."
                : r.arity === "one"
                ? "Takes exactly one input file."
                : "Takes one or more input files."}{" "}
            {r.outputType === "none" ? "Records results only." : "Writes files to an asset."}
        </p>
        {r.allow.length > 0 && (
            <p className="mb-1 text-text-secondary">
                From the{" "}
                {r.source === "workflow" ? "workflow's own filters" : "workflow's pipelines"}.
            </p>
        )}
        {!r.templatesResolved && <p>A step&apos;s template may narrow this further once chosen.</p>}
    </>
);

const RestrictionSummary: React.FC<{
    restrictions: ResolvedRestrictions;
    compact?: boolean;
}> = ({ restrictions: r, compact = false }) => {
    if (compact) {
        // Counts alone ("2 file types") do not tell the user WHICH files to go and find, so the actual
        // patterns are one hover away rather than absent. Kept out of the line itself so the picker
        // stays scannable when several workflows are compared.
        return (
            <p className="text-xs text-text-secondary flex items-center gap-1.5 flex-wrap">
                <span>
                    {summarizeRestrictions(r)}
                    {!r.templatesResolved && " · may narrow once a template is chosen"}
                </span>
                <InfoTooltip
                    label="Which files this workflow accepts"
                    text={<PatternHelp r={r} />}
                />
            </p>
        );
    }

    return (
        <div className="orch-outline rounded-lg border border-border-default bg-surface-container p-3 space-y-2">
            <div className="text-sm font-semibold text-text-primary">
                What this workflow accepts
            </div>

            <Row label="Input files">{ARITY_TEXT[r.arity]}</Row>

            {r.arity !== "none" && (
                <>
                    <Row label="Accepted file types">
                        {r.allow.length === 0 ? (
                            "Any file type"
                        ) : (
                            <span className="inline-flex flex-wrap gap-1">
                                {r.allow.map((p) => (
                                    <Pattern key={p}>{p}</Pattern>
                                ))}
                            </span>
                        )}
                        {r.allow.length > 0 && (
                            <span className="ml-1 text-xs text-text-secondary">
                                (from the {r.source === "workflow" ? "workflow" : "pipelines"})
                            </span>
                        )}
                    </Row>

                    {r.exclude.length > 0 && (
                        <Row label="Excluded">
                            <span className="inline-flex flex-wrap gap-1">
                                {r.exclude.map((p) => (
                                    <Pattern key={p} tone="warn">
                                        {p}
                                    </Pattern>
                                ))}
                            </span>
                        </Row>
                    )}
                </>
            )}

            <Row label="Metadata provided to the steps">
                {r.metadataInputs.length === 0 ? "None" : r.metadataInputs.join(", ")}
            </Row>

            {r.metadataGatedOff.length > 0 && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                    A step uses {r.metadataGatedOff.join(", ")}, but the workflow does not provide
                    it — that step runs without it.
                </p>
            )}

            <Row label="Output">
                {r.outputType === "none"
                    ? "Results only — no files are written to an asset"
                    : "Files and metadata written to an asset"}
            </Row>

            {!r.templatesResolved && (
                <p className="text-xs text-text-secondary">
                    A step&apos;s template can narrow these further; the values above reflect the
                    templates chosen so far.
                </p>
            )}
        </div>
    );
};

export default RestrictionSummary;
