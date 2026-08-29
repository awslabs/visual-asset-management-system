/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Escape every RegExp metacharacter in `value` so it matches literally.
 *
 * Needed wherever user-typed text becomes part of a pattern. `new RegExp("(")` does not return a
 * pattern that fails to match — it THROWS `SyntaxError: Unterminated group`, and a throw inside a
 * React render blanks the page rather than degrading the feature. `(`, `[`, `*`, `+` and `?` are all
 * ordinary characters to type into a search box.
 *
 * Escaping rather than try/catch is deliberate: every string has a valid escaped form, so the
 * construction cannot throw afterwards and there is no failure mode left to swallow.
 */
export function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export default escapeRegExp;
