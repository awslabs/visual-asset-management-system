/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const validateEntityIdAsYouType = (s?: string): string | undefined => {
    if (!s) {
        return "Required field.";
    }

    if (!s.match(/^[a-z].*/)) {
        return "First character must be a lower case letter.";
    }

    if (s.length < 4) {
        return "Must be at least 4 characters.";
    }

    const valid = /^[a-z][a-z0-9-_]{3,63}$/;

    if (!s.match(valid)) {
        return "Invalid characters detected.";
    }
};

export const validateNonZeroLengthTextAsYouType = (s?: string): string | undefined => {
    if (!s) {
        return "Required field.";
    }

    if (s.length < 4) {
        return "Must be at least 4 characters.";
    }
};