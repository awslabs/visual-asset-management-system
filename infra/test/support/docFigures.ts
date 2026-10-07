/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Reads documented figures out of prose, for tests that pin documentation to emitted or derived
 * values (the pattern `test/api/apiStackCeilings.test.ts` established). A pattern that matches
 * nowhere or more than once throws rather than yielding a default, so a reworded sentence fails
 * loudly instead of silently comparing zero to zero.
 */

/** Every capture group of the single match, as numbers, in group order. */
export function documentedFigures(text: string, pattern: RegExp, source: string): number[] {
    const flags = pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`;
    const matches = Array.from(text.matchAll(new RegExp(pattern.source, flags)));
    if (matches.length !== 1) {
        throw new Error(
            `${source}: expected exactly one match for ${pattern}, found ${matches.length}. If the ` +
                `sentence was reworded, update the pattern in the same change — otherwise this guard ` +
                `silently stops protecting the figure it exists to protect.`
        );
    }
    return matches[0].slice(1).map(Number);
}

/** The first capture group of the single match. */
export function documentedFigure(text: string, pattern: RegExp, source: string): number {
    return documentedFigures(text, pattern, source)[0];
}
