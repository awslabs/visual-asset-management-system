/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const tagEditor = {
    keyPlaceholder: "Enter key",
    valuePlaceholder: "Enter value",
    addButton: "Add new tag",
    removeButton: "Remove",
    undoButton: "Undo",
    undoPrompt: "This tag will be removed upon saving changes",
    loading: "Loading tags that are associated with this resource",
    keyHeader: "Key",
    valueHeader: "Value",
    optional: "optional",
    keySuggestion: "Custom tag key",
    valueSuggestion: "Custom tag value",
    emptyTags: "No tags associated with the resource.",
    tooManyKeysSuggestion: "You have more keys than can be displayed",
    tooManyValuesSuggestion: "You have more values than can be displayed",
    keysSuggestionLoading: "Loading key values",
    keysSuggestionError: "Tag keys could not be retrieved",
    valuesSuggestionLoading: "Loading tag values",
    valuesSuggestionError: "Tag values could not be retrieved",
    emptyKeyError: "You must specify a tag key",
    maxKeyCharLengthError: "The maximum number of characters you can use in a tag key is 128.",
    maxValueCharLengthError: "The maximum number of characters you can use in a tag value is 256.",
    duplicateKeyError: "You must specify a unique tag key.",
    invalidKeyError:
        "Invalid key. Keys can only contain alphanumeric characters, spaces and any of the following: _.:/=+@-",
    invalidValueError:
        "Invalid value. Values can only contain alphanumeric characters, spaces and any of the following: _.:/=+@-",
    awsPrefixError: "Cannot start with aws:",
    tagLimit: (availableTags) =>
        `You can add up to ${availableTags} more tag${availableTags > 1 ? "s" : ""}.`,
    tagLimitReached: (tagLimit) => `You have reached the limit of ${tagLimit} tags.`,
    tagLimitExceeded: (tagLimit) => `You have exceeded the limit of ${tagLimit} tags.`,
    enteredKeyLabel: (tag) => `Use "${tag}"`,
    enteredValueLabel: (tag) => `Use "${tag}"`,
};
