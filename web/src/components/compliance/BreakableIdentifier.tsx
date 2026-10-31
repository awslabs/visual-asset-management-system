/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

const IDENTIFIER_BREAKS = /(?<=_)|(?<=[a-z0-9])(?=[A-Z])/;

/**
 * A user id (an audit entry's actor) also breaks after each `@` and `.`, the separators of an
 * e-mail-style id: "db-admin@example.com" gives "db-admin@", "example.", "com". Other
 * identifiers do not, since there a `.` is often part of a version or a number ("1.2").
 */
const USER_ID_BREAKS = /(?<=[_@.])|(?<=[a-z0-9])(?=[A-Z])/;

/**
 * The parts of an identifier a line may break between: after each `_`, and before an upper-case
 * letter that follows a lower-case letter or a digit (a camelCase boundary). Spaces and hyphens
 * are break opportunities of their own and need no marking. "schema_unbound_from_asset" gives
 * "schema_", "unbound_", "from_", "asset"; "exceptionSchemaVersion" gives "exception", "Schema",
 * "Version". With `userId`, also after each `@` and `.`.
 */
export const identifierParts = (text: string, userId = false): string[] =>
    text.split(userId ? USER_ID_BREAKS : IDENTIFIER_BREAKS).filter((part) => part.length > 0);

/**
 * An identifier (an audit event type, a detail key or value, a rule name) that wraps only at an
 * `_`, a camelCase boundary, a space or a hyphen, never inside a word. A word-break opportunity
 * (`<wbr>`) marks each `_` and camelCase boundary, and nothing else lets the text break, so a
 * table column is never narrower than the longest part. Text that wraps anywhere instead
 * (`overflow-wrap: anywhere`) lets a wrapping table squeeze the column until a word splits
 * ("complianc / e_check"). With `userId`, the text also breaks after each `@` and `.`.
 */
export const BreakableIdentifier: React.FC<{ text: string; userId?: boolean }> = ({
    text,
    userId = false,
}) => {
    const parts = identifierParts(text, userId);
    return (
        <span data-identifier="">
            {parts.map((part, index) => (
                <React.Fragment key={index}>
                    {index > 0 && <wbr />}
                    {part}
                </React.Fragment>
            ))}
        </span>
    );
};

export default BreakableIdentifier;
