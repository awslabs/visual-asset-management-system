/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { TagType } from "../Tag/TagType.interface";

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
    // The API removes surrounding whitespace before applying its own length constraint, so the
    // trimmed length is what decides whether a value is accepted. Measuring the raw length here
    // would let a padded short value through the form and surface as a server rejection instead of
    // an inline message.
    const trimmed = s?.trim();
    if (!trimmed) {
        return "Required field.";
    }

    if (trimmed.length < 4) {
        return "Must be at least 4 characters.";
    }
};

/**
 * The required tag types a form can actually enforce.
 *
 * A tag type marked required only constrains an asset if it HAS tags: with none, the picker has
 * nothing to offer that would satisfy it, so the form could never be submitted. The backend takes the
 * same view — `get_required_tag_types` returns a required type only when tags exist for it in scope —
 * so enforcing an empty one client-side would reject an asset the API accepts.
 *
 * Applies to both scopes: an empty GLOBAL required type and an empty database-scoped one are equally
 * unsatisfiable.
 */
export const enforceableRequiredTagTypes = (allTagTypes?: TagType[]): TagType[] =>
    (allTagTypes || []).filter(
        (tagType) => tagType?.required === "True" && (tagType.tags || []).length > 0
    );

export const validateRequiredTagTypeSelected = (
    selectedTags: string[] | undefined,
    allTagTypes: TagType[]
): string | undefined => {
    // Get required tag types
    const requiredTagTypes: TagType[] = enforceableRequiredTagTypes(allTagTypes);

    // If no tags are selected but there are required tag types, when Next button is pressed, return this
    if ((!selectedTags || !selectedTags.length) && requiredTagTypes.length) {
        return "Required Field.";
    }

    // If tags are selected, determine which are missing
    if (selectedTags?.length) {
        // For each required tag type, check if there is at least one selected tag
        const missingTagTypes: string[] = [];
        requiredTagTypes.forEach((tagType) => {
            const found = (tagType.tags || []).some((tag) => selectedTags.includes(tag));

            // If selected tag is not found in the required tag list, add it to the missing list
            if (!found) {
                missingTagTypes.push(tagType.tagTypeName);
            }
        });

        // If there are missing tags, return error text
        if (missingTagTypes.length) {
            return (
                "A selection from the following tag type(s) required: " + missingTagTypes.join(", ")
            );
        }
    }
};

/**
 * Wizard steps that are declared `isOptional: false` in the upload wizard, by index.
 *
 * Kept here rather than derived from the `steps` array because that array is built inline in the
 * wizard's JSX, after the submit handler that has to consult it.
 */
export const REQUIRED_UPLOAD_STEP_INDEXES = [0, 1];

/**
 * The first non-optional step that has not reported itself valid, or `undefined` when all have.
 *
 * This gates SUBMIT, not navigation. The wizard sets `allowSkipTo`, and its `onNavigate` can only
 * validate the step being LEFT — so from a valid step 0, "Skip to Select Files to upload" jumps over
 * the metadata step even though that step is `isOptional: false`. Gating navigation cannot close that
 * without breaking skip-to, which other flows and a Playwright spec rely on; gating submit closes it
 * while leaving skip-to intact.
 *
 * `!validSteps[i]` treats a missing entry as invalid on purpose: a step that has never reported is not
 * a step that has passed, and an under-sized `validSteps` array is exactly how index 3 read `undefined`
 * before.
 */
export const firstIncompleteRequiredStep = (
    validSteps: readonly (boolean | undefined)[],
    requiredIndexes: readonly number[] = REQUIRED_UPLOAD_STEP_INDEXES
): number | undefined => requiredIndexes.find((i) => !validSteps[i]);
