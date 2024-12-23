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
    if (!s) {
        return "Required field.";
    }

    if (s.length < 4) {
        return "Must be at least 4 characters.";
    }
};

export const validateRequiredTagTypeSelected = (selectedTags: string[] | undefined, allTagTypes: TagType[]): string | undefined => {
    // Get required tag types
    let requiredTagTypes: TagType[] = allTagTypes.filter((tagType) => tagType.required === 'True');

    // If no tags are selected but there are required tag types, when Next button is pressed, return this
    if((!selectedTags || !selectedTags.length) && requiredTagTypes.length) {
        return "Required Field.";
    }

    // If tags are selected, determine which are missing
    if (selectedTags?.length) {
        // For each required tag type, check if there is at least one selected tag
        let missingTagTypes: string[] = []
        requiredTagTypes.forEach((tagType) => {
            const found = tagType.tags.some(tag => selectedTags.includes(tag))

            // If selected tag is not found in the required tag list, add it to the missing list
            if(!found) {
                missingTagTypes.push(tagType.tagTypeName)
            }
        })

        // If there are missing tags, return error text
        if(missingTagTypes.length) {
            return "A selection from the following tag type(s) required: " + missingTagTypes.join(', ')
        }
    }
}
