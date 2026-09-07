/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    enforceableRequiredTagTypes,
    validateRequiredTagTypeSelected,
    firstIncompleteRequiredStep,
} from "./validations";
import { TagType } from "../Tag/TagType.interface";

const tagType = (over: Partial<TagType> & { tagTypeName: string }): TagType =>
    ({ description: "", required: "False", tags: [], ...over } as TagType);

describe("enforceableRequiredTagTypes", () => {
    it("drops a required tag type that has no tags", () => {
        // Nothing can satisfy it, so demanding a selection makes the form unsubmittable.
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Populated", required: "True", tags: ["a"] }),
        ];

        expect(enforceableRequiredTagTypes(types).map((t) => t.tagTypeName)).toEqual(["Populated"]);
    });

    it("drops an empty required tag type in either scope", () => {
        // The rule is about the tag type having tags, not about which scope owns it.
        const types = [
            tagType({ tagTypeName: "GlobalEmpty", required: "True", tags: [] }),
            tagType({
                tagTypeName: "DbEmpty",
                required: "True",
                tags: [],
                databaseId: "factory-db",
            } as any),
        ];

        expect(enforceableRequiredTagTypes(types)).toEqual([]);
    });

    it("tolerates a record with no tags array at all", () => {
        const types = [{ tagTypeName: "NoField", required: "True" } as any];
        expect(enforceableRequiredTagTypes(types)).toEqual([]);
        expect(enforceableRequiredTagTypes(undefined)).toEqual([]);
    });

    it("keeps optional tag types out regardless of their tags", () => {
        expect(enforceableRequiredTagTypes([tagType({ tagTypeName: "Opt", tags: ["a"] })])).toEqual(
            []
        );
    });
});

describe("validateRequiredTagTypeSelected", () => {
    it("accepts no tags when the only required tag type is empty", () => {
        const types = [tagType({ tagTypeName: "Empty", required: "True", tags: [] })];

        expect(validateRequiredTagTypeSelected([], types)).toBeUndefined();
        expect(validateRequiredTagTypeSelected(undefined, types)).toBeUndefined();
    });

    it("accepts an unrelated selection when the only required tag type is empty", () => {
        // The second branch of the validator also has to ignore the empty type; otherwise picking a
        // tag from another type reports it as missing.
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Free", tags: ["blue"] }),
        ];

        expect(validateRequiredTagTypeSelected(["blue"], types)).toBeUndefined();
    });

    it("still demands a selection from a required tag type that has tags", () => {
        const types = [tagType({ tagTypeName: "Line", required: "True", tags: ["press"] })];

        expect(validateRequiredTagTypeSelected([], types)).toBe("Required Field.");
        expect(validateRequiredTagTypeSelected(["other"], types)).toContain("Line");
        expect(validateRequiredTagTypeSelected(["press"], types)).toBeUndefined();
    });

    it("names only the unsatisfied populated tag types", () => {
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Line", required: "True", tags: ["press"] }),
            tagType({ tagTypeName: "Site", required: "True", tags: ["north"] }),
        ];

        const message = validateRequiredTagTypeSelected(["press"], types);
        expect(message).toContain("Site");
        expect(message).not.toContain("Empty");
    });
});

describe("firstIncompleteRequiredStep — a skipped-past required step still blocks submit", () => {
    // The wizard sets `allowSkipTo`, and `onNavigate` validates only the step being LEFT. So from a
    // valid step 0, "Skip to Select Files to upload" bypasses the metadata step even though it is
    // declared `isOptional: false`. Closing that in navigation would break skip-to, which a Playwright
    // spec relies on, so the requirement gates SUBMIT instead (owner question 90, option B).

    it("returns undefined when every required step has reported valid", () => {
        // The arm that matters most: an ordinary complete run must still be able to submit.
        expect(firstIncompleteRequiredStep([true, true, false, false, false])).toBeUndefined();
    });

    it("names the skipped metadata step when it never reported", () => {
        // The defect: step 0 valid, step 1 skipped past and therefore still false.
        expect(firstIncompleteRequiredStep([true, false, false, false, false])).toBe(1);
    });

    it("names the earliest incomplete step, not the last", () => {
        // So the user is sent back to the first thing they need to fix rather than the final one.
        expect(firstIncompleteRequiredStep([false, false, false, false, false])).toBe(0);
    });

    it("treats a missing entry as incomplete", () => {
        // A step that has never reported is not a step that has passed. This is the shape that let
        // index 3 read `undefined` while the array held only three entries.
        expect(firstIncompleteRequiredStep([true])).toBe(1);
        expect(firstIncompleteRequiredStep([])).toBe(0);
    });

    it("ignores the optional steps", () => {
        // Control for the scope of the rule: relationships (2), file selection (3) and review (4) are
        // `isOptional: true`, so leaving them invalid must NOT block submit. Without this the gate
        // would be indistinguishable from "every step must be valid", which would break the documented
        // flow where files are attached later.
        expect(firstIncompleteRequiredStep([true, true, false, false, false])).toBeUndefined();
    });

    it("honours an explicit required-index list", () => {
        expect(firstIncompleteRequiredStep([true, false, true], [0, 2])).toBeUndefined();
        expect(firstIncompleteRequiredStep([true, false, true], [0, 1, 2])).toBe(1);
    });
});
